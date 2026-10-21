from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import AppContainer, create_app
from backend.app.repositories import InMemoryUploadRepository
from backend.app.webhooks import build_signed_webhook_request
from backend.app.webhooks import N8nCompletionWebhook


class FakeStorage:
    def create_presigned_upload_url(self, object_key, content_type, expires_in_seconds):
        return f"http://minio.test/{object_key}?signed=1"

    def stream_object(self, object_key):
        raise AssertionError("not used by upload API tests")


def test_upload_endpoints_use_injected_boundaries() -> None:
    settings = Settings(presigned_url_expiry_seconds=120)
    container = AppContainer(
        settings=settings,
        upload_repository=InMemoryUploadRepository(),
        object_storage=FakeStorage(),
        ingestion_repository=object(),
    )
    client = TestClient(create_app(container))

    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/uploads", json={"filename": "customers.csv"})
    assert response.status_code == 201
    body = response.json()
    assert UUID(body["upload_id"])
    assert body["object_key"].endswith("/customers.csv")
    assert body["expires_in_seconds"] == 120

    status = client.get(f"/uploads/{body['upload_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "created"


def test_webhook_signature_uses_canonical_body_and_timestamp() -> None:
    payload = {"upload_id": "u-1", "status": "completed"}
    request = build_signed_webhook_request(
        "https://n8n.example.test/webhook/complete",
        payload,
        "secret",
        timestamp=1_700_000_000,
    )
    expected_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    expected_digest = hmac.new(
        b"secret", b"1700000000." + expected_body, hashlib.sha256
    ).hexdigest()

    assert request.body == expected_body
    assert request.headers["X-N8N-Signature"] == f"sha256={expected_digest}"


def test_completion_webhook_rejects_non_success_status() -> None:
    class Transport:
        def post(self, url, body, headers, timeout_seconds):
            return 503

    webhook = N8nCompletionWebhook("https://n8n.example.test/hook", "secret", Transport())

    try:
        webhook.send({"event_id": "evt"}, timestamp=1_700_000_000)
    except RuntimeError as exc:
        assert str(exc) == "n8n completion webhook returned HTTP 503"
    else:
        raise AssertionError("non-success webhook response must fail the ingestion attempt")


def test_aws_api_requires_configured_api_key(monkeypatch) -> None:
    from scripts.infra.lambda_handlers import api_handler

    monkeypatch.setenv("API_AUTH_TOKEN", "demo-token")
    monkeypatch.setenv("API_AUTH_TOKEN_SECRET_ARN", "")
    response = api_handler(
        {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/uploads",
            "body": '{"filename":"demo.csv"}',
            "headers": {},
        },
        None,
    )

    assert response["statusCode"] == 401
