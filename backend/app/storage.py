"""S3-compatible object storage adapter."""

from __future__ import annotations

from typing import Any, Iterator, Protocol

from .config import Settings
from .errors import ExternalServiceNotConfiguredError


class ObjectStorage(Protocol):
    def create_presigned_upload_url(
        self, object_key: str, content_type: str, expires_in_seconds: int
    ) -> str: ...

    def stream_object(self, object_key: str) -> Any: ...


class S3ObjectStorage:
    """Lazy boto3 adapter supporting AWS S3 and MinIO-compatible endpoints."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.s3_bucket:
            raise ExternalServiceNotConfiguredError("S3_BUCKET is not configured")
        try:
            import boto3
        except ImportError as exc:
            raise ExternalServiceNotConfiguredError("boto3 is not installed") from exc

        kwargs: dict[str, Any] = {
            "region_name": self.settings.s3_region,
        }
        if self.settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self.settings.s3_endpoint_url
        if self.settings.s3_access_key_id and self.settings.s3_secret_access_key:
            kwargs["aws_access_key_id"] = self.settings.s3_access_key_id
            kwargs["aws_secret_access_key"] = self.settings.s3_secret_access_key
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def create_presigned_upload_url(
        self, object_key: str, content_type: str, expires_in_seconds: int
    ) -> str:
        return self._get_client().generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self.settings.s3_bucket,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in_seconds,
            HttpMethod="PUT",
        )

    def stream_object(self, object_key: str) -> Any:
        response = self._get_client().get_object(
            Bucket=self.settings.s3_bucket,
            Key=object_key,
        )
        return response["Body"]
