"""Deterministic local substitute for ambiguous public loan criteria extraction.

The endpoint intentionally accepts only public product copy. It has no user,
CSV, database, or credential inputs and returns the same JSON for the same
public text, which makes Workflow A reproducible offline.
"""

from __future__ import annotations

import json
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


INCOME_CONTEXT_RE = re.compile(r"(?:monthly\s+income|income)[^0-9$]{0,20}(?:\$|usd\s*)?(\d[\d,]*(?:\.\d+)?)", re.I)
CURRENCY_MONTHLY_RE = re.compile(r"(?:\$|usd\s*)(\d[\d,]*(?:\.\d+)?)\s*(?:per\s+month|monthly|/month)", re.I)
CREDIT_RE = re.compile(r"(?:credit\s+score|fico)[^\d]{0,30}(?:of\s+)?(\d{3})", re.I)
AGE_RE = re.compile(r"(?:at\s+least|minimum|age\s+of)\s*(\d{2})\s*(?:years?|y/o)?", re.I)
EMPLOYMENT_RE = re.compile(r"(?:employed|employment|income\s+from)[^.!]{0,30}\b(full[- ]?time|part[- ]?time|self[- ]?employed)\b", re.I)
FORBIDDEN_INPUT_KEYS = {"user", "users", "csv", "rows", "email", "monthly_income", "credit_score"}


def _number(value: str) -> int | float:
    value = value.replace(",", "")
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def extract_product(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload).intersection(FORBIDDEN_INPUT_KEYS):
        raise ValueError("the local LLM stub accepts public product text only")
    text = payload.get("product_text")
    source_url = payload.get("source_url")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("product_text is required")
    parsed_url = urlparse(str(source_url))
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("source_url must be a public HTTP(S) URL")

    criteria: dict[str, Any] = {}
    income = INCOME_CONTEXT_RE.search(text) or CURRENCY_MONTHLY_RE.search(text)
    if income:
        criteria["min_monthly_income"] = _number(income.group(1))
    credit = CREDIT_RE.search(text)
    if credit:
        criteria["min_credit_score"] = int(credit.group(1))
    age = AGE_RE.search(text)
    if age:
        criteria["min_age"] = int(age.group(1))
    employment = EMPLOYMENT_RE.search(text)
    if employment:
        status = employment.group(1).strip(" .,:;").casefold()
        if status in {"full time", "full-time", "fulltime"}:
            status = "full-time"
        criteria["employment_status"] = status

    product_id = str(payload.get("product_id") or "public-loan-product")
    title = str(payload.get("name") or product_id.replace("-", " ").title())
    ambiguous = [] if criteria else ["eligibility criteria were not stated numerically"]
    return {
        "product_id": product_id,
        "name": title[:255],
        "criteria": criteria,
        "source_url": str(source_url),
        "ambiguous_criteria": ambiguous,
        "confidence": "high" if criteria else "low",
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/extract":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            self._json(HTTPStatus.OK, extract_product(payload))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print("llm-stub:", format % args, flush=True)


if __name__ == "__main__":
    port = int(os.getenv("LLM_STUB_PORT", "8090"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
