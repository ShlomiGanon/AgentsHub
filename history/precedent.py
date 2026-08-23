"""Summary-first, exact type/area precedent lookup."""

from dataclasses import dataclass
from datetime import timedelta

from history.retrieval import retrieve_range
from history.time_utils import parse_timestamp


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
    window_end = parse_timestamp(target_event_occurred_at)
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

    return [
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
