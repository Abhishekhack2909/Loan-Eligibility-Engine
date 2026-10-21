from __future__ import annotations

import io
from uuid import uuid4

import pytest

from backend.app.csv_ingestion import (
    CsvRowIssue,
    idempotency_key,
    iter_csv_rows,
    iter_csv_rows_with_errors,
)
from backend.app.errors import CsvValidationError


CSV = (
    "user_id,name,email,monthly_income,credit_score,employment_status,age,region\n"
    "u-1,Ada,ADA@example.com,5000.50,720,employed,30,west\n"
)


class TinyChunks:
    def __init__(self, value: bytes, size: int = 3) -> None:
        self.value = value
        self.size = size

    def iter_chunks(self, chunk_size: int):
        for offset in range(0, len(self.value), self.size):
            yield self.value[offset : offset + self.size]


def test_csv_parser_streams_chunks_and_preserves_extra_columns() -> None:
    rows = list(iter_csv_rows(TinyChunks(CSV.encode("utf-8"))))

    assert len(rows) == 1
    assert rows[0].source_row_number == 2
    assert rows[0].row.email == "ada@example.com"
    assert str(rows[0].row.monthly_income) == "5000.50"
    assert rows[0].row.raw_data == {"region": "west"}


def test_csv_parser_rejects_missing_required_columns() -> None:
    with pytest.raises(CsvValidationError, match="missing required columns"):
        list(iter_csv_rows(io.StringIO("user_id,name\nu-1,Ada\n")))


def test_row_validation_is_recoverable_for_streaming_ingestion() -> None:
    value = CSV + "u-2,Bad,bad-email,not-income,720,employed,30,east\n"

    parsed = list(iter_csv_rows_with_errors(io.StringIO(value)))

    assert parsed[0].source_row_number == 2
    assert isinstance(parsed[1], CsvRowIssue)
    assert parsed[1].source_row_number == 3
    assert "monthly_income" in parsed[1].message


def test_idempotency_key_is_stable_and_requires_data_row_number() -> None:
    upload_id = str(uuid4())

    assert idempotency_key(upload_id, 2) == f"{upload_id}:2"
    with pytest.raises(ValueError):
        idempotency_key(upload_id, 1)
