"""Shared timestamp helpers for the split response-team domain stores."""

from datetime import datetime, timezone

from persistence.team_status_contracts import TeamStatusPersistenceError


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""

    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp, treating a missing offset as UTC."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise TeamStatusPersistenceError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
