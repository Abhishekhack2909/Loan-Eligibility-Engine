"""Pydantic contracts shared by API and ingestion services."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


REQUIRED_CSV_COLUMNS: tuple[str, ...] = (
    "user_id",
    "name",
    "email",
    "monthly_income",
    "credit_score",
    "employment_status",
    "age",
)

EMAIL_PATTERN = re.compile(r"^[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+$")


class CreateUploadRequest(BaseModel):
    """Request metadata used to create a presigned CSV upload."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(default="upload.csv", min_length=1, max_length=255)
    content_type: str = Field(default="text/csv", min_length=1, max_length=255)
    expires_in_seconds: int | None = Field(default=None, ge=1, le=86_400)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if "\r" in value or "\n" in value or "\\" in value:
            raise ValueError("filename contains an invalid character")
        filename = value.rsplit("/", 1)[-1]
        if not filename:
            raise ValueError("filename must contain a name")
        return filename

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("content_type contains an invalid character")
        return value


class UploadResponse(BaseModel):
    upload_id: UUID
    upload_url: str
    object_key: str
    expires_in_seconds: int


class UploadStatusResponse(BaseModel):
    upload_id: UUID
    object_key: str
    status: str
    row_count: int
    error_count: int
    created_at: datetime
    completed_at: datetime | None = None


class HealthResponse(BaseModel):
    status: str


class LoanCsvRow(BaseModel):
    """The exact known portion of one input row.

    ``raw_data`` contains columns unknown to the engine. This prevents the
    matching rules from accidentally depending on columns that were not part
    of the supplied CSV contract.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    monthly_income: Decimal = Field(ge=0)
    credit_score: int = Field(ge=0, le=850)
    employment_status: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=150)
    raw_data: dict[str, str] = Field(default_factory=dict)

    @field_validator("user_id", "name", "employment_status")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("must be a valid email address")
        return value


class CsvRow(BaseModel):
    """Validated row plus its 1-based CSV source row number."""

    model_config = ConfigDict(frozen=True)

    source_row_number: int = Field(ge=2)
    row: LoanCsvRow


class IngestionErrorRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_row_number: int = Field(ge=2)
    message: str
    raw_data: dict[str, Any] = Field(default_factory=dict)


class MatchResult(BaseModel):
    """Deterministic eligibility decision for one user/product pair."""

    model_config = ConfigDict(frozen=True)

    decision: str
    evaluated_criteria: dict[str, bool]
    unknown_criteria: dict[str, Any]
    reasons: tuple[str, ...]
