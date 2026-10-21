"""Environment-backed application settings.

Settings deliberately use only the standard library and pydantic so importing
the application does not establish an external connection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with safe local defaults.

    Production deployments should provide the S3 and PostgreSQL values via
    environment variables. The defaults are suitable for a local MinIO and
    PostgreSQL setup but no connection is made while settings are loaded.
    """

    app_name: str = "loan-eligibility-engine"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "loan-uploads"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    presigned_url_expiry_seconds: int = 900
    database_dsn: str | None = None
    n8n_completion_webhook_url: str | None = None
    n8n_webhook_secret: str | None = None
    n8n_webhook_timeout_seconds: int = 10
    ingestion_batch_size: int = 500


def get_settings() -> Settings:
    """Load settings at the point the application is constructed."""

    return Settings(
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        s3_region=os.getenv("S3_REGION", "us-east-1"),
        s3_bucket=os.getenv("S3_BUCKET", "loan-uploads"),
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        presigned_url_expiry_seconds=_env_int("PRESIGNED_URL_EXPIRY_SECONDS", 900),
        database_dsn=os.getenv("DATABASE_DSN"),
        n8n_completion_webhook_url=os.getenv("N8N_COMPLETION_WEBHOOK_URL"),
        n8n_webhook_secret=os.getenv("N8N_WEBHOOK_SECRET"),
        n8n_webhook_timeout_seconds=_env_int("N8N_WEBHOOK_TIMEOUT_SECONDS", 10),
        ingestion_batch_size=_env_int("INGESTION_BATCH_SIZE", 500),
    )
