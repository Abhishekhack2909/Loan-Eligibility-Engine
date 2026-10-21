"""Persistence ports and PostgreSQL implementations.

The service layer depends on the small protocols in this module. Tests can
provide in-memory implementations without importing or running PostgreSQL.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Protocol, Sequence
from uuid import UUID

from .errors import ExternalServiceNotConfiguredError, UploadNotFoundError
from .schemas import CsvRow, UploadStatusResponse


class UploadRepository(Protocol):
    def create_upload(self, upload_id: UUID, object_key: str) -> None: ...

    def get_upload(self, upload_id: UUID) -> UploadStatusResponse: ...


class IngestionRepository(Protocol):
    def mark_processing(self, upload_batch_id: UUID) -> None: ...

    def insert_rows(self, upload_batch_id: UUID, rows: Sequence[CsvRow]) -> int: ...

    def record_error(
        self,
        upload_batch_id: UUID,
        source_row_number: int,
        message: str,
        raw_data: dict[str, Any],
    ) -> None: ...

    def complete_upload(
        self, upload_batch_id: UUID, row_count: int, error_count: int, status: str
    ) -> None: ...


def _require_dsn(dsn: str | None) -> str:
    if not dsn:
        raise ExternalServiceNotConfiguredError("DATABASE_DSN is not configured")
    return dsn


class PostgresUploadRepository:
    """Short-lived transaction repository for upload metadata."""

    def __init__(self, dsn: str | None) -> None:
        self.dsn = dsn

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise ExternalServiceNotConfiguredError("psycopg is not installed") from exc
        return psycopg.connect(_require_dsn(self.dsn))

    def create_upload(self, upload_id: UUID, object_key: str) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO upload_batches (id, object_key, status)
                    VALUES (%s, %s, 'created')
                    """,
                    (upload_id, object_key),
                )
            connection.commit()

    def get_upload(self, upload_id: UUID) -> UploadStatusResponse:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, object_key, status, row_count, error_count,
                           created_at, completed_at
                    FROM upload_batches
                    WHERE id = %s
                    """,
                    (upload_id,),
                )
                record = cursor.fetchone()
        if record is None:
            raise UploadNotFoundError(str(upload_id))
        return UploadStatusResponse(
            upload_id=record[0],
            object_key=record[1],
            status=record[2],
            row_count=record[3],
            error_count=record[4],
            created_at=record[5],
            completed_at=record[6],
        )


class PostgresIngestionRepository:
    """Batch insert adapter with database-enforced row idempotency."""

    def __init__(self, dsn: str | None) -> None:
        self.dsn = dsn

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise ExternalServiceNotConfiguredError("psycopg is not installed") from exc
        return psycopg.connect(_require_dsn(self.dsn))

    def mark_processing(self, upload_batch_id: UUID) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE upload_batches SET status = 'processing' WHERE id = %s",
                    (upload_batch_id,),
                )
            connection.commit()

    def insert_rows(self, upload_batch_id: UUID, rows: Sequence[CsvRow]) -> int:
        if not rows:
            return 0
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise ExternalServiceNotConfiguredError("psycopg is not installed") from exc
        values = [
            (
                upload_batch_id,
                csv_row.source_row_number,
                csv_row.row.user_id,
                csv_row.row.name,
                csv_row.row.email,
                csv_row.row.monthly_income,
                csv_row.row.credit_score,
                csv_row.row.employment_status,
                csv_row.row.age,
                Jsonb(csv_row.row.raw_data),
            )
            for csv_row in rows
        ]
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO users (
                        upload_batch_id, source_row_number, user_id, name, email,
                        monthly_income, credit_score, employment_status, age, raw_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (upload_batch_id, source_row_number) DO NOTHING
                    """,
                    values,
                )
                inserted = cursor.rowcount
            connection.commit()
        return max(inserted, 0)

    def record_error(
        self,
        upload_batch_id: UUID,
        source_row_number: int,
        message: str,
        raw_data: dict[str, Any],
    ) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingestion_errors
                        (upload_batch_id, source_row_number, error_message, raw_data)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (upload_batch_id, source_row_number) DO NOTHING
                    """,
                    (upload_batch_id, source_row_number, message, Jsonb(raw_data)),
                )
            connection.commit()

    def complete_upload(
        self, upload_batch_id: UUID, row_count: int, error_count: int, status: str
    ) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE upload_batches
                    SET status = %s, row_count = %s, error_count = %s,
                        completed_at = CASE WHEN %s IN ('completed', 'failed')
                                            THEN now() ELSE completed_at END
                    WHERE id = %s
                    """,
                    (status, row_count, error_count, status, upload_batch_id),
                )
            connection.commit()


@dataclass(slots=True)
class InMemoryUploadRepository:
    """Small fake useful for API tests and local development."""

    uploads: dict[UUID, UploadStatusResponse]

    def __init__(self) -> None:
        self.uploads = {}

    def create_upload(self, upload_id: UUID, object_key: str) -> None:
        self.uploads[upload_id] = UploadStatusResponse(
            upload_id=upload_id,
            object_key=object_key,
            status="created",
            row_count=0,
            error_count=0,
            created_at=datetime.now().astimezone(),
        )

    def get_upload(self, upload_id: UUID) -> UploadStatusResponse:
        try:
            return self.uploads[upload_id]
        except KeyError as exc:
            raise UploadNotFoundError(str(upload_id)) from exc
