"""UTC period arithmetic shared by history internals."""

from datetime import date, datetime, time, timedelta, timezone


UTC = timezone.utc


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def storage_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def day_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(value.date(), time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def month_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime(value.year, value.month, 1, tzinfo=UTC)
    if value.month == 12:
        end = datetime(value.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(value.year, value.month + 1, 1, tzinfo=UTC)
    return start, end


def year_bounds(value: datetime) -> tuple[datetime, datetime]:
    return datetime(value.year, 1, 1, tzinfo=UTC), datetime(value.year + 1, 1, 1, tzinfo=UTC)


def add_month(value: datetime) -> datetime:
    return month_bounds(value)[1]


def iter_days(start: datetime, end: datetime):
    cursor = day_bounds(start)[0]
    while cursor < end:
        yield cursor, cursor + timedelta(days=1)
        cursor += timedelta(days=1)


def iter_months(start: datetime, end: datetime):
    cursor = month_bounds(start)[0]
    while cursor < end:
        next_cursor = add_month(cursor)
        yield cursor, next_cursor
        cursor = next_cursor


def iter_years(start: datetime, end: datetime):
    cursor = year_bounds(start)[0]
    while cursor < end:
        next_cursor = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        yield cursor, next_cursor
        cursor = next_cursor
