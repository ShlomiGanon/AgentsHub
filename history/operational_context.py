"""A compact, deterministic digest of what was recently reported across every channel.

Every model decision used to see only the one message in front of it, so a follow-up in
another chat ("the casualty at the entrance", "the suspect on the roof", "there was no
shooting") could not be tied to the incident it belonged to. This digest is the shared
operational memory those decisions read: one short entry per recent event, built straight
from the event log with no model call, so it is cheap, stable, and never invents anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable, Mapping

from history.event_pipeline import parse_timestamp, storage_timestamp

DEFAULT_CONTEXT_HOURS = 12
DEFAULT_CONTEXT_LIMIT = 15
_MESSAGE_CHARS = 300
_REPLY_CHARS = 220

# Plain English status words for the model; the event log's raw outcome names are internal.
_STATUS_BY_OUTCOME = {
    None: "in progress",
    "queued": "in progress",
    "running": "in progress",
    "succeeded": "handled",
    "closed_on_precedent": "handled (matched an earlier identical report)",
    "handled_resource_unavailable": "handled, but a requested resource was unavailable",
    "waiting_for_event_data": "waiting for missing details from the reporter",
    "held_for_clarification": "waiting for clarification",
    "held_for_approval": "waiting for commander approval",
    "no_match_protocol": "recorded, no action defined",
    "failed": "failed",
    "uncertain": "outcome uncertain",
    "declined": "declined by commander",
}


def _clip(text: str | None, limit: int) -> str | None:
    """Trim long free text so one chatty message cannot crowd out the rest of the digest."""

    if not text:
        return None
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _local_label(value: str | None, zone: tzinfo, today) -> str | None:
    """HH:MM in the deployment's timezone, with the date only when it is not today."""

    if not value:
        return None
    try:
        moment = parse_timestamp(value).astimezone(zone)
    except (TypeError, ValueError):
        return None
    clock = f"{moment.hour:02d}:{moment.minute:02d}"
    return clock if moment.date() == today else f"{moment.day:02d}/{moment.month:02d} {clock}"


def _status(event: Mapping) -> str:
    """Readable lifecycle status, including a pending hold the outcome column does not show yet."""

    outcome = event.get("outcome")
    if outcome is None:
        if event.get("approval_held") and not event.get("approval_answered_at"):
            return _STATUS_BY_OUTCOME["held_for_approval"]
        if event.get("clarification_held") and not event.get("clarification_resolved_by"):
            return _STATUS_BY_OUTCOME["held_for_clarification"]
    return _STATUS_BY_OUTCOME.get(outcome, str(outcome))


def operational_context_entries(
    persistence,
    *,
    now: datetime | None = None,
    zone: tzinfo = timezone.utc,
    area_labels: Mapping[str, str] | None = None,
    exclude_event_ids: Iterable[str] = (),
    hours: int = DEFAULT_CONTEXT_HOURS,
    limit: int = DEFAULT_CONTEXT_LIMIT,
    sender_identity_filter: str | None = None,
) -> list[dict]:
    """The newest `limit` events received in the last `hours` hours, oldest first.

    Each entry carries a stable `ref` (the event id) so a model can point back at it when a
    new message continues, corrects, or closes that event, plus the `incident` it belongs to.
    `sender_identity_filter` keeps a viewer to their own events, the same rule history applies.
    """

    now = now or datetime.now(timezone.utc)
    labels = dict(area_labels or {})
    excluded = set(exclude_event_ids)
    start = storage_timestamp(now - timedelta(hours=hours))
    end = storage_timestamp(now + timedelta(minutes=1))
    try:
        events = persistence.fetch_events_range(start, end)
    except Exception:
        return []
    events = [
        event for event in events
        if event.get("event_id") not in excluded
        and (sender_identity_filter is None or event.get("sender_identity") == sender_identity_filter)
    ]
    events.sort(key=lambda event: event.get("received_at") or "")
    events = events[-limit:]
    if not events:
        return []

    group_labels: dict[str, str] = {}
    try:
        for group in persistence.list_groups():
            group_labels[str(group.get("chat_id"))] = group.get("label") or str(group.get("chat_id"))
    except Exception:
        pass
    names: dict[str, str] = {}
    today = now.astimezone(zone).date()

    entries = []
    for event in events:
        sender = str(event.get("sender_identity") or "")
        if sender not in names:
            try:
                user = persistence.read_user(sender) or {}
            except Exception:
                user = {}
            names[sender] = (user.get("full_name") or "").strip() or sender
        chat_id = event.get("telegram_chat_id")
        if chat_id and str(chat_id) in group_labels:
            channel = group_labels[str(chat_id)]
        elif event.get("telegram_chat_type") == "private":
            channel = "private chat with the system"
        else:
            channel = event.get("source") or "unknown"
        area = event.get("area")
        entry = {
            "ref": event.get("event_id"),
            "incident": event.get("incident_id") or event.get("event_id"),
            "time": _local_label(event.get("received_at"), zone, today),
            "channel": channel,
            "from": names[sender],
            "from_role": "commander" if event.get("sender_permission_level") == "commander" else None,
            "message": _clip(event.get("raw_text"), _MESSAGE_CHARS),
            "type": event.get("classification"),
            "area": f"{area} ({labels[area]})" if area and labels.get(area) else area,
            "status": _status(event),
            "retracted_as_false": bool(event.get("retracted")) or None,
            "corrects": event.get("corrects_event_id"),
            "system_reply": _clip(event.get("report_text"), _REPLY_CHARS),
        }
        entries.append({key: value for key, value in entry.items() if value not in (None, "")})
    return entries
