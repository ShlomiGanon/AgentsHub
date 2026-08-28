"""Public, persistence-backed historical query and precedent interface."""

import json
import logging
from datetime import datetime, timedelta, timezone

from history.contracts import HistoryAnswer, HistoryQueryError, HistorySource, PrecedentMatch, RetrievedSource
from history.events import day_bounds, month_bounds, parse_timestamp, storage_timestamp, year_bounds
from tools import get_trace_id

logger = logging.getLogger(__name__)


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


def find_precedents(
    persistence,
    settings_store,
    target_event_id: str,
    classification: str,
    area: str,
    target_event_occurred_at: str,
) -> list[PrecedentMatch]:
    window_end = parse_timestamp(target_event_occurred_at) + timedelta(seconds=1)
    window_start = window_end - timedelta(days=settings_store.get_lookback_window_days())

    events_by_id = {}
    sources = retrieve_range(persistence, window_start, window_end, classification, area)
    for source in sources:
        if source.level == "raw_event":
            event = source.content
            if event["event_id"] != target_event_id:
                events_by_id[event["event_id"]] = event
            continue

        events = persistence.fetch_events_by_type_area_window(classification, area, source.period_start, source.period_end)
        for event in events:
            if event["event_id"] != target_event_id:
                events_by_id[event["event_id"]] = event

    matches = [
        PrecedentMatch(
            event_id=event["event_id"],
            classification=event["classification"],
            area=event["area"],
            occurred_at=event["occurred_at"],
            protocol_name=event.get("selected_protocol"),
            steps_summary=list(event.get("steps") or []),
            outcome=event.get("outcome"),
            resolved=event.get("outcome") in {"succeeded", "closed_on_precedent"},
        )
        for event in sorted(events_by_id.values(), key=lambda item: (item["occurred_at"], item["event_id"]), reverse=True)
    ]

    logger.debug(
        "precedent lookup",
        extra={
            "event": "precedent_lookup",
            "target_event_id": target_event_id,
            "classification": classification,
            "area": area,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "matched_event_ids": [match.event_id for match in matches],
            "trace_id": get_trace_id(),
        },
    )
    return matches


class HistoryQueryService:
    def __init__(self, persistence, history_agent, settings_store=None, clock=None):
        self._persistence = persistence
        self._history_agent = history_agent
        self._settings_store = settings_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _resolve_bounds(self, time_start: str | None, time_end: str | None) -> tuple[datetime, datetime]:
        end = parse_timestamp(time_end) if time_end is not None else self._clock()

        if time_start is not None:
            return parse_timestamp(time_start), end

        all_events = self._persistence.fetch_events_range("0001-01-01T00:00:00", storage_timestamp(end))
        if not all_events:
            return end - timedelta(days=1), end

        return min(parse_timestamp(event["occurred_at"]) for event in all_events), end

    def query(
        self,
        question: str,
        time_start: str | None = None,
        time_end: str | None = None,
        classification: str | None = None,
        area: str | None = None,
    ) -> HistoryAnswer:
        start, end = self._resolve_bounds(time_start, time_end)
        retrieved = retrieve_range(self._persistence, start, end, classification, area)

        context = [
            {
                "level": source.level,
                "period_start": source.period_start,
                "period_end": source.period_end,
                "source_id": source.source_id,
                "content": source.content,
                "matched_event_ids": source.matched_event_ids,
            }
            for source in retrieved
        ]
        prompt = (
            "Answer the question only from the supplied stored history context. State when the "
            "record is insufficient and preserve contradictions.\n"
            f"Question: {question}\nContext: {json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        result = self._history_agent.process(prompt, allowed_tools=[])
        if result.status != "success":
            raise HistoryQueryError(f"history agent could not answer: {result.text}")

        matched_ids = {
            event_id
            for source in retrieved
            for event_id in source.matched_event_ids
        }
        sources = tuple(
            HistorySource(
                level=source.level,
                period_start=source.period_start,
                period_end=source.period_end,
                source_id=source.source_id,
            )
            for source in retrieved
        )

        return HistoryAnswer(
            answer=result.text,
            sources_used=sources,
            time_start=storage_timestamp(start),
            time_end=storage_timestamp(end),
            total_events_matched=len(matched_ids),
        )

    def answer_most_recent_event(self, question: str) -> HistoryAnswer:
        """A narrow, direct-lookup path for "what is the last event"-shaped questions (orchestrator.question_flow's own direct-lookup classification decides when to call this instead of th..."""

        now = self._clock()
        all_events = self._persistence.fetch_events_range("0001-01-01T00:00:00", storage_timestamp(now))
        if not all_events:
            raise HistoryQueryError("no events have been recorded yet")

        most_recent = max(all_events, key=lambda event: parse_timestamp(event["occurred_at"]))

        prompt = (
            "Answer the question using only the one most recent event supplied below — the record has "
            "already been searched for you; do not ask for more context or claim none was given.\n"
            f"Question: {question}\nMost recent event: {json.dumps(most_recent, ensure_ascii=False, sort_keys=True, default=str)}"
        )
        result = self._history_agent.process(prompt, allowed_tools=[])
        if result.status != "success":
            raise HistoryQueryError(f"history agent could not answer: {result.text}")

        source = HistorySource(
            level="raw_event",
            period_start=most_recent["occurred_at"],
            period_end=most_recent["occurred_at"],
            source_id=most_recent["event_id"],
        )
        return HistoryAnswer(
            answer=result.text,
            sources_used=(source,),
            time_start=most_recent["occurred_at"],
            time_end=most_recent["occurred_at"],
            total_events_matched=1,
        )

    def search_precedents(
        self,
        target_event_id: str,
        classification: str,
        area: str,
        target_event_occurred_at: str,
    ) -> list[PrecedentMatch]:
        if self._settings_store is None:
            raise HistoryQueryError("precedent search requires a settings store")

        return find_precedents(
            self._persistence,
            self._settings_store,
            target_event_id,
            classification,
            area,
            target_event_occurred_at,
        )


__all__ = [
    "HistoryAnswer",
    "HistoryQueryError",
    "HistoryQueryService",
    "HistorySource",
    "PrecedentMatch",
    "RetrievedSource",
    "find_precedents",
    "retrieve_range",
]
