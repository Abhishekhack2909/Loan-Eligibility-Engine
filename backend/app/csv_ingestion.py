"""Streaming CSV parsing and validation.

The parser consumes chunks from an object-storage body instead of loading the
object into memory. It keeps only a small text buffer plus the current CSV
record; the ingestion service controls database batch size separately.
"""

from __future__ import annotations

import csv
import codecs
from dataclasses import dataclass
from itertools import chain
from typing import Any, Iterator, Sequence

from pydantic import ValidationError

from .errors import CsvValidationError
from .schemas import CsvRow, LoanCsvRow, REQUIRED_CSV_COLUMNS


CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class CsvRowIssue:
    source_row_number: int
    message: str
    raw_data: dict[str, Any]


def _source_chunks(source: Any, chunk_size: int = CHUNK_SIZE) -> Iterator[str | bytes]:
    """Yield chunks from file-like, boto3 StreamingBody, or iterable sources."""

    if hasattr(source, "iter_chunks"):
        yield from source.iter_chunks(chunk_size=chunk_size)
        return

    if hasattr(source, "read"):
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                return
            yield chunk

    else:
        yield from source


def iter_text_lines(source: Any) -> Iterator[str]:
    """Decode UTF-8 chunks and yield physical CSV lines incrementally."""

    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    pending = ""
    try:
        for chunk in _source_chunks(source):
            if isinstance(chunk, bytes):
                text = decoder.decode(chunk, final=False)
            elif isinstance(chunk, str):
                text = chunk
            else:
                raise TypeError("CSV source chunks must be bytes or strings")
            pending += text
            parts = pending.splitlines(keepends=True)
            # Keep a trailing CR until the next chunk so a CRLF boundary is
            # never exposed to csv.DictReader as two physical lines.
            if parts and not parts[-1].endswith("\n"):
                pending = parts.pop()
            else:
                pending = ""
            yield from parts
        pending += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise CsvValidationError("CSV must be valid UTF-8") from exc
    if pending:
        yield pending


def validate_csv_header(fieldnames: Sequence[str] | None) -> tuple[str, ...]:
    """Validate required columns while explicitly allowing extra columns."""

    if not fieldnames:
        raise CsvValidationError("CSV must contain a header row")
    normalized = list(fieldnames)
    if normalized[0].startswith("\ufeff"):
        normalized[0] = normalized[0].lstrip("\ufeff")
    if any(not column for column in normalized):
        raise CsvValidationError("CSV header contains an empty column name")
    if len(set(normalized)) != len(normalized):
        raise CsvValidationError("CSV header contains duplicate column names")
    missing = [column for column in REQUIRED_CSV_COLUMNS if column not in normalized]
    if missing:
        raise CsvValidationError(f"CSV is missing required columns: {', '.join(missing)}")
    return tuple(normalized)


def _reader(source: Any) -> tuple[tuple[str, ...], Iterator[dict[str, Any]]]:
    lines = iter_text_lines(source)
    try:
        first_line = next(lines)
    except StopIteration as exc:
        raise CsvValidationError("CSV is empty") from exc
    reader = csv.DictReader(chain((first_line,), lines))
    fieldnames = validate_csv_header(reader.fieldnames)
    # ``validate_csv_header`` returns the BOM-free form for text sources too;
    # put that normalized form back on DictReader so data keys match it.
    reader.fieldnames = list(fieldnames)
    return fieldnames, iter(reader)


def _validated_row(raw_data: dict[str | None, Any], source_row_number: int) -> CsvRow:
    if None in raw_data:
        raise CsvValidationError("row contains more values than the header")
    values = {str(key): "" if value is None else str(value) for key, value in raw_data.items()}
    known = {column: values.get(column, "") for column in REQUIRED_CSV_COLUMNS}
    extras = {column: value for column, value in values.items() if column not in REQUIRED_CSV_COLUMNS}
    try:
        model = LoanCsvRow.model_validate({**known, "raw_data": extras})
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise CsvValidationError(messages) from exc
    return CsvRow(source_row_number=source_row_number, row=model)


def iter_csv_rows(source: Any) -> Iterator[CsvRow]:
    """Yield validated data rows, raising on the first invalid row."""

    _, rows = _reader(source)
    for source_row_number, raw_data in enumerate(rows, start=2):
        yield _validated_row(raw_data, source_row_number)


def iter_csv_rows_with_errors(source: Any) -> Iterator[CsvRow | CsvRowIssue]:
    """Yield valid rows and per-row issues so ingestion can continue safely."""

    _, rows = _reader(source)
    for source_row_number, raw_data in enumerate(rows, start=2):
        try:
            yield _validated_row(raw_data, source_row_number)
        except CsvValidationError as exc:
            clean_raw = {
                str(key): value for key, value in raw_data.items() if key is not None
            }
            yield CsvRowIssue(
                source_row_number=source_row_number,
                message=str(exc),
                raw_data=clean_raw,
            )


def idempotency_key(upload_batch_id: str, source_row_number: int) -> str:
    """Create the stable logical key used by every ingestion implementation."""

    if not upload_batch_id:
        raise ValueError("upload_batch_id must not be blank")
    if source_row_number < 2:
        raise ValueError("source_row_number must include the header and be at least 2")
    return f"{upload_batch_id}:{source_row_number}"
