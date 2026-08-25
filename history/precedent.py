"""Summary-first, exact type/area precedent lookup."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from history.retrieval import retrieve_range
from history.time_utils import parse_timestamp
from tools.tracing import get_trace_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrecedentMatch:
    event_id: str
    classification: str
    area: str
    occurred_at: str
    protocol_name: str | None
    steps_summary: list[dict]
    outcome: str | None
    resolved: bool


def find_precedents(
    persistence,
    settings_store,
    target_event_id: str,
    classification: str,
    area: str,
    target_event_occurred_at: str,
) -> list[PrecedentMatch]:
    # +1 second, not the target's own instant, as the window's upper
    # bound: `occurred_at` is only ever stored at whole-second precision
    # (history.time_utils.storage_timestamp), so two events genuinely
    # milliseconds apart — exactly what a real burst produces — can share
    # an identical truncated timestamp. retrieve_range's underlying query
    # excludes anything not strictly less than the bound, so without this
    # widening, an event recorded in the *same second* as the target
    # would never be found as its precedent. This can never pull in a
    # genuinely later event: anything a full second past the target's own
    # timestamp still falls outside the widened bound. The target itself
    # is excluded separately, by event ID, not by this boundary.
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

        events = persistence.fetch_events_by_type_area_window(
            classification,
            area,
            source.period_start,
            source.period_end,
        )
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

    # DEBUG, not INFO: this is the search's internal detail — the exact
    # window boundaries and the raw candidate list before closure is
    # decided. The outcome an operator needs ("did anything match, did it
    # close the event") is logged separately, at INFO, by
    # orchestrator.flows.continue_from_risk_assessment's "precedent_closure"
    # event once closure is actually evaluated — never move that one.
    logger.debug(
        "precedent lookup",
        extra={
            "event": "precedent_lookup",
            "target_event_id": target_event_id,
            "classification": classification,
            "area": area,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "matched_event_ids": [m.event_id for m in matches],
            "trace_id": get_trace_id(),
        },
    )

    return matches
