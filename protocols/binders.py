"""Shared direct-tool binder helpers.

Profile protocol modules keep their own `agent_name`, task text, and domain
descriptions. This module only normalizes timestamps and the attendance-tool
kwargs shape both profiles already persist through `record_attendance_response`.
"""

from datetime import datetime, timezone

from protocols.contracts import Step


def as_aware_iso(value: str) -> str:
    """A persisted event timestamp is stored without an explicit offset but is always UTC
    (config/environment.py's own timestamp convention) — agents/team_status_agent.py's
    `_aware_datetime` rejects a naive string outright, so make it explicit before handing it
    to a tool, the same way a real model call would when it reformats a timestamp itself."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def attendance_tool_kwargs(event: dict) -> tuple[dict, tuple[str, ...]]:
    """Build `record_attendance_response` kwargs from extracted event fields.

    Returns `(kwargs, required_event_fields)`. When dates are still missing for an
    unavailability report, `unavailable_days` is omitted and the required-fields tuple
    names what the event-data hold must collect.
    """

    absence_reason = (event.get("absence_reason") or "").strip()
    received_at = event.get("received_at") or ""
    kwargs = {
        "source_message_id": event.get("source_message_id") or "",
        "original_text": event.get("raw_text") or "",
        "received_at": as_aware_iso(received_at) if received_at else "",
    }
    if not absence_reason:
        kwargs.update(availability="available", reason="", unavailable_days=0)
        return kwargs, ()
    kwargs.update(availability="unavailable", reason=absence_reason)
    missing = tuple(name for name in ("availability_start", "availability_end") if not event.get(name))
    if missing:
        return kwargs, missing
    start = datetime.fromisoformat(event["availability_start"])
    end = datetime.fromisoformat(event["availability_end"])
    days = (end - start).total_seconds() / 86400
    kwargs["unavailable_days"] = max(1, int(days + 0.999999))
    return kwargs, ()


def bind_record_attendance_response(
    event: dict,
    *,
    agent_name: str,
    task_text: str,
) -> tuple[Step, ...]:
    """One `kind="direct_tool"` attendance step, with the calling profile's agent name."""

    kwargs, required = attendance_tool_kwargs(event)
    return (
        Step(
            agent_name=agent_name,
            task_text=task_text,
            allowed_tools=("record_attendance_response",),
            step_id="1",
            required_event_fields=required,
            kind="direct_tool",
            direct_tool_name="record_attendance_response",
            direct_tool_kwargs=kwargs,
        ),
    )
