"""Public, persistence-backed historical query and precedent interface."""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from history.contracts import (
    HistoryAnswer,
    HistoryQueryError,
    HistoryQuerySpec,
    HistorySearchResult,
    HistorySource,
    PrecedentMatch,
    RetrievedSource,
    SemanticEventView,
)
from history.event_pipeline import day_bounds, month_bounds, parse_timestamp, storage_timestamp, year_bounds
from history.field_catalog import FIELD_BY_KEY, FIELD_MEANINGS_GLOSSARY
from persistence import EventSearchCriteria, PersistenceError
from tools import get_trace_id

logger = logging.getLogger(__name__)

_HISTORY_OUTCOMES = {
    "queued",
    "running",
    "held_for_clarification",
    "held_for_approval",
    "succeeded",
    "failed",
    "uncertain",
    "closed_on_precedent",
    "declined",
    "no_match_protocol",
}
_RISK_LEVELS = {"high", "low"}
_MAX_HISTORY_RESULTS = 100


def _build_semantic_event_view(event: dict) -> SemanticEventView:
    """Filter one raw persistence record down to its narrative-category fields
    only (history/field_catalog.py) — internal plumbing (trace_id,
    conversation_id, deadline_at, ingestion identity) is never included, for
    any caller, and a field is omitted entirely when its value is None rather
    than shown as a false empty fact."""

    fields: list[tuple[str, object]] = []
    for definition in FIELD_BY_KEY.values():
        if definition.category != "narrative" or definition.key in ("event_id", "steps"):
            continue
        if definition.key not in event:
            continue
        value = event[definition.key]
        if value is None:
            continue
        fields.append((definition.label, value))

    return SemanticEventView(
        event_id=event["event_id"],
        fields=tuple(fields),
        steps=tuple(event.get("steps") or ()),
    )


def _semantic_view_payload(view: SemanticEventView) -> dict:
    payload = {FIELD_BY_KEY["event_id"].label: view.event_id, **dict(view.fields)}
    if view.steps:
        payload[FIELD_BY_KEY["steps"].label] = view.steps
    return payload


def _history_agent_prompt(instruction: str, question: str, views: list[SemanticEventView]) -> str:
    """Build a History Agent prompt from already-filtered `SemanticEventView`s only —
    never a raw persistence record. Includes the field glossary so the agent can
    faithfully explain what a present field means without inventing semantics."""

    events_payload = [_semantic_view_payload(view) for view in views]
    return (
        f"{instruction}\n"
        "Every field below is already filtered to what you may discuss for this caller — treat any field not "
        "present as not available, never guess or infer it. Field meanings, for reference if asked what one means:\n"
        f"{json.dumps(FIELD_MEANINGS_GLOSSARY, ensure_ascii=False, sort_keys=True)}\n"
        f"Question: {question}\n"
        f"Events: {json.dumps(events_payload, ensure_ascii=False, sort_keys=True, default=str)}"
    )


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

            history_source = _summary_source(level, summary, classification, area)
            if history_source is not None:
                sources.append(history_source)
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
    def __init__(
        self,
        persistence,
        history_agent,
        settings_store=None,
        clock=None,
        *,
        classifications: tuple[str, ...] | None = None,
        areas: tuple[str, ...] | None = None,
        protocol_names: tuple[str, ...] | None = None,
        timezone_name: str = "UTC",
    ):
        self._persistence = persistence
        self._history_agent = history_agent
        self._settings_store = settings_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._classifications = frozenset(classifications) if classifications is not None else None
        self._areas = frozenset(areas) if areas is not None else None
        self._protocol_names = frozenset(protocol_names) if protocol_names is not None else None
        try:
            self._timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise HistoryQueryError(f"unknown history query timezone: {timezone_name!r}") from exc
        self._timezone_name = timezone_name

    def planning_context(self) -> dict:
        """Return only the bounded vocabulary needed to plan a history query."""

        current_time = self._clock()
        return {
            "current_time_utc": storage_timestamp(current_time),
            "current_time_local": current_time.astimezone(self._timezone).isoformat(timespec="seconds"),
            "timezone": self._timezone_name,
            "classifications": sorted(self._classifications) if self._classifications is not None else [],
            "areas": sorted(self._areas) if self._areas is not None else [],
            "protocol_names": sorted(self._protocol_names) if self._protocol_names is not None else [],
            "outcomes": sorted(_HISTORY_OUTCOMES),
            "risk_levels": sorted(_RISK_LEVELS),
        }

    def _resolve_bounds(self, time_start: str | None, time_end: str | None) -> tuple[datetime, datetime]:
        end = parse_timestamp(time_end) if time_end is not None else self._clock()

        if time_start is not None:
            return parse_timestamp(time_start), end

        earliest = self._persistence.fetch_event_time_boundary(
            EventSearchCriteria(time_end=storage_timestamp(end)), latest=False
        )
        if earliest is None:
            return end - timedelta(days=1), end

        return parse_timestamp(earliest), end

    @staticmethod
    def _require_known_values(label: str, values: tuple[str, ...], allowed: frozenset[str] | set[str] | None) -> None:
        if allowed is None:
            return
        unknown = sorted(set(values) - set(allowed))
        if unknown:
            raise HistoryQueryError(f"unknown {label}: {', '.join(unknown)}")

    def _validate_spec(self, spec: HistoryQuerySpec) -> HistoryQuerySpec:
        if not 1 <= spec.limit <= _MAX_HISTORY_RESULTS:
            raise HistoryQueryError(f"history query limit must be between 1 and {_MAX_HISTORY_RESULTS}")

        self._require_known_values("classification", spec.classifications, self._classifications)
        self._require_known_values("area", spec.areas, self._areas)
        self._require_known_values("protocol", spec.protocol_names, self._protocol_names)
        self._require_known_values("outcome", spec.outcomes, _HISTORY_OUTCOMES)
        self._require_known_values("risk level", spec.risk_levels, _RISK_LEVELS)

        start = parse_timestamp(spec.time_start) if spec.time_start is not None else None
        end = parse_timestamp(spec.time_end) if spec.time_end is not None else self._clock()
        if start is not None and end <= start:
            raise HistoryQueryError("history query time_end must be later than time_start")
        if spec.operation == "event_details" and not spec.event_ids:
            raise HistoryQueryError("event_details requires at least one event_id")
        if spec.operation in {"aggregate", "compare"} and spec.group_by == "none":
            raise HistoryQueryError(f"{spec.operation} requires a group_by field")

        return HistoryQuerySpec(
            operation=spec.operation,
            time_start=storage_timestamp(start) if start is not None else None,
            time_end=storage_timestamp(end),
            time_basis=spec.time_basis,
            classifications=tuple(dict.fromkeys(spec.classifications)),
            areas=tuple(dict.fromkeys(spec.areas)),
            outcomes=tuple(dict.fromkeys(spec.outcomes)),
            protocol_names=tuple(dict.fromkeys(spec.protocol_names)),
            event_ids=tuple(dict.fromkeys(spec.event_ids)),
            risk_levels=tuple(dict.fromkeys(spec.risk_levels)),
            order="newest" if spec.operation == "latest" else spec.order,
            group_by=spec.group_by,
            limit=spec.limit,
        )

    @staticmethod
    def _criteria(spec: HistoryQuerySpec, *, limit: int | None = None, sender_identity: str | None = None) -> EventSearchCriteria:
        return EventSearchCriteria(
            time_start=spec.time_start,
            time_end=spec.time_end,
            time_basis=spec.time_basis,
            classifications=spec.classifications,
            areas=spec.areas,
            outcomes=spec.outcomes,
            protocol_names=spec.protocol_names,
            event_ids=spec.event_ids,
            risk_levels=spec.risk_levels,
            sender_identity=sender_identity,
            order=spec.order,
            limit=limit if limit is not None else spec.limit,
        )

    @staticmethod
    def _sources_for_events(events: tuple[dict, ...]) -> tuple[HistorySource, ...]:
        return tuple(
            HistorySource(
                level="raw_event",
                period_start=event.get("occurred_at") or event["received_at"],
                period_end=event.get("occurred_at") or event["received_at"],
                source_id=event["event_id"],
            )
            for event in events
        )

    def query_spec(self, question: str, spec: HistoryQuerySpec, *, sender_identity_filter: str | None = None) -> HistoryAnswer:
        """`sender_identity_filter` restricts every count/search/aggregate this call performs to events submitted
        by exactly this identity — the ownership scoping a viewer's `ask_question` operation requires
        (docs/Next_Plan.md §5 decision record). `None` (a commander, or an already-unrestricted caller) applies
        no such restriction. This is the single enforcement point: every operation below shares this criteria."""

        started_at = time.perf_counter()
        normalized = self._validate_spec(spec)
        criteria = self._criteria(normalized, sender_identity=sender_identity_filter)

        def _finish(answer: HistoryAnswer) -> HistoryAnswer:
            logger.info(
                "history query completed",
                extra={
                    "event": "history_query",
                    "operation": normalized.operation,
                    "time_start": normalized.time_start,
                    "time_end": normalized.time_end,
                    "time_basis": normalized.time_basis,
                    "classifications": list(normalized.classifications),
                    "areas": list(normalized.areas),
                    "outcomes": list(normalized.outcomes),
                    "protocol_names": list(normalized.protocol_names),
                    "total_events_matched": answer.total_events_matched,
                    "truncated": answer.truncated,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    "trace_id": get_trace_id(),
                },
            )
            return answer

        try:
            total_count = self._persistence.count_events(criteria)

            if normalized.operation == "count":
                answer = f"{total_count} matching event{'s' if total_count != 1 else ''}."
                return _finish(HistoryAnswer(answer, (), normalized.time_start, normalized.time_end, total_count, normalized, False))

            if normalized.operation in {"aggregate", "compare"}:
                if normalized.group_by == "none":
                    raise HistoryQueryError(f"{normalized.operation} requires a group_by field")
                aggregates = tuple(self._persistence.aggregate_events(criteria, normalized.group_by))
                answer = "; ".join(f"{item['group'] or '(unresolved)'}: {item['count']}" for item in aggregates)
                return _finish(HistoryAnswer(answer or "No matching events.", (), normalized.time_start, normalized.time_end, total_count, normalized, False))

            query_limit = 1 if normalized.operation == "latest" else normalized.limit
            events = tuple(self._persistence.search_events(
                self._criteria(normalized, limit=query_limit, sender_identity=sender_identity_filter)
            ))
        except PersistenceError as exc:
            raise HistoryQueryError(str(exc)) from exc

        truncated = total_count > len(events)
        sources = self._sources_for_events(events)

        if not events:
            raise HistoryQueryError("no stored events match the requested history filters")

        views = [_build_semantic_event_view(event) for event in events]

        if normalized.operation == "latest":
            prompt = _history_agent_prompt(
                "Describe this one most recent stored event naturally, in one or two sentences. Always state its "
                "Event ID explicitly, exactly as given, so it can be referenced again. State plainly when a fact "
                "is missing rather than inventing it.",
                question, views,
            )
            agent_result = self._history_agent.process(prompt, allowed_tools=[])
            if agent_result.status != "success":
                raise HistoryQueryError(f"history agent could not answer: {agent_result.text}")
            return _finish(HistoryAnswer(agent_result.text, sources, normalized.time_start, normalized.time_end, total_count, normalized, False))

        if normalized.operation == "event_details" and len(events) == 1:
            context_instruction = (
                "Answer only from the one supplied stored event. Explain the fields relevant to the question "
                "faithfully, distinguish a missing value from a false one, and always state the Event ID."
            )
        elif normalized.operation == "list":
            prompt = _history_agent_prompt(
                "List these stored events naturally. Give each one its own short entry, numbered in the order "
                "given, and always state that event's own Event ID explicitly within its entry, so any one of "
                "them can be referenced again later by number or by ID. State plainly when a fact is missing "
                "rather than inventing it.",
                question, views,
            )
            agent_result = self._history_agent.process(prompt, allowed_tools=[])
            if agent_result.status != "success":
                raise HistoryQueryError(f"history agent could not answer: {agent_result.text}")
            answer = agent_result.text
            if truncated:
                answer += f"\n\nShowing {len(events)} of {total_count} matching events."
            return _finish(HistoryAnswer(answer, sources, normalized.time_start, normalized.time_end, total_count, normalized, truncated))
        else:
            context_instruction = (
                "Answer only from the supplied, database-filtered history. Preserve contradictions, state when "
                "the records are insufficient, do not claim that omitted records were searched, and always state "
                "each discussed event's Event ID explicitly."
            )

        prompt = _history_agent_prompt(context_instruction, question, views)
        agent_result = self._history_agent.process(prompt, allowed_tools=[])
        if agent_result.status != "success":
            raise HistoryQueryError(f"history agent could not answer: {agent_result.text}")

        return _finish(HistoryAnswer(
            answer=agent_result.text,
            sources_used=sources,
            time_start=normalized.time_start,
            time_end=normalized.time_end,
            total_events_matched=total_count,
            applied_query=normalized,
            truncated=truncated,
        ))

    def query(
        self,
        question: str,
        time_start: str | None = None,
        time_end: str | None = None,
        classification: str | None = None,
        area: str | None = None,
        *,
        sender_identity_filter: str | None = None,
    ) -> HistoryAnswer:
        start, end = self._resolve_bounds(time_start, time_end)
        retrieved = retrieve_range(self._persistence, start, end, classification, area)

        if sender_identity_filter is not None:
            # A rolled-up period summary (`level` daily/monthly/yearly) is prose
            # covering potentially many senders' events with no way to redact
            # just one sender's share of it — it can never be safely shown to an
            # ownership-scoped caller, so it is dropped entirely rather than
            # risked. Only raw, per-event sources the caller actually owns
            # remain (docs/Next_Plan.md §5 decision record).
            retrieved = [
                source for source in retrieved
                if source.level == "raw_event" and source.content.get("sender_identity") == sender_identity_filter
            ]

        context = [
            {
                "level": source.level,
                "period_start": source.period_start,
                "period_end": source.period_end,
                "source_id": source.source_id,
                "content": (
                    _semantic_view_payload(_build_semantic_event_view(source.content))
                    if source.level == "raw_event"
                    else source.content
                ),
                "matched_event_ids": source.matched_event_ids,
            }
            for source in retrieved
        ]
        prompt = (
            "Answer the question only from the supplied stored history context. State when the "
            "record is insufficient and preserve contradictions. If asked what a field means, use only the "
            "meanings given here, never invented database semantics:\n"
            f"{json.dumps(FIELD_MEANINGS_GLOSSARY, ensure_ascii=False, sort_keys=True)}\n"
            f"Question: {question}\nContext: {json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        agent_result = self._history_agent.process(prompt, allowed_tools=[])
        if agent_result.status != "success":
            raise HistoryQueryError(f"history agent could not answer: {agent_result.text}")

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
            answer=agent_result.text,
            sources_used=sources,
            time_start=storage_timestamp(start),
            time_end=storage_timestamp(end),
            total_events_matched=len(matched_ids),
        )

    def answer_most_recent_event(self, question: str, *, sender_identity_filter: str | None = None) -> HistoryAnswer:
        """A narrow, direct-lookup path for "what is the last event"-shaped questions (orchestrator.question_flow's own direct-lookup classification decides when to call this instead of th...

        `sender_identity_filter` restricts the lookup to the caller's own most
        recent event (docs/Next_Plan.md §5 decision record); `None` (a
        commander) applies no restriction."""

        now = self._clock()
        criteria = EventSearchCriteria(
            time_end=storage_timestamp(now), sender_identity=sender_identity_filter, order="newest", limit=1
        )
        events = self._persistence.search_events(criteria)
        if not events:
            raise HistoryQueryError("no events have been recorded yet")

        most_recent = events[0]
        view = _build_semantic_event_view(most_recent)

        prompt = _history_agent_prompt(
            "Answer the question using only the one most recent event supplied below — the record has already "
            "been searched for you; do not ask for more context or claim none was given. Always state its "
            "Event ID explicitly.",
            question, [view],
        )
        agent_result = self._history_agent.process(prompt, allowed_tools=[])
        if agent_result.status != "success":
            raise HistoryQueryError(f"history agent could not answer: {agent_result.text}")

        history_source = HistorySource(
            level="raw_event",
            period_start=most_recent["occurred_at"],
            period_end=most_recent["occurred_at"],
            source_id=most_recent["event_id"],
        )
        return HistoryAnswer(
            answer=agent_result.text,
            sources_used=(history_source,),
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
