import pytest

from backend.app.errors import UnsafeRecipientError
from backend.app.metrics import IngestionMetrics
from backend.app.recipients import normalize_recipient


def test_metrics_partition_every_source_row() -> None:
    metrics = IngestionMetrics()
    for outcome in ("inserted", "duplicate", "invalid", "failed", "inserted"):
        metrics.record(outcome)

    assert metrics.total_rows == 5
    assert metrics.accepted_rows == 3
    metrics.assert_invariant()
    assert metrics.as_dict()["failed_rows"] == 1


def test_recipient_normalization_and_header_injection_safety() -> None:
    assert normalize_recipient(" ADA@EXAMPLE.COM ") == "ada@example.com"
    with pytest.raises(UnsafeRecipientError):
        normalize_recipient("ada@example.com\r\nBcc: attacker@example.com")
    with pytest.raises(UnsafeRecipientError):
        normalize_recipient("Ada <ada@example.com>")
