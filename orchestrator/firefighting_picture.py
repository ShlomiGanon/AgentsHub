"""FIRE run scoping for the existing multi-agent picture orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from messages import get_catalog
from persistence import EventSearchCriteria
from orchestrator.situational_picture import (
    DomainReport, RECENT_EVENTS_DOMAIN, SituationalPicture, build_situational_picture,
)


def _scenario_datetime(value: str, scenario_zone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=scenario_zone) if parsed.tzinfo is None else parsed


def fire_run_context(registry, persistence, *, sender_identity_filter=None, as_of_iso=""):
    """Read the active run boundary and its original events, without a calendar guess."""
    operations = registry.get("surveillance_agent").operations_store
    root = operations.get_incident()
    started = (root or {}).get("run_started_at")
    if not started:
        return None
    events = persistence.search_events(EventSearchCriteria(
        time_start=started, time_basis="received_at", order="newest", limit=500,
        sender_identity=sender_identity_filter,
    ))
    events = [event for event in events if event.get("simulation_context") == "FIRE_SIMULATION"]
    scenario_zone = ZoneInfo(registry.get("team_status_agent").timezone_name)
    if as_of_iso:
        cutoff = _scenario_datetime(as_of_iso, scenario_zone).astimezone(timezone.utc)
        events = [
            event for event in events
            if _scenario_datetime(
                event.get("occurred_at") or event["received_at"],
                scenario_zone if event.get("occurred_at") else timezone.utc,
            ).astimezone(timezone.utc) <= cutoff
        ]
    events.reverse()
    if not events:
        return None
    def event_clock(event):
        occurred = event.get("occurred_at")
        value = occurred or event["received_at"]
        zone = scenario_zone if occurred else timezone.utc
        return _scenario_datetime(value, zone).astimezone(timezone.utc)

    latest = max(events, key=event_clock)
    clock_value = latest.get("occurred_at") or latest["received_at"]
    clock_zone = scenario_zone if latest.get("occurred_at") else timezone.utc
    clock = _scenario_datetime(clock_value, clock_zone).isoformat()
    return {"run_started_at": started, "clock": clock, "events": events}


def format_fire_run_history(events, timezone_name="Asia/Jerusalem"):
    """Render the persisted reports for one already-scoped FIRE run, without event IDs."""
    scenario_zone = ZoneInfo(timezone_name)
    catalog = get_catalog("he")
    if not events:
        return catalog.text("fire.history.empty")
    lines = [catalog.text("fire.history.title")]
    for event in events:
        occurred = event.get("occurred_at")
        timestamp = event.get("received_at") or ""
        if occurred:
            parsed = _scenario_datetime(occurred, scenario_zone)
        elif timestamp:
            parsed = _scenario_datetime(timestamp, timezone.utc).astimezone(scenario_zone)
        else:
            parsed = None
        when = parsed.strftime("%d/%m %H:%M") if parsed else catalog.text("fire.history.unknown_time")
        source = event.get("sender_name") or event.get("sender_identity") or catalog.text("fire.history.unknown_source")
        report = str(event.get("raw_text") or "").strip()
        if report:
            lines.append(f"• {when} — {source}: {report}")
    return "\n".join(lines)


def build_fire_situational_picture(
    registry, request_text="", *, as_of_iso="", main_agent,
    history_query_service, protocol, caller_identity, persistence,
    sender_identity_filter=None, conversation_messages=(),
):
    """Use the existing planner, specialists and composer with current FIRE evidence."""
    catalog = get_catalog("he")
    run = fire_run_context(
        registry, persistence,
        sender_identity_filter=sender_identity_filter,
        as_of_iso=as_of_iso,
    )
    if run is None:
        raise LookupError(catalog.text("fire.context.run_required"))
    scenario_zone = ZoneInfo(registry.get("team_status_agent").timezone_name)
    now = _scenario_datetime(as_of_iso or run["clock"], scenario_zone)
    run_started = _scenario_datetime(run["run_started_at"], timezone.utc)
    evidence = []
    for event in run["events"]:
        occurred = event.get("occurred_at")
        if not occurred:
            continue
        event_time = _scenario_datetime(occurred, scenario_zone)
        if event_time > now.astimezone(timezone.utc):
            continue
        item = {key: event.get(key) for key in (
            "event_id", "raw_text", "occurred_at", "selected_protocol", "outcome",
        )}
        if item["outcome"] is None:
            item["outcome"] = "pending"
        elif item["outcome"] in {"succeeded", "failed", "uncertain", "declined", "closed_on_precedent"}:
            item["user_response"] = event.get("user_response")
            item["steps"] = event.get("steps")
        evidence.append(item)
    context = (
        "FIRE SIMULATION ONLY. This is the active run, not SEC or a readiness squad. "
        "Never rename FIRE members as SEC. If another profile is requested, explain that "
        "its data is unavailable here and ask which profile is intended. "
        "Use the scenario clock below, never the wall clock. Planned return, dispatch and "
        "arrival are distinct; only confirmed tool results establish actions. Missing is unknown. "
        "Analyse the current question, not a fixed report template. Prioritize the latest changes; "
        "give justified recommendations requiring commander decision. Do not execute recommendations. "
        "Do not connect geographically distinct reports without supporting evidence. "
        "Respond in concise natural Hebrew, plain text, without internal IDs or escaped Markdown. "
        f"Scenario clock: {now.isoformat()}. "
        f"Conversation references: {json.dumps(conversation_messages, ensure_ascii=False, default=str)}. "
        f"Current request: {request_text}"
    )
    operations = registry.get("surveillance_agent").operations_store
    run_event_ids = {event["event_id"] for event in run["events"]}
    force_states = {}
    for update in operations.list_updates():
        if update.get("update_kind") != "external_force" or update.get("event_id") not in run_event_ids:
            continue
        try:
            facts = json.loads(update.get("facts_json") or "{}")
            force = facts.get("external_force")
            update_time = _scenario_datetime(update["occurred_at"], scenario_zone)
        except (TypeError, ValueError, KeyError):
            continue
        current = force_states.get(force["force_id"]) if force else None
        if force and update_time <= now and (
            current is None or update_time >= _scenario_datetime(current["last_updated"], scenario_zone)
        ):
            force_states[force["force_id"]] = {**force, "last_updated": update["occurred_at"]}
    for force in operations.list_external_forces():
        update_time = _scenario_datetime(force["last_updated"], scenario_zone)
        current = force_states.get(force["force_id"])
        if update_time <= now and (
            current is None or update_time >= _scenario_datetime(current["last_updated"], scenario_zone)
        ):
            force_states[force["force_id"]] = force
    incidents = [
        row for row in operations.list_incidents()
        if _scenario_datetime(row["run_started_at"], timezone.utc) == run_started
        and _scenario_datetime(row["last_updated"], scenario_zone) <= now
        and (row["status"], row["area"], row["spread_status"], row["hazard_status"]) !=
            ("unknown", "unknown", "unknown", "unknown")
    ]
    history = DomainReport(
        RECENT_EVENTS_DOMAIN, request_text,
        json.dumps({
            "events": evidence,
            "external_forces": list(force_states.values()),
            "incidents": incidents,
        }, ensure_ascii=False, default=str), True,
    )
    picture = build_situational_picture(
        main_agent, protocol, registry, history_query_service, context,
        caller_identity=caller_identity, sender_identity_filter=sender_identity_filter,
        now=now, recent_events=history,
    )
    return picture
