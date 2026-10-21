"""Safe recipient handling for outbound email integrations."""

from __future__ import annotations

from .errors import UnsafeRecipientError
from .schemas import EMAIL_PATTERN


def normalize_recipient(value: str) -> str:
    """Return a plain, normalized email or reject unsafe input.

    Display names and CR/LF are rejected rather than passed to a downstream
    mail library where they could be interpreted as headers.
    """

    if not isinstance(value, str):
        raise UnsafeRecipientError("recipient must be a string")
    stripped = value.strip()
    if "\r" in value or "\n" in value or any(character.isspace() for character in stripped):
        raise UnsafeRecipientError("recipient must be a plain email address")
    normalized = stripped.lower()
    if not EMAIL_PATTERN.fullmatch(normalized) or len(normalized) > 320:
        raise UnsafeRecipientError("recipient must be a valid plain email address")
    if any(character in normalized for character in (",", ";", "<", ">")):
        raise UnsafeRecipientError("recipient must not contain a display name or list")
    return normalized
