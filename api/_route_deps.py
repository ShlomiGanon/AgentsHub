"""Shared helpers for JSON route modules in this package."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from history import storage_timestamp
from protocols import CriticalityLevel, Protocol

from api.request_boundary import InvalidInputError

if TYPE_CHECKING:
    from api.app import ApiContext

_STORE_KEYS_BY_AGENT = {
    "roster_agent": "store:roster",
    "team_status_agent": "store:roster",
    "surveillance_agent": "store:cameras",
    "neighboring_forces_agent": "store:forces",
}


def utc_now_storage() -> str:
    """Return the current UTC instant in persistence storage format."""

    return storage_timestamp(datetime.now(timezone.utc))


# Historical name used by the route modules and tests.
_now = utc_now_storage


def work_concurrency_keys(sender_identity: str, scoped_agent: str | None = None) -> tuple[str, ...]:
    """Queue keys that serialize one sender (and optional specialist store) at a time."""

    keys = [f"sender:{sender_identity}"]
    store_key = _STORE_KEYS_BY_AGENT.get(scoped_agent or "")
    if store_key:
        keys.append(store_key)
    return tuple(keys)


_work_concurrency_keys = work_concurrency_keys


def protocol_to_dict(protocol: Protocol) -> dict:
    """Serialize a protocol object to the public JSON shape."""

    return {
        "name": protocol.name,
        "description": protocol.description,
        "participating_agents": list(protocol.participating_agents),
        "approved_tools": list(protocol.approved_tools),
        "expected_success_output": protocol.expected_success_output,
        "criticality": protocol.criticality.name.lower(),
        "approval_flag": protocol.approval_flag,
    }


def protocol_from_body(request_payload: dict, name_override: str | None = None) -> Protocol:
    """Build a Protocol from a JSON body, or raise InvalidInputError."""

    try:
        return Protocol(
            name=name_override if name_override is not None else request_payload["name"],
            description=request_payload["description"],
            participating_agents=tuple(request_payload["participating_agents"]),
            approved_tools=tuple(request_payload["approved_tools"]),
            expected_success_output=request_payload["expected_success_output"],
            criticality=CriticalityLevel[str(request_payload["criticality"]).upper()],
            approval_flag=request_payload["approval_flag"],
        )
    except KeyError as exc:
        from messages import get_current_catalog
        raise InvalidInputError(
            get_current_catalog().text("api.missing_required_field", field=exc.args[0]),
            field=str(exc.args[0]),
        ) from exc
    except (TypeError, AttributeError) as exc:
        from messages import get_current_catalog
        raise InvalidInputError(
            get_current_catalog().text("api.malformed_protocol", reason=exc)
        ) from exc


_protocol_from_body = protocol_from_body


def steps_completed(event: dict) -> list[str]:
    """Succeeded step labels derived from persisted event steps, in order."""

    return [
        f"{step['agent_name']}: {step['result_text']}"
        for step in event.get("steps", [])
        if step.get("status") == "succeeded" and step.get("result_text") is not None
    ]


_steps_completed = steps_completed


def failed_step_agent_name(event: dict) -> str | None:
    """The specialist whose step failed, or None if every persisted step succeeded."""

    for step in event.get("steps", []):
        if step.get("status") == "failed":
            return step["agent_name"]
    return None


_failed_step_agent_name = failed_step_agent_name


def job_status(ctx: "ApiContext", event_id: str) -> dict | None:
    """Public job payload for one event: outcome, hold, running, or queued."""

    event = ctx.deps.persistence.fetch_event(event_id)
    if event is None:
        return None

    event_trace_id = event.get("trace_id") if event is not None else None

    if event["outcome"] is not None:
        response_payload = {"event_id": event_id, "status": event["outcome"], "trace_id": event_trace_id}
        if event.get("insight_text") is not None:
            response_payload["insight_text"] = event["insight_text"]
        if event.get("report_text"):
            response_payload["report_text"] = event["report_text"]

        completed = steps_completed(event)
        if completed:
            response_payload["steps_completed"] = completed

        if event["outcome"] == "failed":
            if event.get("outcome_failure_reason"):
                response_payload["detail"] = event["outcome_failure_reason"]
            failed_agent = failed_step_agent_name(event)
            if failed_agent is not None:
                response_payload["failed_step_agent_name"] = failed_agent
        elif event["outcome"] == "closed_on_precedent" and event.get("precedent_closed_by_event_id"):
            response_payload["detail"] = f"closed against resolved precedent '{event['precedent_closed_by_event_id']}'"
        elif event["outcome"] == "no_match_protocol" and event.get("outcome_failure_reason"):
            response_payload["detail"] = event["outcome_failure_reason"]

        return response_payload

    approval_hold = ctx.deps.persistence.fetch_held_event("approval", event_id)
    if approval_hold is not None and not approval_hold["resolved"]:
        return {"event_id": event_id, "status": "held_for_approval", "reason": approval_hold["reason"], "trace_id": event_trace_id}

    clarification_hold = ctx.deps.persistence.fetch_held_event("clarification", event_id)
    if clarification_hold is not None and not clarification_hold["resolved"]:
        return {"event_id": event_id, "status": "held_for_clarification", "unresolved_field": clarification_hold["unresolved_field"], "trace_id": event_trace_id}

    event_data_hold = ctx.deps.persistence.fetch_held_event("event_data", event_id)
    if event_data_hold is not None and not event_data_hold["resolved"]:
        return {
            "event_id": event_id,
            "status": "waiting_for_event_data",
            "missing_fields": event_data_hold.get("missing_fields", []),
            "question": event_data_hold.get("question", ""),
            "steps_completed": steps_completed(event),
            "trace_id": event_trace_id,
        }

    processing = ctx.queue.currently_processing()
    if processing is not None and processing[0] == event_id:
        return {"event_id": event_id, "status": "running", "trace_id": event_trace_id}

    return {"event_id": event_id, "status": "queued", "trace_id": event_trace_id}
