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


def _steps_completed(event: dict) -> list[str]:
    """Every step that actually produced a result, in order — derivable
    entirely from `event["steps"]` (§2.3's `event_steps` table, already
    attached by `fetch_event`): a step that failed is persisted with
    `result_text=None` (`protocols.executor.StepOutcome`'s own failure
    shape), so filtering those out leaves exactly the completed steps. No
    new column or tracking was needed (§7.12).
    """

    return [f"{step['agent_name']}: {step['result_text']}" for step in event.get("steps", []) if step.get("result_text") is not None]


def _failed_step_agent_name(event: dict) -> str | None:
    """The agent whose step has no result — execution stops at the first
    failing step (`protocols.executor.execute_steps`), so at most one
    persisted step ever has `result_text=None`, and it's always the last
    one recorded.
    """

    for step in event.get("steps", []):
        if step.get("result_text") is None:
            return step["agent_name"]
    return None


def job_status(ctx: "ApiContext", event_id: str) -> dict | None:
    event = ctx.deps.persistence.fetch_event(event_id)
    if event is None:
        return None

    if event["outcome"] is not None:
        body = {"event_id": event_id, "status": event["outcome"]}
        if event.get("insight_text") is not None:
            body["insight_text"] = event["insight_text"]

        steps_completed = _steps_completed(event)
        if steps_completed:
            body["steps_completed"] = steps_completed

        if event["outcome"] == "failed":
            if event.get("outcome_failure_reason"):
                body["detail"] = event["outcome_failure_reason"]
            failed_step_agent_name = _failed_step_agent_name(event)
            if failed_step_agent_name is not None:
                body["failed_step_agent_name"] = failed_step_agent_name
        elif event["outcome"] == "closed_on_precedent" and event.get("precedent_closed_by_event_id"):
            body["detail"] = f"closed against resolved precedent '{event['precedent_closed_by_event_id']}'"
        elif event["outcome"] == "no_match_protocol" and event.get("outcome_failure_reason"):
            body["detail"] = event["outcome_failure_reason"]

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

"""POST /Approve/<event_id> and POST /Clarify/<event_id> (work_plan.md
§7.11).

Looks up a hold by event ID — the API surface matches what the bot and
any external caller actually have on hand (the event ID a report,
request, or message was already given back, never the orchestrator's
internal hold ID) — translating to a hold ID only at this one boundary,
via §2.13's `fetch_held_event`. That same lookup is what reports "already
resolved by X at T" for a second commander answering an already-handled
hold, rather than a generic not-found (§7.11's own requirement).

`POST /Clarify` and the approved (or candidate-selected) branch of
`POST /Approve` both queue a continuation — resuming is itself a full
run, exactly what §7.2 exists to avoid blocking a request on. The
rejected branch of `POST /Approve` stays fully synchronous: declining is
genuinely final, with nothing left to continue, so there is nothing to
queue.

`decision` accepts three shapes: `"approved"`, `"rejected"`, or — for the
ambiguous-selection case (§6.4/§6.7) — a candidate protocol name. This
layer does no branching on which shape it is beyond routing to the right
parameter; `decision` is passed straight through to the widened
`orchestrator.holds.answer_approval_hold`, which is the one place that
knows what a candidate name means. Keeping the API a thin wrapper here is
deliberate — see that function's own docstring.

`require` gates every write here before `orchestrator.holds
.answer_clarification_hold`/`answer_approval_hold` even run — those
functions' own internal `is_permitted` check still runs underneath, as
defense-in-depth for any direct, non-API caller, not as a second inline
check duplicating this one.

Each of these gets its own fresh trace ID (§1.8) for the resumption's own
handling — it does not inherit or continue the original event's ingestion
trace ID, which is not persisted anywhere a resumption, possibly arriving
much later, could read it back from. See `api/ingestion.py`'s own docstring
for why a queued continuation threads the ID explicitly rather than
relying on contextvar propagation across the queue's worker thread, and
for why the synchronous portion sets it via `tools.tracing.set_trace_id`
rather than a `with trace_context(...)` block that would reset it before
werkzeug's own request-log line gets written.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import ConflictError, InvalidInputError, NotFoundError
from orchestrator.flows import continue_after_approval, continue_after_clarification, decline, resolve_approval, resolve_clarification
from tools.tracing import new_trace_id, set_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext


def _pending_hold_or_raise(ctx: "ApiContext", kind: str, event_id: str) -> dict:
    hold = ctx.deps.persistence.fetch_held_event(kind, event_id)
    if hold is None:
        raise NotFoundError(f"no {kind} hold was ever created against event '{event_id}'")
    if hold["resolved"]:
        raise ConflictError(f"already resolved by '{hold['resolved_by']}' at {hold['resolved_at']}")
    return hold


def build_holds_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("holds", __name__)

    @blueprint.route("/Clarify/<event_id>", methods=["POST"])
    def post_clarify(event_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "resolve_hold")
        identity = request.headers.get("X-Identity")

        body = request.get_json(silent=True) or {}
        classification = body.get("classification")
        if not classification:
            raise InvalidInputError("'classification' is required", field="classification")

        trace_id = new_trace_id()
        set_trace_id(trace_id)

        hold = _pending_hold_or_raise(ctx, "clarification", event_id)

        answer = resolve_clarification(ctx.deps, hold["hold_id"], identity, level, classification)
        if answer.status == "invalid_classification":
            raise InvalidInputError(answer.message, field="classification")
        if answer.status != "resolved":
            # A hold resolved by someone else between the check above
            # and this call — a narrow race; not_found is the accurate
            # status, reported generically rather than re-querying for
            # who/when.
            raise InvalidInputError(answer.message)

        def _work() -> None:
            with trace_context(trace_id):
                continue_after_clarification(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit((event_id, _work))
        return jsonify({"event_id": event_id, "status": "queued"}), 202

    @blueprint.route("/Approve/<event_id>", methods=["POST"])
    def post_approve(event_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "approve_run")
        identity = request.headers.get("X-Identity")

        body = request.get_json(silent=True) or {}
        decision = body.get("decision")
        if not decision:
            raise InvalidInputError("'decision' is required — 'approved', 'rejected', or a candidate protocol name", field="decision")

        trace_id = new_trace_id()
        set_trace_id(trace_id)

        hold = _pending_hold_or_raise(ctx, "approval", event_id)

        answer = resolve_approval(ctx.deps, hold["hold_id"], identity, level, decision)
        if answer.status == "invalid_candidate":
            raise InvalidInputError(answer.message, field="decision")
        if answer.status not in ("approved", "rejected"):
            # Same narrow race as the clarify path above.
            raise InvalidInputError(answer.message)

        if answer.status == "rejected":
            decline(ctx.deps, event_id)
            return jsonify({"event_id": event_id, "status": "declined"})

        selected_protocol_name = answer.hold["selected_protocol_name"]

        def _work() -> None:
            with trace_context(trace_id):
                continue_after_approval(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent, selected_protocol_name)

        ctx.queue.submit((event_id, _work))
        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint

"""`GET /Notifications` (work_plan.md §8.12).

Closes the gap the Mission 8 deep audit found: `bot.api_client
.BotApiClient.poll_pending_notifications` — what §8.4/§8.5/§8.6/§8.9/
§8.11's proactive pushes all funnel through — had no corresponding
endpoint anywhere in the API Layer. Numbered under §8, not §7, because
§8's own commands and prompts are the reason it exists; the code itself
lives here in `api/*`, the same way §7.11/§7.12 already do.

Read-only: every row this endpoint serves already exists, written by
`persistence.sqlite_backend`'s `store_held_event`/`update_event` in the
same transaction as the state change it records (§2.9's migration 8).
This module's only job is turning a `notification_log` row plus a
re-fetch of the event/hold it references into the exact JSON shape
`bot.api_client.BotNotification`'s dataclasses mirror by value.

COMMANDER-level (`poll_notifications`), not the per-notification-kind
viewer/commander split an earlier draft of this endpoint's own work_plan
text sketched: in this system's real design there is exactly one caller,
the bot's own service identity (`docs/allowed_calls.md`: "bot calls only
api"), which needs to see every kind — including commander-only hold
detail — to do its own fan-out (§8.4/§8.5's "push to every commander",
§8.9's "deliver to whoever submitted it") correctly. See
`docs/api_spec.md`'s "Service identity" section for who that caller is.

The cursor is the caller's own responsibility to remember and pass back
(`since`, default 0) — this endpoint keeps no per-caller read state, only
the one global, monotonic `sequence_id` ordering `notification_log`
already provides. Polling again with the same `since` returns nothing
new; the `next_cursor` a response carries is exactly what the next poll
should pass as `since`.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError

if TYPE_CHECKING:
    from api.app import ApiContext


def _clarification_hold_payload(ctx: "ApiContext", event_id: str) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("clarification", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "raw_text": hold["raw_text"],
        "unresolved_field": hold["unresolved_field"],
        "available_classifications": list(ctx.deps.event_type_registry.types),
    }


def _approval_hold_payload(ctx: "ApiContext", event_id: str) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("approval", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "reason": hold["reason"],
        "risk_level": hold["risk_level"],
        "risk_reason": hold["risk_reason"],
        "selected_protocol_name": hold.get("selected_protocol_name"),
        "candidate_protocol_names": hold.get("candidate_protocol_names") or [],
    }


def _uncertain_verdict_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    return {"event_id": event_id, "insight_text": event.get("insight_text") or ""}


def _precedent_closure_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    matched_id = event["precedent_closed_by_event_id"]
    matched_event = ctx.deps.persistence.fetch_event(matched_id)
    return {
        "event_id": event_id,
        "raw_text": event["raw_text"],
        "matched_precedent_event_id": matched_id,
        "precedent_ending": matched_event["outcome"] if matched_event is not None else "unknown",
    }


def _no_match_payload(ctx: "ApiContext", event_id: str) -> dict:
    # A real terminal outcome ("no_match_protocol"), not a hold — the
    # "why" text lives in outcome_failure_reason, the same column "failed"
    # uses, written by orchestrator.flows.continue_from_risk_assessment's
    # own record_event_outcome(..., failure_reason=selection.reason) call.
    event = ctx.deps.persistence.fetch_event(event_id)
    return {
        "event_id": event_id,
        "raw_text": event["raw_text"],
        "reason": event.get("outcome_failure_reason") or "",
        "risk_level": event.get("risk_level") or "",
        "risk_reason": event.get("risk_reason") or "",
    }


def _job_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    return {
        "job_id": event_id,
        "outcome": event["outcome"],
        "insight_text": event.get("insight_text") or "",
        "steps_completed": _steps_completed(event),
        "failure_reason": event.get("outcome_failure_reason"),
        "failed_step_agent_name": _failed_step_agent_name(event),
    }


_PAYLOAD_BUILDERS = {
    "clarification_hold": _clarification_hold_payload,
    "approval_hold": _approval_hold_payload,
    "uncertain_verdict": _uncertain_verdict_payload,
    "precedent_closure": _precedent_closure_payload,
    "no_match_notice": _no_match_payload,
    "job_finished": _job_payload,
    "job_failed": _job_payload,
}


def _target_chat_ids(ctx: "ApiContext", kind: str, event_id: str) -> list[str]:
    """`job_finished`/`job_failed` are addressed to whoever submitted the
    original event (§8.9: "deliver to whoever submitted it") — its
    `sender_identity` doubles as the chat to reach them at, the same
    "a private chat's chat_id is its user's own identity" equivalence
    §8.13's commander roster already relies on. The other four kinds are
    addressed to every commander, which the bot resolves for itself via
    `GET /Commanders` rather than this endpoint repeating that list on
    every single notification — so they carry none here, matching how
    `bot/holds.py`/`bot/holds.py`/`bot/notifications.py`
    already ignore `BotNotification.target_chat_ids` for these kinds and
    call `list_commander_chat_ids` themselves.
    """

    if kind not in ("job_finished", "job_failed"):
        return []

    event = ctx.deps.persistence.fetch_event(event_id)
    return [event["sender_identity"]]


def _reply_to_message_id(ctx: "ApiContext", kind: str, event_id: str) -> str | None:
    """The originating Telegram message's own ID (work_plan.md §2.3's
    `source_message_id` column), so `job_finished`/`job_failed` — the two
    kinds ever delivered via `TelegramClient.send_reply` (§8.9/§8.11) — can
    actually reference the message that started this event. `None` for a
    sensor-sourced event (no Telegram message to reference) and for every
    other notification kind (none of them are replies to anything).
    """

    if kind not in ("job_finished", "job_failed"):
        return None

    event = ctx.deps.persistence.fetch_event(event_id)
    return event.get("source_message_id")


def _format_notification(ctx: "ApiContext", row: dict) -> dict:
    builder = _PAYLOAD_BUILDERS[row["kind"]]
    return {
        "sequence_id": row["sequence_id"],
        "kind": row["kind"],
        "payload": builder(ctx, row["event_id"]),
        "target_chat_ids": _target_chat_ids(ctx, row["kind"], row["event_id"]),
        "reply_to_message_id": _reply_to_message_id(ctx, row["kind"], row["event_id"]),
    }


def build_notifications_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("notifications", __name__)

    @blueprint.route("/Notifications", methods=["GET"])
    def get_notifications():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "poll_notifications")

        raw_since = request.args.get("since", "0")
        try:
            since = int(raw_since)
            if since < 0:
                raise ValueError
        except ValueError:
            raise InvalidInputError("'since' must be a non-negative integer cursor", field="since")

        rows = ctx.deps.persistence.fetch_notifications_since(since)
        notifications = [_format_notification(ctx, row) for row in rows]
        next_cursor = rows[-1]["sequence_id"] if rows else since

        return jsonify({"notifications": notifications, "next_cursor": next_cursor})

    return blueprint



