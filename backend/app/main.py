"""FastAPI application for the Loan Eligibility Engine backend."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .errors import ExternalServiceNotConfiguredError, UploadNotFoundError
from .ingestion import StreamingCsvIngestionService
from .repositories import (
    IngestionRepository,
    PostgresIngestionRepository,
    PostgresUploadRepository,
    UploadRepository,
)
from .schemas import CreateUploadRequest, HealthResponse, UploadResponse, UploadStatusResponse
from .storage import ObjectStorage, S3ObjectStorage
from .uploads import UploadService


@dataclass(slots=True)
class AppContainer:
    """Injectable application boundaries.

    Constructing the default container is side-effect free. AWS and
    PostgreSQL connections are lazy and only occur when a corresponding
    endpoint or service is invoked.
    """

    settings: Settings
    upload_repository: UploadRepository
    object_storage: ObjectStorage
    ingestion_repository: IngestionRepository

    @classmethod
    def default(cls, settings: Settings | None = None) -> "AppContainer":
        settings = settings or get_settings()
        return cls(
            settings=settings,
            upload_repository=PostgresUploadRepository(settings.database_dsn),
            object_storage=S3ObjectStorage(settings),
            ingestion_repository=PostgresIngestionRepository(settings.database_dsn),
        )

    def ingestion_service(self) -> StreamingCsvIngestionService:
        return StreamingCsvIngestionService(
            self.ingestion_repository,
            batch_size=self.settings.ingestion_batch_size,
        )


def _container_from_request(request: Request) -> AppContainer:
    return request.app.state.container


def create_app(container: AppContainer | None = None) -> FastAPI:
    app = FastAPI(title="Loan Eligibility Engine", version="1.0.0")
    app.state.container = container or AppContainer.default()

    @app.exception_handler(UploadNotFoundError)
    async def upload_not_found_handler(_: Request, exc: UploadNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ExternalServiceNotConfiguredError)
    async def external_service_handler(
        _: Request, exc: ExternalServiceNotConfiguredError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post(
        "/uploads",
        response_model=UploadResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_upload(
        payload: CreateUploadRequest,
        app_container: AppContainer = Depends(_container_from_request),
    ) -> UploadResponse:
        return UploadService(
            app_container.upload_repository,
            app_container.object_storage,
            app_container.settings,
        ).create_upload(payload)

    @app.get("/uploads/{upload_id}", response_model=UploadStatusResponse)
    async def get_upload(
        upload_id: UUID,
        app_container: AppContainer = Depends(_container_from_request),
    ) -> UploadStatusResponse:
        return UploadService(
            app_container.upload_repository,
            app_container.object_storage,
            app_container.settings,
        ).get_upload(upload_id)

    return app


app = create_app()

__all__ = ["AppContainer", "app", "create_app"]
