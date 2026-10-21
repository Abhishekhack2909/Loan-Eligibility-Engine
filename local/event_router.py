"""Receive MinIO webhook notifications and publish them to LocalStack SQS."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote_plus

QUEUE_NAME = os.getenv("EVENT_QUEUE_NAME", "loan-eligibility-events")
LOCALSTACK_ENDPOINT = os.getenv("LOCALSTACK_ENDPOINT_URL", "http://localstack:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")


def _sqs() -> Any:
    import boto3

    return boto3.client(
        "sqs",
        endpoint_url=LOCALSTACK_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _queue_url() -> str:
    return _sqs().get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]


def _validate_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("Records"), list):
        raise ValueError("expected a MinIO event with a Records array")
    accepted: list[dict[str, Any]] = []
    for record in payload["Records"]:
        if not isinstance(record, dict):
            continue
        s3 = record.get("s3")
        if not isinstance(s3, dict) or not isinstance(s3.get("object"), dict):
            continue
        bucket = s3.get("bucket", {}).get("name")
        key = s3["object"].get("key")
        if not bucket or not key:
            continue
        accepted.append(
            {
                "event_name": record.get("eventName", "unknown"),
                "event_time": record.get("eventTime"),
                "bucket": bucket,
                "key": unquote_plus(str(key)),
                "etag": s3["object"].get("eTag"),
                "size": s3["object"].get("size"),
            }
        )
    if not accepted:
        raise ValueError("MinIO event contained no usable object records")
    return {"source": "minio", "records": accepted}


class RouterHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"status": "ok", "queue": QUEUE_NAME})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/events/minio":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            event = _validate_event(json.loads(self.rfile.read(length)))
            response = _sqs().send_message(
                QueueUrl=_queue_url(),
                MessageBody=json.dumps(event, separators=(",", ":")),
            )
            self._send(HTTPStatus.ACCEPTED, {"status": "queued", "message_id": response["MessageId"]})
        except (ValueError, json.JSONDecodeError, KeyError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - infrastructure failure path
            self._send(HTTPStatus.BAD_GATEWAY, {"error": f"queue publish failed: {exc}"})

    def log_message(self, format: str, *args: Any) -> None:
        print("event-router:", format % args, flush=True)


if __name__ == "__main__":
    port = int(os.getenv("EVENT_ROUTER_PORT", "8091"))
    ThreadingHTTPServer(("0.0.0.0", port), RouterHandler).serve_forever()
