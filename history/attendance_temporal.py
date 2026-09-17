"""Deterministic resolution of attendance availability periods."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


_DAY_NAMES = {
    "\u05e8\u05d0\u05e9\u05d5\u05df": 6,
    "\u05e9\u05e0\u05d9": 0,
    "\u05e9\u05dc\u05d9\u05e9\u05d9": 1,
    "\u05e8\u05d1\u05d9\u05e2\u05d9": 2,
    "\u05d7\u05de\u05d9\u05e9\u05d9": 3,
    "\u05e9\u05d9\u05e9\u05d9": 4,
    "\u05e9\u05d1\u05ea": 5,
}
_DAY_TOKEN = "|".join(_DAY_NAMES)
_TODAY = "\u05d4\u05d9\u05d5\u05dd"
_TOMORROW = "\u05de\u05d7\u05e8"
_DAY = "\u05d9\u05d5\u05dd"
_FROM = "\u05de"
_FROM_DAY = "\u05de\u05d9\u05d5\u05dd"
_UNTIL = "\u05e2\u05d3"
_AND_UNTIL = "\u05d5\u05e2\u05d3"
_EVENING = "\u05d1\u05e2\u05e8\u05d1"
_RANGE_PATTERN = re.compile(
    rf"(?:{_FROM_DAY}\s*|{_FROM})(?P<start>{_DAY_TOKEN}|{_TODAY}|{_TOMORROW})"
    rf"\s*(?:{_UNTIL}|{_AND_UNTIL})\s*(?:{_DAY}\s*)?"
    rf"(?P<end>{_DAY_TOKEN}|{_TODAY}|{_TOMORROW})(?P<evening>\s+{_EVENING})?"
)
_EXPLICIT_RANGE_PATTERN = re.compile(
    rf"(?:{_FROM_DAY}\s*|{_FROM})?(?P<start>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"\s*(?:{_UNTIL}|{_AND_UNTIL})\s*(?P<end>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"(?P<evening>\s+{_EVENING})?"
)
_EXPLICIT_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_WEEKDAY_PATTERN = re.compile(rf"(?:{_DAY}\s+)?(?P<day>{_DAY_TOKEN})")

_EVENING_END = time(hour=20)


@dataclass(frozen=True)
class AvailabilityPeriod:
    """A half-open, absolute availability interval in the profile timezone."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("availability period timestamps must include a timezone")
        if self.end <= self.start:
            raise ValueError("availability period end must be after its start")

    @property
    def availability_start(self) -> str:
        return self.start.astimezone(timezone.utc).isoformat()

    @property
    def availability_end(self) -> str:
        return self.end.astimezone(timezone.utc).isoformat()


def _parse_received_at(received_at: str) -> datetime:
    parsed = datetime.fromisoformat(received_at.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_midnight(value: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(value, time.min, tzinfo=zone)


def _next_weekday(reference: date, weekday: int) -> date:
    return reference + timedelta(days=(weekday - reference.weekday()) % 7)


def _date_for_token(token: str, reference: date) -> date | None:
    if token == _TODAY:
        return reference
    if token == _TOMORROW:
        return reference + timedelta(days=1)
    weekday = _DAY_NAMES.get(token)
    return _next_weekday(reference, weekday) if weekday is not None else None


def _range_end(start: date, end: date, evening: bool, zone: ZoneInfo) -> datetime:
    if evening:
        return datetime.combine(end, _EVENING_END, tzinfo=zone)
    return _local_midnight(end + timedelta(days=1), zone)


def _resolve_relative_range(text: str, reference: date, zone: ZoneInfo) -> AvailabilityPeriod | None:
    match = _RANGE_PATTERN.search(text)
    if match is None:
        return None

    start = _date_for_token(match.group("start"), reference)
    end_token = match.group("end")
    if start is None:
        return None
    if end_token in _DAY_NAMES:
        days_after_start = (_DAY_NAMES[end_token] - start.weekday()) % 7
        if days_after_start == 0:
            days_after_start = 7
        end = start + timedelta(days=days_after_start)
    else:
        end = _date_for_token(end_token, reference)
        if end is None or end < start:
            return None

    return AvailabilityPeriod(
        start=_local_midnight(start, zone),
        end=_range_end(start, end, bool(match.group("evening")), zone),
    )


def _resolve_explicit_range(text: str, zone: ZoneInfo) -> AvailabilityPeriod | None:
    match = _EXPLICIT_RANGE_PATTERN.search(text)
    if match is not None:
        try:
            start = date.fromisoformat(match.group("start"))
            end = date.fromisoformat(match.group("end"))
        except ValueError:
            return None
        if end < start:
            return None
        return AvailabilityPeriod(
            start=_local_midnight(start, zone),
            end=_range_end(start, end, bool(match.group("evening")), zone),
        )

    dates = _EXPLICIT_DATE_PATTERN.findall(text)
    if len(dates) != 1:
        return None
    try:
        resolved = date.fromisoformat(dates[0])
    except ValueError:
        return None
    return AvailabilityPeriod(_local_midnight(resolved, zone), _local_midnight(resolved + timedelta(days=1), zone))


def resolve_availability_period(raw_text: str, received_at: str, timezone_name: str) -> AvailabilityPeriod | None:
    """Resolve an unambiguous Hebrew availability period from trusted receipt time.

    The resolver deliberately handles only the declared attendance vocabulary.
    Any expression outside that contract returns ``None`` so the normal
    event-data clarification path can ask the reporter instead of guessing.
    """

    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    try:
        reference = _parse_received_at(received_at).astimezone(ZoneInfo(timezone_name))
    except (TypeError, ValueError):
        return None

    normalized = " ".join(raw_text.split())
    period = _resolve_explicit_range(normalized, reference.tzinfo)
    if period is not None:
        return period

    period = _resolve_relative_range(normalized, reference.date(), reference.tzinfo)
    if period is not None:
        return period

    if _TODAY in normalized:
        start = reference.date()
    elif _TOMORROW in normalized:
        start = reference.date() + timedelta(days=1)
    else:
        match = _WEEKDAY_PATTERN.search(normalized)
        if match is None:
            return None
        start = _next_weekday(reference.date(), _DAY_NAMES[match.group("day")])

    return AvailabilityPeriod(_local_midnight(start, reference.tzinfo), _local_midnight(start + timedelta(days=1), reference.tzinfo))
