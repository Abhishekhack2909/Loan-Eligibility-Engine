"""Small AWS Lambda adapters for the Serverless infrastructure blueprint.

The adapters deliberately reuse the backend service boundaries rather than
duplicating CSV parsing, persistence, or upload semantics. Dependencies beyond
the AWS Lambda Python runtime are supplied by the layer referenced in
``serverless.yml``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote_plus
from uuid import UUID

import boto3
from pydantic import ValidationError

from backend.app.errors import ExternalServiceNotConfiguredError, UploadNotFoundError
from backend.app.main import AppContainer
from backend.app.schemas import CreateUploadRequest
from backend.app.webhooks import N8nCompletionWebhook, UrllibWebhookTransport


_secret_cache: dict[str, dict[str, str]] = {}


def _secret_values(arn: str) -> dict[str, str]:
    """Read one JSON-or-plain-text secret once per warm Lambda container."""

    if not arn:
        return {}
    if arn in _secret_cache:
        return _secret_cache[arn]
    response = boto3.client("secretsmanager").get_secret_value(SecretId=arn)
    secret_string = response.get("SecretString")
    if secret_string is None:
        secret_string = base64.b64decode(response["SecretBinary"]).decode("utf-8")
    try:
        decoded = json.loads(secret_string)
    except json.JSONDecodeError:
        decoded = {"value": secret_string}
    if not isinstance(decoded, dict):
        decoded = {"value": str(decoded)}
    values = {str(key): str(value) for key, value in decoded.items()}
    _secret_cache[arn] = values
    return values


def _load_runtime_secrets(*, load_api_auth: bool = False) -> None:
    database = _secret_values(os.getenv("DATABASE_SECRET_ARN", ""))
    if database.get("DATABASE_DSN"):
        os.environ["DATABASE_DSN"] = database["DATABASE_DSN"]
    elif database.get("value"):
        os.environ["DATABASE_DSN"] = database["value"]

    webhook = _secret_values(os.getenv("N8N_WEBHOOK_SECRET_ARN", ""))
    if webhook.get("N8N_WEBHOOK_SECRET"):
        os.environ["N8N_WEBHOOK_SECRET"] = webhook["N8N_WEBHOOK_SECRET"]
    elif webhook.get("value"):
        os.environ["N8N_WEBHOOK_SECRET"] = webhook["value"]

    if load_api_auth:
        api = _secret_values(os.getenv("API_AUTH_TOKEN_SECRET_ARN", ""))
        if api.get("API_AUTH_TOKEN"):
            os.environ["API_AUTH_TOKEN"] = api["API_AUTH_TOKEN"]
        elif api.get("value"):
            os.environ["API_AUTH_TOKEN"] = api["value"]


def _response(status_code: int, body: Any) -> dict[str, Any]:
    origin = os.getenv("WEB_ORIGIN", "http://localhost:8080")
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": origin,
            "access-control-allow-headers": "content-type,x-api-key",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def _request_header(event: dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    expected = name.lower()
    return next(
        (str(value) for key, value in headers.items() if str(key).lower() == expected),
        "",
    )


def _require_api_auth(event: dict[str, Any]) -> None:
    expected = os.getenv("API_AUTH_TOKEN", "")
    if not expected:
        return
    received = _request_header(event, "x-api-key")
    if not received or not secrets.compare_digest(received, expected):
        raise PermissionError("invalid API key")


def _request_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise ValueError("request body must be a JSON object")
    return decoded


def api_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Adapt the optional HTTP API routes to the existing service layer."""

    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod", "GET")
    ).upper()
    path = event.get("rawPath") or event.get("path") or "/"

    if method == "GET" and path == "/health":
        return _response(200, {"status": "ok"})

    if method == "OPTIONS":
        return _response(204, {})

    try:
        _load_runtime_secrets(load_api_auth=True)
        _require_api_auth(event)
        container = AppContainer.default()
        if method == "POST" and path == "/uploads":
            request = CreateUploadRequest.model_validate(_request_body(event))
            result = container
            from backend.app.uploads import UploadService

            upload = UploadService(
                result.upload_repository,
                result.object_storage,
                result.settings,
            ).create_upload(request)
            return _response(201, upload.model_dump(mode="json"))

        if method == "GET" and path.startswith("/uploads/"):
            upload_id = UUID(path.rsplit("/", 1)[-1])
            from backend.app.uploads import UploadService

            upload = UploadService(
                container.upload_repository,
                container.object_storage,
                container.settings,
            ).get_upload(upload_id)
            return _response(200, upload.model_dump(mode="json"))
    except ValidationError as exc:
        return _response(422, {"detail": exc.errors()})
    except UploadNotFoundError as exc:
        return _response(404, {"detail": str(exc)})
    except PermissionError as exc:
        return _response(401, {"detail": str(exc)})
    except ExternalServiceNotConfiguredError as exc:
        return _response(503, {"detail": str(exc)})
    except (ValueError, json.JSONDecodeError) as exc:
        return _response(400, {"detail": str(exc)})

    return _response(404, {"detail": "route not found"})


def _object_created_message(body: str) -> tuple[str, UUID, str]:
    event = json.loads(body)
    detail = event.get("detail", event)
    bucket = detail.get("bucket", {}).get("name")
    key = detail.get("object", {}).get("key")
    expected_bucket = os.getenv("S3_BUCKET")
    if not bucket or not key or (expected_bucket and bucket != expected_bucket):
        raise ValueError("object-created event has an unexpected bucket or missing object key")
    key = unquote_plus(key)
    parts = key.split("/", 2)
    if len(parts) != 3 or parts[0] != "uploads":
        raise ValueError("object key must use uploads/{upload_id}/{filename}")
    return str(event.get("id", "")), UUID(parts[1]), key


def _send_completion(event_id: str, upload_id: UUID, object_key: str, result: Any) -> None:
    """Notify n8n after persistence; a failed callback keeps SQS retryable."""

    url = os.getenv("N8N_COMPLETION_WEBHOOK_URL", "")
    secret = os.getenv("N8N_WEBHOOK_SECRET", "")
    if not url and not secret:
        return
    if not url or not secret:
        raise ExternalServiceNotConfiguredError(
            "N8N_COMPLETION_WEBHOOK_URL and N8N_WEBHOOK_SECRET must be configured together"
        )
    payload = {
        "event_id": event_id
        or f"aws-s3-ingestion:{upload_id}:{hashlib.sha256(object_key.encode()).hexdigest()[:16]}",
        "event_type": "users.ingestion.completed",
        "upload_batch_id": str(upload_id),
        "object_key": object_key,
        "metrics": result.metrics.as_dict(),
        "status": result.status,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    N8nCompletionWebhook(
        url,
        secret,
        UrllibWebhookTransport(),
        timeout_seconds=int(os.getenv("N8N_WEBHOOK_TIMEOUT_SECONDS", "10")),
    ).send(payload)


def ingestion_handler(event: dict[str, Any], _context: Any) -> dict[str, list[dict[str, str]]]:
    """Process S3 EventBridge envelopes delivered through SQS.

    Returning only failed message IDs enables Lambda's partial batch response;
    database row idempotency makes a retried object safe to process again.
    """

    _load_runtime_secrets()
    container = AppContainer.default()
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        message_id = str(record.get("messageId", ""))
        try:
            event_id, upload_id, object_key = _object_created_message(record["body"])
            result = container.ingestion_service().ingest_object(
                upload_id,
                container.object_storage,
                object_key,
            )
            _send_completion(event_id, upload_id, object_key, result)
        except Exception:
            if message_id:
                failures.append({"itemIdentifier": message_id})
            else:
                raise
    return {"batchItemFailures": failures}
