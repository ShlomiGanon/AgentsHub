"""Validation and normalization for the single user full-name field."""

from __future__ import annotations

import unicodedata


MAX_FULL_NAME_LENGTH = 120


class InvalidFullNameError(ValueError):
    """Raised when a supplied display name is not a clear full name."""


def normalize_full_name(value: object, *, allow_empty: bool = False) -> str:
    """Return a whitespace-normalized full name containing at least two words."""

    if not isinstance(value, str):
        raise InvalidFullNameError("full name must be text")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise InvalidFullNameError("full name must not contain control characters")
    normalized = " ".join(value.split())
    if not normalized and allow_empty:
        return ""
    if not normalized:
        raise InvalidFullNameError("full name is required")
    if len(normalized) > MAX_FULL_NAME_LENGTH:
        raise InvalidFullNameError(f"full name must be at most {MAX_FULL_NAME_LENGTH} characters")
    if len(normalized.split(" ")) < 2:
        raise InvalidFullNameError("full name must contain at least two words")
    return normalized
