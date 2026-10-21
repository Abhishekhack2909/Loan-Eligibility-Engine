"""Offline structural checks for the local n8n and service slice."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / "n8n"


def load_workflow(filename: str) -> dict[str, Any]:
    path = WORKFLOW_DIR / filename
    workflow = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(workflow.get("nodes"), list), f"{filename}: nodes must be a list"
    names = [node.get("name") for node in workflow["nodes"]]
    assert len(names) == len(set(names)), f"{filename}: node names must be unique"
    node_names = set(names)
    for source, outputs in workflow.get("connections", {}).items():
        assert source in node_names, f"{filename}: unknown connection source {source}"
        for branches in outputs.values():
            for branch in branches:
                for connection in branch:
                    assert connection["node"] in node_names, (
                        f"{filename}: unknown connection target {connection['node']}"
                    )
    return workflow


def node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    for item in workflow["nodes"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"missing node: {workflow['name']} / {name}")


def all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for child in value.values() for part in all_strings(child)]
    if isinstance(value, list):
        return [part for child in value for part in all_strings(child)]
    return []


def assert_a(workflow: dict[str, Any]) -> None:
    names = {item["name"] for item in workflow["nodes"]}
    for required in {
        "Schedule Trigger",
        "Manual Test Trigger",
        "Use Local Fixtures?",
        "Fetch Discover Personal Loan Page",
        "Fetch NerdWallet Personal Loan Page",
        "Fetch Prosper Personal Loan Page",
        "Local LLM - Resolve Ambiguous Criteria",
        "Validate Structured Loan Product",
        "Upsert loan_products",
    }:
        assert required in names, f"Workflow A missing {required}"
    http_urls = [
        node["parameters"].get("url", "")
        for node in workflow["nodes"]
        if node["type"] == "n8n-nodes-base.httpRequest"
    ]
    public_http_urls = [url for url in http_urls if url.startswith("https://")]
    assert len(public_http_urls) == 3
    assert all("personal-loans" in url for url in public_http_urls)
    assert not any(domain in url for url in public_http_urls for domain in ("sba.gov", "consumerfinance.gov"))
    fixture_paths = [
        node["parameters"].get("filePath", "")
        for node in workflow["nodes"]
        if node["type"] == "n8n-nodes-base.readBinaryFile"
    ]
    assert len(fixture_paths) == 3 and all(path.startswith("/data/workflows/fixtures/") for path in fixture_paths)
    assert workflow.get("active") is True
    assert not any(item["type"] == "n8n-nodes-base.merge" for item in workflow["nodes"])
    assert "LLM_MODE" in "\n".join(all_strings(workflow))
    assert "LLM_API_BASE_URL" in "\n".join(all_strings(workflow))
    assert "LLM_MODEL" in "\n".join(all_strings(workflow))
    assert "loan_products" in "\n".join(all_strings(node(workflow, "Upsert loan_products")))
    assert "criteria" in "\n".join(all_strings(node(workflow, "Validate Structured Loan Product")))
    assert "source_url" in "\n".join(all_strings(node(workflow, "Upsert loan_products")))


def assert_b(workflow: dict[str, Any]) -> None:
    assert workflow.get("active") is True
    names = {item["name"] for item in workflow["nodes"]}
    for required in {
        "Ingestion Completed Webhook",
        "Verify Signed Ingestion Event",
        "Record Event Idempotency",
        "Event Is New?",
        "Respond Duplicate Event",
        "Fetch Users",
        "Fetch Active Products",
        "SQL Coarse Filter",
        "Match Users with n8n Code",
        "Store Every Deterministic Match",
        "Compute Metrics and Enforce Invariant",
        "Execute Workflow C - Email Delivery",
    }:
        assert required in names, f"Workflow B missing {required}"
    text = "\n".join(all_strings(workflow))
    assert "naive" in text and "candidates" in text and "evaluated" in text and "stored" in text
    assert "top_k_highlights" in text and "may_qualify" in text
    assert "loan-eligibility-email-delivery" in text or "$env.WORKFLOW_C_ID" in text
    assert "ON CONFLICT (upload_batch_id, user_id, loan_product_id)" in text
    assert "processed_events" in text
    assert "x-n8n-signature" in text.lower() and "timingSafeEqual" in text
    assert "status !== 'completed'" in text
    assert "EXISTS (SELECT 1 FROM inserted)" in text
    assert "CROSS JOIN loan_products" in text and "sql_candidate" in text


def assert_c(workflow: dict[str, Any]) -> None:
    names = {item["name"] for item in workflow["nodes"]}
    for required in {
        "When B Calls Workflow C",
        "Demo Notification Webhook",
        "Group Matches and Apply DEMO_RECIPIENTS Allowlist",
        "Has Notification Matches?",
        "Mailpit Local HTTP Delivery",
        "AWS SES Delivery (credential-gated)",
        "AWS SES Credential Gate",
        "Record Mailpit Delivery",
        "Record SES Delivery",
        "Record Blocked Delivery",
    }:
        assert required in names, f"Workflow C missing {required}"
    text = "\n".join(all_strings(workflow))
    assert "DEMO_RECIPIENTS" in text
    assert "email_deliveries" in text
    assert "AWS_SES_ENABLED" in text and "AWS_SES_CREDENTIAL_NAME" in text
    assert "mailpit" in text.lower()
    assert "may_qualify" in text and "unknown" in text
    assert "no_matches" in text and "user_id" in text
    assert "demo_targets" in text and "DEMO_NOTIFICATION_TOKEN" in text
    assert not any(item["type"] == "n8n-nodes-base.merge" for item in workflow["nodes"])
    assert node(workflow, "Demo Notification Webhook")["parameters"].get("responseMode") == "lastNode"
    for delivery_node in ("Record Mailpit Delivery", "Record SES Delivery"):
        assert node(workflow, delivery_node).get("executeOnce") is False
    aws_node = node(workflow, "AWS SES Delivery (credential-gated)")
    assert aws_node.get("credentials", {}).get("aws", {}).get("id", "") == "loan-engine-aws-ses"


def assert_local_wiring() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "minio:" in compose and "event-router:" in compose and "localstack:" in compose
    assert "MINIO_NOTIFY_WEBHOOK_ENDPOINT_PRIMARY" in compose
    assert "event-router:8091/events/minio" in compose
    assert "localstack:4566" in compose
    assert "ingestion-worker:" in compose and "EVENT_QUEUE_NAME" in compose
    assert "N8N_WEBHOOK_URL: http://n8n:5678/webhook/loan-ingestion-completed" in compose
    assert "N8N_COMPLETION_WEBHOOK_URL: http://n8n:5678/webhook/loan-ingestion-completed" in compose
    assert "n8n-bootstrap.js" in compose and "loan-engine-provisioned-v1" in compose
    assert (ROOT / "local" / "minio_bootstrap.py").exists()
    assert (ROOT / "local" / "localstack-init.sh").exists()
    bootstrap = ROOT / "local" / "n8n-bootstrap.js"
    assert bootstrap.exists()
    bootstrap_text = bootstrap.read_text(encoding="utf-8")
    for workflow in ("01-public-loan-catalog.json", "02-ingestion-match.json", "03-email-delivery.json"):
        assert workflow in bootstrap_text
    assert "import:credentials" in bootstrap_text
    assert "public-loan-catalog-ingestion" in bootstrap_text
    assert "loan-ingestion-match" in bootstrap_text
    assert "put_bucket_notification_configuration" in (ROOT / "local" / "minio_bootstrap.py").read_text(encoding="utf-8")

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "N8N_WEBHOOK_URL=http://n8n:5678" in env
    assert "N8N_COMPLETION_WEBHOOK_URL=http://localhost" not in env
    worker = (ROOT / "local" / "ingestion_worker.py").read_text(encoding="utf-8")
    assert "StreamingCsvIngestionService" in worker
    assert "build_signed_webhook_request" in worker
    assert '"metrics": metrics["metrics"]' in worker
    assert '"status": metrics["status"]' in worker
    assert 'get_object(Bucket=S3_BUCKET, Key=key)["Body"]' in worker
    assert ".read()" not in worker

    migration = (ROOT / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
    assert "source_url" in migration and "content_sha256" in migration
    assert "may_qualify" in migration


def assert_no_secrets(workflows: list[dict[str, Any]]) -> None:
    text = "\n".join("\n".join(all_strings(workflow)) for workflow in workflows)
    assert not re.search(r"AKIA[0-9A-Z]{16}", text)
    assert not re.search(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", text)
    allowed_credential_ids = {"", "loan-engine-aws-ses"}
    for workflow in workflows:
        for item in workflow["nodes"]:
            for credential in item.get("credentials", {}).values():
                assert credential.get("id", "") in allowed_credential_ids


def main() -> None:
    workflows = [
        load_workflow("01-public-loan-catalog.json"),
        load_workflow("02-ingestion-match.json"),
        load_workflow("03-email-delivery.json"),
    ]
    assert_a(workflows[0])
    assert_b(workflows[1])
    assert_c(workflows[2])
    assert_local_wiring()
    assert_no_secrets(workflows)
    print("offline validation passed: 3 n8n exports, local event wiring, and secret checks")


if __name__ == "__main__":
    main()
