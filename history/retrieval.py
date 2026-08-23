"""Deterministic, non-overlapping history range planning."""

from dataclasses import dataclass
from datetime import datetime

from history.time_utils import day_bounds, month_bounds, storage_timestamp, year_bounds


@dataclass(frozen=True)
class RetrievedSource:
    level: str
    period_start: str
    period_end: str
    source_id: str
    content: object
    matched_event_ids: tuple[str, ...]


def _exact_summary(persistence, level: str, start: datetime, end: datetime) -> dict | None:
    start_text = storage_timestamp(start)
    end_text = storage_timestamp(end)
    for summary in persistence.fetch_summaries_range(level, start_text, end_text):
        if summary["period_start"] == start_text and summary["period_end"] == end_text:
            if summary.get("event_index") is not None:
                return summary
    return None


def _matching_ids(index: list[dict], classification: str | None, area: str | None) -> tuple[str, ...]:
    return tuple(
        item["event_id"]
        for item in index
        if (classification is None or item.get("classification") == classification)
        and (area is None or item.get("area") == area)
    )


def _summary_source(level: str, summary: dict, classification: str | None, area: str | None) -> RetrievedSource | None:
    matched_ids = _matching_ids(summary.get("event_index") or [], classification, area)
    if (classification is not None or area is not None) and not matched_ids:
        return None

    start = summary["period_start"]
    end = summary["period_end"]
    return RetrievedSource(
        level=level,
        period_start=start,
        period_end=end,
        source_id=f"{level}:{start}:{end}",
        content=summary["summary_text"],
        matched_event_ids=matched_ids,
    )


def retrieve_range(persistence, start: datetime, end: datetime, classification: str | None, area: str | None) -> list[RetrievedSource]:
    if end <= start:
        raise ValueError("time_end must be later than time_start")

    sources = []
    cursor = start

    while cursor < end:
        candidates = []
        year_start, year_end = year_bounds(cursor)
        month_start, month_end = month_bounds(cursor)
        day_start, day_end = day_bounds(cursor)

        if cursor == year_start and year_end <= end:
            candidates.append(("yearly", year_end))
        if cursor == month_start and month_end <= end:
            candidates.append(("monthly", month_end))
        if cursor == day_start and day_end <= end:
            candidates.append(("daily", day_end))

        used_summary = False
        for level, candidate_end in candidates:
            summary = _exact_summary(persistence, level, cursor, candidate_end)
            if summary is None:
                continue

            source = _summary_source(level, summary, classification, area)
            if source is not None:
                sources.append(source)
            cursor = candidate_end
            used_summary = True
            break

        if used_summary:
            continue

        raw_end = min(day_end, end)
        events = persistence.fetch_events_range(storage_timestamp(cursor), storage_timestamp(raw_end))
        for event in events:
            if classification is not None and event.get("classification") != classification:
                continue
            if area is not None and event.get("area") != area:
                continue
            sources.append(
                RetrievedSource(
                    level="raw_event",
                    period_start=event["occurred_at"],
                    period_end=event["occurred_at"],
                    source_id=event["event_id"],
                    content=event,
                    matched_event_ids=(event["event_id"],),
                )
            )
        cursor = raw_end

    return sources
