"""Hold listing and continuation routes."""

import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api._route_deps import work_concurrency_keys
from api.request_boundary import ConflictError, InvalidInputError, NotFoundError, ServiceUnavailableError, authenticate, require
from auth.permissions import RequestedOperation
from orchestrator.flows import (
    WorkItem,
    continue_after_approval,
    continue_after_clarification,
    decline,
    resolve_approval,
    resolve_clarification,
)
from profiles import OptimizationPolicy
from tools import get_trace_id, new_trace_id, set_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext

def _pending_hold_or_raise(ctx: "ApiContext", kind: str, event_id: str) -> dict:
    """Return the unresolved hold, or raise NotFoundError / ConflictError."""

    hold = ctx.deps.persistence.fetch_held_event(kind, event_id)
    if hold is None:
        raise NotFoundError(
            ctx.loaded_profile.message_catalog.text("api.hold_not_found", kind=kind, event_id=event_id)
        )
    if hold["resolved"]:
        raise ConflictError(
            ctx.loaded_profile.message_catalog.text(
                "api.hold_resolved",
                identity=hold["resolved_by"],
                resolved_at=hold["resolved_at"],
            ),
            details={
                "resolved_by": hold["resolved_by"],
                "resolved_at": hold["resolved_at"],
            },
        )
    return hold

def _hold_continuation_context(ctx: "ApiContext") -> tuple[str, object]:
    """Assign a trace id and load the profile's optimization policy for a hold continuation."""

    trace_id = get_trace_id() or new_trace_id()
    set_trace_id(trace_id)
    policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
    return trace_id, policy

def _reserve_continuation(ctx: "ApiContext"):
    """Reserve a high-priority queue slot for a hold continuation, or raise 503."""

    reservation = ctx.queue.reserve(True)
    if reservation is None:
        raise ServiceUnavailableError(ctx.loaded_profile.message_catalog.text("api.queue_full"))
    return reservation

def _reject_continuation(ctx: "ApiContext", reservation, message: str, field: str | None = None):
    """Release the reserved slot and raise InvalidInputError."""

    ctx.queue.release_reservation(reservation)
    raise InvalidInputError(message, field=field)

def _submit_hold_work(
    ctx: "ApiContext",
    *,
    event_id: str,
    identity: str,
    trace_id: str,
    policy,
    reservation,
    work,
):
    """Queue the continuation work and return 202 queued."""

    def _work() -> None:
        """Run the reserved hold continuation under this request's trace id."""

        with trace_context(trace_id):
            work()

    ctx.queue.submit(
        WorkItem(
            (event_id, _work),
            trace_id=trace_id,
            priority=0,
            deadline_monotonic=time.monotonic() + policy.job_deadline_seconds,
            concurrency_keys=work_concurrency_keys(identity),
        ),
        reservation,
    )
    return jsonify({"event_id": event_id, "status": "queued"}), 202

def build_holds_blueprint(ctx: "ApiContext") -> Blueprint:
    """JSON routes that list pending holds and continue after clarify/approve."""

    blueprint = Blueprint("holds", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Holds/Pending", methods=["GET"])
    def get_pending_holds():
        """List unresolved approval and clarification holds."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.APPROVE_RUN)

        approval_holds = ctx.deps.persistence.list_held_events("approval")
        clarification_holds = ctx.deps.persistence.list_held_events("clarification")

        items = []
        for h in approval_holds:
            event = ctx.deps.persistence.fetch_event(h["event_id"]) or {}
            items.append({
                "hold_id": h["hold_id"],
                "event_id": h["event_id"],
                "kind": "approval",
                "protocol_name": h.get("selected_protocol_name") or (h.get("candidate_protocol_names") or [""])[0],
                "reason": h.get("reason") or "flagged_protocol",
                "risk_level": h.get("risk_level") or "high",
                "risk_reason": h.get("risk_reason") or "",
                "created_at": h.get("created_at") or "",
                "raw_text": event.get("raw_text") or h.get("selection_reason") or "",
                "sender_identity": event.get("sender_identity") or "",
                "area": event.get("area") or "",
            })

        for h in clarification_holds:
            event = ctx.deps.persistence.fetch_event(h["event_id"]) or {}
            avail_classifications = list(getattr(ctx.deps.event_type_registry, "types", []))
            items.append({
                "hold_id": h["hold_id"],
                "event_id": h["event_id"],
                "kind": "clarification",
                "unresolved_field": h.get("unresolved_field") or "classification",
                "created_at": h.get("created_at") or "",
                "raw_text": h.get("raw_text") or event.get("raw_text") or "",
                "sender_identity": event.get("sender_identity") or "",
                "area": event.get("area") or "",
                "available_classifications": avail_classifications,
            })

        return jsonify({"holds": items, "count": len(items)}), 200

    @blueprint.route("/Clarify/<event_id>", methods=["POST"])
    def post_clarify(event_id):
        """Resolve a clarification hold and queue the continuation."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.RESOLVE_CLARIFICATION)
        identity = request.headers.get("X-Identity")

        request_payload = request.get_json(silent=True) or {}
        classification = request_payload.get("classification")
        if not classification:
            raise InvalidInputError(
                messages.text("api.field_required", field="classification"), field="classification"
            )

        trace_id, policy = _hold_continuation_context(ctx)
        hold = _pending_hold_or_raise(ctx, "clarification", event_id)
        reservation = _reserve_continuation(ctx)

        answer = resolve_clarification(ctx.deps, hold["hold_id"], identity, level, classification)
        if answer.status == "invalid_classification":
            _reject_continuation(ctx, reservation, answer.message, field="classification")
        if answer.status != "resolved":
            # A hold resolved by someone else between the check above
            # and this call — a narrow race; not_found is the accurate
            # status, reported generically rather than re-querying for
            # who/when.
            _reject_continuation(ctx, reservation, answer.message)

        return _submit_hold_work(
            ctx,
            event_id=event_id,
            identity=identity,
            trace_id=trace_id,
            policy=policy,
            reservation=reservation,
            work=lambda: continue_after_clarification(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent),
        )

    @blueprint.route("/Approve/<event_id>", methods=["POST"])
    def post_approve(event_id):
        """Approve or decline a held run and queue the continuation when approved."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.APPROVE_RUN)
        identity = request.headers.get("X-Identity")

        request_payload = request.get_json(silent=True) or {}
        decision = request_payload.get("decision")
        if not decision:
            raise InvalidInputError(messages.text("api.decision_required"), field="decision")

        trace_id, policy = _hold_continuation_context(ctx)
        hold = _pending_hold_or_raise(ctx, "approval", event_id)
        reservation = _reserve_continuation(ctx)

        answer = resolve_approval(ctx.deps, hold["hold_id"], identity, level, decision)
        if answer.status == "invalid_candidate":
            _reject_continuation(ctx, reservation, answer.message, field="decision")
        if answer.status not in ("approved", "rejected"):
            # Same narrow race as the clarify path above.
            _reject_continuation(ctx, reservation, answer.message)

        if answer.status == "rejected":
            ctx.queue.release_reservation(reservation)
            decline(ctx.deps, event_id)
            return jsonify({"event_id": event_id, "status": "declined"})

        selected_protocol_name = answer.hold["selected_protocol_name"]
        return _submit_hold_work(
            ctx,
            event_id=event_id,
            identity=identity,
            trace_id=trace_id,
            policy=policy,
            reservation=reservation,
            work=lambda: continue_after_approval(
                ctx.deps, event_id, ctx.main_agent, ctx.insights_agent, selected_protocol_name
            ),
        )

    return blueprint
