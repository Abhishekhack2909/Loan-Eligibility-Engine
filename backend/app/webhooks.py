"""Signed n8n completion webhook helper with injectable transport."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class WebhookTransport(Protocol):
    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: int
    ) -> int: ...


class UrllibWebhookTransport:
    """Default network adapter; it is only invoked when explicitly sent."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: int
    ) -> int:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        with urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status)


@dataclass(frozen=True, slots=True)
class SignedWebhookRequest:
    url: str
    body: bytes
    headers: dict[str, str]


def build_signed_webhook_request(
    url: str,
    payload: Mapping[str, Any],
    secret: str,
    *,
    timestamp: int | None = None,
) -> SignedWebhookRequest:
    """Serialize and sign a canonical payload for an n8n completion hook."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("webhook URL must be an absolute HTTP(S) URL")
    if any(character in url for character in "\r\n"):
        raise ValueError("webhook URL contains a header injection character")
    if not secret:
        raise ValueError("webhook secret must not be blank")
    request_timestamp = int(time.time()) if timestamp is None else int(timestamp)
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signed_value = f"{request_timestamp}.".encode("ascii") + body
    digest = hmac.new(secret.encode("utf-8"), signed_value, hashlib.sha256).hexdigest()
    return SignedWebhookRequest(
        url=url,
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-N8N-Timestamp": str(request_timestamp),
            "X-N8N-Signature": f"sha256={digest}",
        },
    )


class N8nCompletionWebhook:
    def __init__(
        self,
        url: str,
        secret: str,
        transport: WebhookTransport,
        timeout_seconds: int = 10,
    ) -> None:
        self.url = url
        self.secret = secret
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def send(self, payload: Mapping[str, Any], *, timestamp: int | None = None) -> int:
        request = build_signed_webhook_request(
            self.url, payload, self.secret, timestamp=timestamp
        )
        status_code = self.transport.post(
            request.url, request.body, request.headers, self.timeout_seconds
        )
        if not 200 <= status_code < 300:
            raise RuntimeError(f"n8n completion webhook returned HTTP {status_code}")
        return status_code
