"""Local MinIO/SQS worker that ingests CSVs and starts n8n Workflow B."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence
from uuid import UUID

from backend.app.ingestion import StreamingCsvIngestionService
from backend.app.schemas import CsvRow
from backend.app.webhooks import build_signed_webhook_request

QUEUE_NAME = os.getenv("EVENT_QUEUE_NAME", "loan-eligibility-events")
SQS_ENDPOINT = os.getenv("LOCALSTACK_ENDPOINT_URL", "http://localstack:4566")
S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
S3_BUCKET = os.getenv("S3_BUCKET", "loan-uploads")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
DATABASE_DSN = os.getenv(
    "DATABASE_DSN",
    "postgresql://loan_engine:loan_engine_local@postgres:5432/loan_engine",
)
N8N_URL = os.getenv("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/loan-ingestion-completed")
N8N_SECRET = os.getenv("N8N_WEBHOOK_SECRET", "")
INGESTION_BATCH_SIZE = int(os.getenv("INGESTION_BATCH_SIZE", "500"))


def _sqs() -> Any:
    import boto3

    return boto3.client(
        "sqs",
        endpoint_url=SQS_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _s3() -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY", "minioadmin"),
    )


def _queue_url() -> str:
    return _sqs().get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]


def _batch_id(key: str) -> UUID:
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "uploads":
        raise ValueError(f"object key is not an upload: {key}")
    return UUID(parts[1])


class LocalPostgresIngestionRepository:
    """Adapter that lets the local worker use the shared ingestion service."""

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(DATABASE_DSN)

    def mark_processing(self, upload_batch_id: UUID) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE upload_batches SET status = 'processing' WHERE id = %s",
                    (upload_batch_id,),
                )

    def insert_rows(self, upload_batch_id: UUID, rows: Sequence[CsvRow]) -> int:
        if not rows:
            return 0
        from psycopg.types.json import Jsonb

        values = [
            (
                upload_batch_id,
                item.source_row_number,
                item.row.user_id,
                item.row.name,
                item.row.email,
                item.row.monthly_income,
                item.row.credit_score,
                item.row.employment_status,
                item.row.age,
                Jsonb(item.row.raw_data),
            )
            for item in rows
        ]
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO users
                       (upload_batch_id, source_row_number, user_id, name, email, monthly_income,
                        credit_score, employment_status, age, raw_data)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (upload_batch_id, source_row_number) DO NOTHING""",
                    values,
                )
                return max(cursor.rowcount, 0)

    def record_error(
        self,
        upload_batch_id: UUID,
        source_row_number: int,
        message: str,
        raw_data: dict[str, Any],
    ) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO ingestion_errors
                       (upload_batch_id, source_row_number, error_message, raw_data)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (upload_batch_id, source_row_number, message, Jsonb(raw_data)),
                )

    def complete_upload(
        self, upload_batch_id: UUID, row_count: int, error_count: int, status: str
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE upload_batches
                       SET status = %s, row_count = %s, error_count = %s,
                           completed_at = CASE WHEN %s IN ('completed', 'failed')
                                               THEN now() ELSE completed_at END
                       WHERE id = %s""",
                    (status, row_count, error_count, status, upload_batch_id),
                )


def _ingest(batch_id: UUID, body: Any) -> dict[str, Any]:
    result = StreamingCsvIngestionService(
        LocalPostgresIngestionRepository(), batch_size=INGESTION_BATCH_SIZE
    ).ingest(batch_id, body)
    return {"metrics": result.metrics.as_dict(), "status": result.status}


def _send_n8n(batch_id: UUID, key: str, metrics: dict[str, Any]) -> None:
    payload = {
        "event_id": f"minio-ingestion:{batch_id}:{hashlib.sha256(key.encode()).hexdigest()[:16]}",
        "event_type": "users.ingestion.completed",
        "upload_batch_id": str(batch_id),
        "object_key": key,
        "metrics": metrics["metrics"],
        "status": metrics["status"],
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    signed = build_signed_webhook_request(
        N8N_URL,
        payload,
        N8N_SECRET,
        timestamp=int(time.time()),
    )
    request = urllib.request.Request(
        signed.url, data=signed.body, headers=signed.headers, method="POST"
    )
    with urllib.request.urlopen(
        request, timeout=int(os.getenv("N8N_WEBHOOK_TIMEOUT_SECONDS", "10"))
    ) as response:
        if response.status >= 300:
            raise RuntimeError(f"n8n returned HTTP {response.status}")


def process_message(message: dict[str, Any]) -> None:
    for record in message.get("records", []):
        key = str(record["key"])
        batch_id = _batch_id(key)
        body = _s3().get_object(Bucket=S3_BUCKET, Key=key)["Body"]
        metrics = _ingest(batch_id, body)
        _send_n8n(batch_id, key, metrics)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def _health_server() -> None:
    port = int(os.getenv("WORKER_HEALTH_PORT", "8092"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


def run() -> None:
    threading.Thread(target=_health_server, daemon=True).start()
    queue_url = _queue_url()
    while True:
        response = _sqs().receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20)
        for message in response.get("Messages", []):
            try:
                process_message(json.loads(message["Body"]))
            except Exception as exc:  # keep the message available for retry
                print(f"ingestion-worker failed: {exc}", flush=True)
                time.sleep(2)
                continue
            _sqs().delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])


if __name__ == "__main__":
    run()
