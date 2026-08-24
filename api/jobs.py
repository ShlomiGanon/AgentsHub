"""GET /Job/<event_id> — job status and result retrieval (work_plan.md
§7.2).

The job ID *is* the event ID: `history.interface.record_initial_event`
already returns one synchronously, so §7.2's "job ID" is never a second,
separate identifier. Status is derived entirely from already-persisted
event/hold state, plus the one piece of in-process state
`orchestrator.queue.SerialEventQueue` tracks (which item, if any, is
currently being worked) — never a separate "jobs" table (§7.2's own rule:
"keep job state in the database rather than memory" — everything that
*is* state lives there; the queued/running distinction is the one thing
that is genuinely transient, not a decision worth persisting).

Reads a hold through `fetch_held_event` (§2.13) rather than the event's
own `clarification_held`/`approval_held` columns — those are permanent
historical markers ("this event went through a hold at some point") set
once and never cleared back to False on resolution, so they can't tell a
still-pending hold from an already-answered one. `fetch_held_event`'s own
`resolved` flag can.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import NotFoundError

if TYPE_CHECKING:
    from api.app import ApiContext


def job_status(ctx: "ApiContext", event_id: str) -> dict | None:
    event = ctx.deps.persistence.fetch_event(event_id)
    if event is None:
        return None

    if event["outcome"] is not None:
        body = {"event_id": event_id, "status": event["outcome"]}
        if event.get("insight_text") is not None:
            body["insight_text"] = event["insight_text"]
        if event["outcome"] == "failed" and event.get("outcome_failure_reason"):
            body["detail"] = event["outcome_failure_reason"]
        elif event["outcome"] == "closed_on_precedent" and event.get("precedent_closed_by_event_id"):
            body["detail"] = f"closed against resolved precedent '{event['precedent_closed_by_event_id']}'"
        return body

    approval_hold = ctx.deps.persistence.fetch_held_event("approval", event_id)
    if approval_hold is not None and not approval_hold["resolved"]:
        return {"event_id": event_id, "status": "held_for_approval", "reason": approval_hold["reason"]}

    clarification_hold = ctx.deps.persistence.fetch_held_event("clarification", event_id)
    if clarification_hold is not None and not clarification_hold["resolved"]:
        return {"event_id": event_id, "status": "held_for_clarification", "unresolved_field": clarification_hold["unresolved_field"]}

    processing = ctx.queue.currently_processing()
    if processing is not None and processing[0] == event_id:
        return {"event_id": event_id, "status": "running"}

    return {"event_id": event_id, "status": "queued"}


def build_jobs_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("jobs", __name__)

    @blueprint.route("/Job/<event_id>", methods=["GET"])
    def get_job(event_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        status = job_status(ctx, event_id)
        if status is None:
            raise NotFoundError(f"no such job '{event_id}'")

        return jsonify(status)

    return blueprint
