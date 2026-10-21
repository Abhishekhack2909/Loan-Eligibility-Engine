"""Batching service for validated object-storage CSV ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .csv_ingestion import CsvRowIssue, iter_csv_rows_with_errors
from .metrics import IngestionMetrics
from .repositories import IngestionRepository
from .storage import ObjectStorage


@dataclass(frozen=True, slots=True)
class IngestionResult:
    upload_batch_id: UUID
    status: str
    metrics: IngestionMetrics


class StreamingCsvIngestionService:
    """Read, validate, and persist a CSV in bounded batches.

    The database repository owns the idempotency constraint. A repeated
    ``upload_batch_id`` and source row number is therefore counted as a
    duplicate and never creates a second user row.
    """

    def __init__(self, repository: IngestionRepository, batch_size: int = 500) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        self.repository = repository
        self.batch_size = batch_size

    def ingest(self, upload_batch_id: UUID, source: Any) -> IngestionResult:
        metrics = IngestionMetrics()
        pending = []
        self.repository.mark_processing(upload_batch_id)
        try:
            for item in iter_csv_rows_with_errors(source):
                if isinstance(item, CsvRowIssue):
                    self.repository.record_error(
                        upload_batch_id,
                        item.source_row_number,
                        item.message,
                        item.raw_data,
                    )
                    metrics.record("invalid")
                    continue
                pending.append(item)
                if len(pending) >= self.batch_size:
                    self._flush(upload_batch_id, pending, metrics)
                    pending.clear()
            if pending:
                self._flush(upload_batch_id, pending, metrics)
        except Exception:
            # Header and infrastructure failures have no reliable row-level
            # count. Persist the terminal state and preserve the root error.
            try:
                self.repository.complete_upload(
                    upload_batch_id,
                    metrics.total_rows,
                    metrics.invalid_rows + metrics.failed_rows + 1,
                    "failed",
                )
            finally:
                self._close_source(source)
            raise
        else:
            status = "failed" if metrics.failed_rows else "completed"
            self.repository.complete_upload(
                upload_batch_id,
                metrics.total_rows,
                metrics.invalid_rows + metrics.failed_rows,
                status,
            )
            self._close_source(source)
            return IngestionResult(upload_batch_id, status, metrics)

    def ingest_object(
        self,
        upload_batch_id: UUID,
        storage: ObjectStorage,
        object_key: str,
    ) -> IngestionResult:
        """Stream one object-store object through the same bounded pipeline."""

        return self.ingest(upload_batch_id, storage.stream_object(object_key))

    def _flush(self, upload_batch_id: UUID, rows: list[Any], metrics: IngestionMetrics) -> None:
        try:
            inserted = self.repository.insert_rows(upload_batch_id, rows)
        except Exception:
            for _ in rows:
                metrics.record("failed")
            raise
        if inserted < 0 or inserted > len(rows):
            raise RuntimeError("ingestion repository returned an invalid insert count")
        for index in range(len(rows)):
            metrics.record("inserted" if index < inserted else "duplicate")

    @staticmethod
    def _close_source(source: Any) -> None:
        close = getattr(source, "close", None)
        if close is not None:
            close()
