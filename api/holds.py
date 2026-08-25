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
much later, could read it back from. See `api/events.py`'s own docstring
for why a queued continuation threads the ID explicitly rather than
relying on contextvar propagation across the queue's worker thread.
"""

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import ConflictError, InvalidInputError, NotFoundError
from orchestrator.flows import continue_after_approval, continue_after_clarification, decline, resolve_approval, resolve_clarification
from tools.tracing import new_trace_id, trace_context

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
        with trace_context(trace_id):
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
        with trace_context(trace_id):
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
