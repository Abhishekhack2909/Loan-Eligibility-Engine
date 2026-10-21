from __future__ import annotations

import io
from uuid import UUID, uuid4

from backend.app.ingestion import StreamingCsvIngestionService
from backend.app.schemas import CsvRow


CSV = (
    "user_id,name,email,monthly_income,credit_score,employment_status,age\n"
    "u-1,Ada,ada@example.com,5000,720,employed,30\n"
    "u-2,Bob,bob@example.com,3000,650,self-employed,40\n"
)


class FakeIngestionRepository:
    def __init__(self) -> None:
        self.keys: set[tuple[UUID, int]] = set()
        self.errors: list[tuple[int, str]] = []
        self.completed: list[tuple[int, int, str]] = []

    def mark_processing(self, upload_batch_id: UUID) -> None:
        pass

    def insert_rows(self, upload_batch_id: UUID, rows: list[CsvRow]) -> int:
        inserted = 0
        for row in rows:
            key = (upload_batch_id, row.source_row_number)
            if key not in self.keys:
                self.keys.add(key)
                inserted += 1
        return inserted

    def record_error(self, upload_batch_id, source_row_number, message, raw_data) -> None:
        self.errors.append((source_row_number, message))

    def complete_upload(self, upload_batch_id, row_count, error_count, status) -> None:
        self.completed.append((row_count, error_count, status))


def test_reprocessing_same_batch_counts_duplicates_without_new_inserts() -> None:
    repository = FakeIngestionRepository()
    service = StreamingCsvIngestionService(repository, batch_size=1)
    upload_id = uuid4()

    first = service.ingest(upload_id, io.StringIO(CSV))
    second = service.ingest(upload_id, io.StringIO(CSV))

    assert first.metrics.as_dict() == {
        "total_rows": 2,
        "inserted_rows": 2,
        "duplicate_rows": 0,
        "invalid_rows": 0,
        "failed_rows": 0,
        "accepted_rows": 2,
    }
    assert second.metrics.inserted_rows == 0
    assert second.metrics.duplicate_rows == 2
    assert second.status == "completed"
    assert len(repository.keys) == 2
