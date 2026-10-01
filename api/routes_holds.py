"""Hold listing and continuation routes."""

import dataclasses
from datetime import datetime, timedelta, timezone
import time

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import BOT_SERVICE_IDENTITY, AuthorizationError, ConflictError, InvalidInputError, NotFoundError, RunFailureError, ServiceUnavailableError, authenticate, require
from history import record_event_outcome, parse_timestamp, storage_timestamp

from orchestrator.flows import begin_report, run_report_extraction

from tools import (
    deep_debug_enabled,
    get_trace_id,
    is_valid_trace_id,
    new_trace_id,
    record_telegram_security_metric,
    render_deep_debug_entry,
    set_trace_id,
    stage_context,
    trace_context,
)
from config import environment as base_config

import logging

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from auth.permissions import InvalidFullNameError, normalize_full_name
from agents import AgentInvocationError, authenticated_request_identity, set_invocation_deadline

from orchestrator.flows import (
    GroupNotRegisteredError,
    InvalidRoutingTargetError,
    OrchestrationParseError,
    is_scoped_target,
    resolve_scope,
    scope_deps,
    answer_conversationally,
    answer_question,
    answer_question_from_plan,
    apply_event_data_reply,
    apply_drone_selection_reply,
    attempt_direct_lane,
    build_role_aware_system_context,
    begin_report,
    begin_request,
    classify_intent,
    build_situational_picture,
    plan_message,
    protocol_requires_approval,
    WorkItem,
    continue_from_risk_assessment,
    run_report_extraction,
    resume_after_event_data,
)

from protocols import CriticalityLevel, Protocol, ProtocolEditError, add_protocol, remove_protocol, replace_protocol
from profiles.loader import hash_profile_file
from profiles import HUMAN_ACTIVATION_TYPE, OptimizationPolicy
from persistence import NotFoundError as PersistenceNotFoundError
from api.simulations import find_simulation_scenario, materialize_simulation, simulation_catalog_payload

from orchestrator.flows import continue_after_approval, continue_after_clarification, decline, resolve_approval, resolve_clarification

if TYPE_CHECKING:
    from api.app import ApiContext

from api.routes import _work_concurrency_keys

def _pending_hold_or_raise(ctx: "ApiContext", kind: str, event_id: str) -> dict:
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
    trace_id = get_trace_id() or new_trace_id()
    set_trace_id(trace_id)
    policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
    return trace_id, policy

def _reserve_continuation(ctx: "ApiContext"):
    reservation = ctx.queue.reserve(True)
    if reservation is None:
        raise ServiceUnavailableError(ctx.loaded_profile.message_catalog.text("api.queue_full"))
    return reservation

def _reject_continuation(ctx: "ApiContext", reservation, message: str, field: str | None = None):
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
    def _work() -> None:
        with trace_context(trace_id):
            work()

    ctx.queue.submit(
        WorkItem(
            (event_id, _work),
            trace_id=trace_id,
            priority=0,
            deadline_monotonic=time.monotonic() + policy.job_deadline_seconds,
            concurrency_keys=_work_concurrency_keys(identity),
        ),
        reservation,
    )
    return jsonify({"event_id": event_id, "status": "queued"}), 202

def build_holds_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("holds", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Holds/Pending", methods=["GET"])
    def get_pending_holds():
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
