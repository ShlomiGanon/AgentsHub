"""Public, persistence-backed historical query and precedent interface."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from history.precedent import PrecedentMatch, find_precedents
from history.retrieval import retrieve_range
from history.time_utils import parse_timestamp, storage_timestamp


class HistoryQueryError(Exception):
    """A historical answer could not be produced from retrieved material."""


@dataclass(frozen=True)
class HistorySource:
    level: str
    period_start: str
    period_end: str
    source_id: str


@dataclass(frozen=True)
class HistoryAnswer:
    answer: str
    sources_used: tuple[HistorySource, ...]
    time_start: str | None
    time_end: str | None
    total_events_matched: int


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
]
