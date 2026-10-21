"""Small, dependency-free ingestion metrics model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class IngestionMetrics:
    """Counters with an explicit partition invariant.

    Every source row is classified exactly once as inserted, duplicate,
    invalid, or failed. This makes reported totals auditable.
    """

    total_rows: int = 0
    inserted_rows: int = 0
    duplicate_rows: int = 0
    invalid_rows: int = 0
    failed_rows: int = 0

    def record(self, outcome: str) -> None:
        if outcome not in {"inserted", "duplicate", "invalid", "failed"}:
            raise ValueError(f"unknown ingestion outcome: {outcome}")
        self.total_rows += 1
        if outcome == "inserted":
            self.inserted_rows += 1
        elif outcome == "duplicate":
            self.duplicate_rows += 1
        elif outcome == "invalid":
            self.invalid_rows += 1
        else:
            self.failed_rows += 1
        self.assert_invariant()

    @property
    def accepted_rows(self) -> int:
        return self.inserted_rows + self.duplicate_rows

    def assert_invariant(self) -> None:
        if self.total_rows != (
            self.inserted_rows
            + self.duplicate_rows
            + self.invalid_rows
            + self.failed_rows
        ):
            raise AssertionError("ingestion metrics do not partition total rows")

    def as_dict(self) -> dict[str, int]:
        self.assert_invariant()
        return {
            "total_rows": self.total_rows,
            "inserted_rows": self.inserted_rows,
            "duplicate_rows": self.duplicate_rows,
            "invalid_rows": self.invalid_rows,
            "failed_rows": self.failed_rows,
            "accepted_rows": self.accepted_rows,
        }
