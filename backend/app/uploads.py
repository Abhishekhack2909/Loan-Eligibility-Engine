"""Upload lifecycle service."""

from __future__ import annotations

from uuid import UUID, uuid4

from .config import Settings
from .repositories import UploadRepository
from .schemas import CreateUploadRequest, UploadResponse, UploadStatusResponse
from .storage import ObjectStorage


class UploadService:
    def __init__(
        self,
        repository: UploadRepository,
        storage: ObjectStorage,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.settings = settings

    def create_upload(self, request: CreateUploadRequest) -> UploadResponse:
        upload_id = uuid4()
        object_key = f"uploads/{upload_id}/{request.filename}"
        expires = request.expires_in_seconds or self.settings.presigned_url_expiry_seconds
        self.repository.create_upload(upload_id, object_key)
        upload_url = self.storage.create_presigned_upload_url(
            object_key,
            request.content_type,
            expires,
        )
        return UploadResponse(
            upload_id=upload_id,
            upload_url=upload_url,
            object_key=object_key,
            expires_in_seconds=expires,
        )

    def get_upload(self, upload_id: UUID) -> UploadStatusResponse:
        return self.repository.get_upload(upload_id)
