"""Domain errors exposed by backend services."""


class UploadNotFoundError(LookupError):
    """Raised when an upload batch does not exist."""


class CsvValidationError(ValueError):
    """Raised when a CSV header or row violates the ingestion contract."""


class UnsafeRecipientError(ValueError):
    """Raised when an email recipient is not safe to send to."""


class ExternalServiceNotConfiguredError(RuntimeError):
    """Raised when a production-only integration has not been configured."""
