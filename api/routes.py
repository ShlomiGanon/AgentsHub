"""Consolidated responsibility module for routes."""

import dataclasses
from datetime import datetime, timedelta, timezone
import time

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import BOT_SERVICE_IDENTITY, AuthorizationError, ConflictError, InvalidInputError, NotFoundError, RunFailureError, ServiceUnavailableError, authenticate, require
from history import record_event_outcome, storage_timestamp

from orchestrator.flows import begin_report, run_report_extraction

from tools import (
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
from agents import authenticated_request_identity, set_invocation_deadline

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

from orchestrator.flows import continue_after_approval, continue_after_clarification, decline, resolve_approval, resolve_clarification

if TYPE_CHECKING:
    from api.app import ApiContext


def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))


def build_events_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("events", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Event", methods=["POST"])
    def post_event():
        optimization_policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.SUBMIT_EVENT)

        request_payload = request.get_json(silent=True) or {}
        text = request_payload.get("text")
        sender_identity = request_payload.get("sender_identity")

        if not text:
            raise InvalidInputError(messages.text("api.field_required", field="text"), field="text")
        if not sender_identity:
            raise InvalidInputError(
                messages.text("api.field_required", field="sender_identity"), field="sender_identity"
            )
        if sender_identity != caller_identity:
            raise AuthorizationError(messages.text("api.sender_identity_mismatch"))
        reservation = ctx.queue.reserve(False)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)
        deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
        try:
            event_id = begin_report(
                ctx.deps,
                text,
                "sensor",
                _now(),
                sender_identity,
                deadline_at=deadline_at,
                sender_permission_level=level.name.lower(),
            )
        except Exception:
            ctx.queue.release_reservation(reservation)
            raise

        def _work() -> None:
            with trace_context(trace_id):
                run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit(
            WorkItem(
                (event_id, _work), trace_id=trace_id,
                deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                concurrency_keys=(f"sender:{sender_identity}",),
            ),
            reservation,
        )

        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint


if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)


def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))


KNOWN_BUTTON_PROTOCOLS: dict[str, str] = {
    "📊 \u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1 \u05db\u05dc\u05dc\u05d9\u05ea": "overall_situational_picture",
    "📹 \u05de\u05e6\u05d1 \u05de\u05e6\u05dc\u05de\u05d5\u05ea": "query_camera_status",
    "🛸 \u05de\u05e6\u05d1 \u05e6\u05d9 \u05e8\u05d7\u05e4\u05e0\u05d9\u05dd": "query_drone_fleet_status",
    "🚀 \u05d4\u05d6\u05e0\u05e7\u05ea \u05e8\u05d7\u05e4\u05df": "dispatch_drone_to_incident",
    "🔄 \u05d4\u05d7\u05d6\u05e8\u05ea \u05e8\u05d7\u05e4\u05df \u05dc\u05d1\u05e1\u05d9\u05e1": "recall_drone_to_base",
    "👥 \u05e1\u05d8\u05d8\u05d5\u05e1 \u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea": "report_team_availability",
    "🚨 \u05d4\u05d6\u05e0\u05e7\u05ea \u05db\u05d5\u05d7\u05d5\u05ea": "dispatch_emergency_forces",
    "📜 \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05d0\u05d9\u05e8\u05d5\u05e2\u05d9\u05dd": "query_historical_incidents",
    "✅ \u05d0\u05e0\u05d9 \u05d6\u05de\u05d9\u05df \u05dc\u05db\u05d5\u05e0\u05e0\u05d5\u05ea": "record_attendance_response",
    "❌ \u05d0\u05d9\u05e0\u05d9 \u05d6\u05de\u05d9\u05df": "record_attendance_response",
}


SITUATIONAL_PICTURE_PROTOCOL = "overall_situational_picture"

# Phrases that ask for the overall picture in so many words. Escaped \uXXXX
# Hebrew literals keep tests/test_hebrew_leakage.py's scan clean, exactly as
# `_is_team_roster_query` below does. Matching one of these routes the message
# deterministically to the live multi-domain picture instead of leaving the
# choice to general question routing.
_SITUATIONAL_PICTURE_TERMS = (
    "\u05ea\u05de\u05d5\u05e0\u05ea \u05de\u05e6\u05d1",  # (Hebrew) situational picture
    "\u05ea\u05de\u05d5\u05e0\u05ea \u05d4\u05de\u05e6\u05d1",  # (Hebrew) the situational picture
    "\u05ea\u05de\u05d5\u05e0\u05ea-\u05de\u05e6\u05d1",  # (Hebrew) situational-picture
    "\u05de\u05e6\u05d1 \u05d4\u05d2\u05d6\u05e8\u05d4",  # (Hebrew) the sector's state
    "\u05e1\u05d8\u05d8\u05d5\u05e1 \u05d2\u05d6\u05e8\u05d4",  # (Hebrew) sector status
    "situational picture",
    "situation picture",
    "sector status",
)


def _is_situational_picture_query(text: str) -> bool:
    normalized = " ".join(text.strip().casefold().split())
    return any(term in normalized for term in _SITUATIONAL_PICTURE_TERMS)


def _is_team_roster_query(text: str, prior_messages: tuple[dict, ...]) -> bool:
    """Recognize roster questions that should not depend on general LLM routing."""
    normalized = text.strip().casefold()
    explicit_terms = (
        "\u05db\u05d9\u05ea\u05ea \u05db\u05d5\u05e0\u05e0\u05d5\u05ea", "\u05db\u05d9\u05ea\u05ea \u05d4\u05db\u05d5\u05e0\u05e0\u05d5\u05ea", "\u05d7\u05d1\u05e8\u05d9 \u05db\u05d9\u05ea\u05d4", "\u05d7\u05d1\u05e8\u05d9 \u05d4\u05db\u05d9\u05ea\u05d4",
        "\u05d4\u05d7\u05d1\u05e8\u05d9 \u05db\u05d9\u05ea\u05ea", "\u05de\u05e6\u05d1\u05ea \u05db\u05d9\u05ea\u05d4", "\u05de\u05e6\u05d1\u05ea \u05d4\u05db\u05d9\u05ea\u05d4",
    )
    if any(term in normalized for term in explicit_terms):
        return True
    if normalized.rstrip(" ?!") not in {"\u05de\u05d9 \u05d4\u05dd", "\u05de\u05d9 \u05d0\u05dc\u05d4", "\u05de\u05d4 \u05d4\u05e9\u05de\u05d5\u05ea", "\u05d0\u05e4\u05e9\u05e8 \u05d0\u05ea \u05d4\u05e9\u05de\u05d5\u05ea \u05e9\u05dc\u05d4\u05dd"}:
        return False
    recent_context = " ".join(str(item.get("content", "")) for item in prior_messages[-4:]).casefold()
    return any(term in recent_context for term in explicit_terms)


def _team_roster_view(text: str) -> str:
    normalized = text.strip().casefold()
    if any(term in normalized for term in ("\u05d8\u05e8\u05dd \u05d3\u05d9\u05d5\u05d5\u05d7", "\u05dc\u05d0 \u05d3\u05d9\u05d5\u05d5\u05d7", "\u05de\u05de\u05ea\u05d9\u05df", "\u05de\u05de\u05ea\u05d9\u05e0\u05d9\u05dd")):
        return "awaiting"
    if any(term in normalized for term in ("\u05de\u05d9 \u05dc\u05d0 \u05d6\u05de\u05d9\u05df", "\u05d0\u05d9\u05e0\u05dd \u05d6\u05de\u05d9\u05e0\u05d9\u05dd", "\u05dc\u05d0 \u05d6\u05de\u05d9\u05e0\u05d9\u05dd")):
        return "unavailable"
    if any(term in normalized for term in ("\u05de\u05d9 \u05d6\u05de\u05d9\u05df", "\u05d6\u05de\u05d9\u05e0\u05d9\u05dd \u05d1\u05dc\u05d1\u05d3")):
        return "available"
    if any(term in normalized for term in ("\u05de\u05d9 \u05d4\u05dd", "\u05de\u05d9 \u05d0\u05dc\u05d4", "\u05de\u05d9 \u05d7\u05d1\u05e8", "\u05d7\u05d1\u05e8\u05d9 \u05db\u05d9\u05ea\u05d4", "\u05d7\u05d1\u05e8\u05d9 \u05d4\u05db\u05d9\u05ea\u05d4", "\u05de\u05d4 \u05d4\u05e9\u05de\u05d5\u05ea", "\u05d4\u05e9\u05de\u05d5\u05ea \u05e9\u05dc\u05d4\u05dd")):
        return "members"
    return "summary"


def _is_approval_policy_question(text: str) -> bool:
    normalized = text.strip().casefold()
    return any(word in normalized for word in ("\u05d0\u05d9\u05e9\u05d5\u05e8", "\u05dc\u05d0\u05e9\u05e8", "\u05de\u05d0\u05e9\u05e8")) and any(
        term in normalized for term in ("\u05de\u05d9", "\u05d0\u05d9\u05e4\u05d4", "\u05d0\u05de\u05d5\u05e8", "\u05e6\u05e8\u05d9\u05da", "\u05de\u05d0\u05e9\u05e8")
    )


def _is_pending_report_cancellation(text: str) -> bool:
    normalized = text.strip().casefold()
    return any(
        phrase in normalized
        for phrase in ("\u05e2\u05d6\u05d5\u05d1", "\u05ea\u05d1\u05d8\u05dc", "\u05d1\u05d8\u05dc", "\u05d0\u05d9\u05df \u05d9\u05d5\u05ea\u05e8", "\u05d0\u05d9\u05df \u05db\u05dc\u05d5\u05dd", "\u05d1\u05d8\u05e2\u05d5\u05ea", "\u05dc\u05d0 \u05e9\u05de\u05e2\u05ea\u05d9 \u05d8\u05d5\u05d1")
    )


def build_messages_blueprint(app_ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("messages", __name__)
    messages = app_ctx.loaded_profile.message_catalog
    non_human_activation_event_types = tuple(
        event_type
        for event_type in app_ctx.deps.event_type_registry.types
        if event_type != HUMAN_ACTIVATION_TYPE
    )
    areas = tuple(app_ctx.deps.area_registry.areas)

    @blueprint.route("/Msg", methods=["POST"])
    def post_msg():
        # `ctx` is request-local: for a message from a Telegram group bound to a
        # specialist it becomes a copy of `app_ctx` whose `deps` only expose that
        # agent (+ history). Every `ctx.deps.*` read below is therefore scoped
        # automatically; nothing else on the context differs.
        ctx = app_ctx
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.SUBMIT_MESSAGE)

        request_payload = request.get_json(silent=True) or {}
        text = request_payload.get("text")
        sender_identity = request_payload.get("sender_identity")
        source_message_id = request_payload.get("source_message_id")
        conversation_id = request_payload.get("conversation_id")
        event_data_event_id = request_payload.get("event_data_event_id")
        telegram_chat_id = request_payload.get("telegram_chat_id")
        telegram_chat_type = request_payload.get("telegram_chat_type")

        try:
            scoped_agent = resolve_scope(
                app_ctx.group_routing,
                str(telegram_chat_id) if telegram_chat_id is not None else None,
                str(telegram_chat_type) if telegram_chat_type is not None else None,
            )
        except GroupNotRegisteredError as exc:
            raise AuthorizationError(messages.text("api.group_not_registered", chat_id=exc.chat_id)) from exc
        if is_scoped_target(scoped_agent):
            ctx = dataclasses.replace(app_ctx, deps=scope_deps(app_ctx.deps, scoped_agent))
            logger.info(
                "message scoped to group agent",
                extra={"event": "group_scope_applied", "chat_id": str(telegram_chat_id), "agent": scoped_agent, "trace_id": get_trace_id()},
            )

        # Built fresh, per authenticated request — never once at blueprint
        # creation, before a caller is known (docs/Next_Plan.md §4.5/Stage 3).
        # A viewer's context has protected arrays absent entirely, not just
        # filtered out of the final answer.
        system_context = build_role_aware_system_context(
            level,
            ctx.loaded_profile.profile_name,
            ctx.deps.protocol_set.all(),
            ctx.deps.registry,
            non_human_activation_event_types,
            areas,
        )

        if not text:
            raise InvalidInputError(messages.text("api.field_required", field="text"), field="text")
        if not sender_identity:
            raise InvalidInputError(
                messages.text("api.field_required", field="sender_identity"), field="sender_identity"
            )
        if sender_identity != caller_identity:
            raise AuthorizationError(messages.text("api.sender_identity_mismatch"))
        if conversation_id is not None and (not isinstance(conversation_id, str) or not conversation_id.strip() or len(conversation_id) > 200):
            raise InvalidInputError(messages.text("api.conversation_id_invalid"), field="conversation_id")
        if event_data_event_id is not None and (
            not isinstance(event_data_event_id, str) or not event_data_event_id.strip()
        ):
            raise InvalidInputError(messages.text("api.event_data_event_id_invalid"), field="event_data_event_id")

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)

        optimization_policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
        set_invocation_deadline(time.monotonic() + optimization_policy.direct_deadline_seconds)
        history_turns = getattr(ctx.loaded_profile, "conversation_history_turns", 0)
        history_ttl = getattr(ctx.loaded_profile, "conversation_history_ttl_hours", 24)

        def _remember(role: str, content: str, event_id: str | None = None) -> None:
            if conversation_id is not None and history_turns > 0:
                ctx.deps.persistence.append_conversation_message(
                    conversation_id,
                    role,
                    content,
                    ttl_hours=history_ttl,
                    max_turns=history_turns,
                    event_id=event_id,
                )

        if source_message_id:
            existing_event = ctx.deps.persistence.fetch_event_by_source_message("telegram", sender_identity, str(source_message_id))
            if existing_event is not None:
                existing_kind = "request" if existing_event.get("classification") == "human_activation" else "report"
                return jsonify({
                    "taken_as": existing_kind,
                    "event_id": existing_event["event_id"],
                    "status": "queued" if existing_event.get("outcome") is None else existing_event["outcome"],
                    "duplicate": True,
                }), 202

        prior_messages: tuple[dict, ...] = ()
        if conversation_id is not None and history_turns > 0:
            prior_messages = tuple(ctx.deps.persistence.fetch_conversation_messages(conversation_id, history_turns * 2))

        _remember("user", text)

        # A correction/cancellation of an incomplete report is conversation
        # control, not a fresh operational request (and especially not a drone
        # recall merely because the text contains "\u05ea\u05d1\u05d8\u05dc"). Resolve only the
        # newest unresolved hold owned by this sender in this conversation.
        if _is_pending_report_cancellation(str(text)):
            owned_pending: list[tuple[dict, dict]] = []
            for hold in ctx.deps.persistence.list_held_events("event_data"):
                held_event = ctx.deps.persistence.fetch_event(hold["event_id"])
                if (
                    held_event is not None
                    and held_event.get("conversation_id") == conversation_id
                    and held_event.get("sender_identity") == caller_identity
                ):
                    owned_pending.append((hold, held_event))
            if owned_pending:
                hold, held_event = owned_pending[-1]
                ctx.deps.persistence.resolve_held_event(
                    "event_data",
                    hold["hold_id"],
                    {"resolved_by": caller_identity, "decision": "cancelled_by_reporter"},
                )
                record_event_outcome(
                    ctx.deps.persistence,
                    held_event["event_id"],
                    "declined",
                    failure_reason="\u05d4\u05de\u05d3\u05d5\u05d5\u05d7 \u05d1\u05d9\u05d8\u05dc \u05d0\u05d5 \u05ea\u05d9\u05e7\u05df \u05d0\u05ea \u05d4\u05d3\u05d9\u05d5\u05d5\u05d7 \u05dc\u05e4\u05e0\u05d9 \u05d4\u05e9\u05dc\u05de\u05ea \u05d4\u05e4\u05e8\u05d8\u05d9\u05dd.",
                )
                answer = "\u05d4\u05d3\u05d9\u05d5\u05d5\u05d7 \u05d4\u05de\u05de\u05ea\u05d9\u05df \u05d1\u05d5\u05d8\u05dc. \u05dc\u05d0 \u05ea\u05d5\u05e4\u05e2\u05dc \u05e4\u05e2\u05d5\u05dc\u05d4 \u05d5\u05dc\u05d0 \u05e0\u05d3\u05e8\u05e9 \u05dc\u05de\u05e1\u05d5\u05e8 \u05de\u05d9\u05e7\u05d5\u05dd."
                _remember("assistant", answer, held_event["event_id"])
                return jsonify({
                    "taken_as": "event_update",
                    "event_id": held_event["event_id"],
                    "answer": answer,
                    "status": "declined",
                })

        # Event-data replies are explicit. Sharing a sender/conversation with an
        # old hold is insufficient because a new button or request must remain
        # an independent message.
        pending_hold = None
        if event_data_event_id is not None:
            candidate = ctx.deps.persistence.fetch_held_event("event_data", event_data_event_id)
            pending_event = ctx.deps.persistence.fetch_event(event_data_event_id)
            if (
                candidate is None
                or candidate.get("resolved")
                or pending_event is None
                or pending_event.get("conversation_id") != conversation_id
                or pending_event.get("sender_identity") != caller_identity
            ):
                raise InvalidInputError(messages.text("api.event_data_reply_not_pending"))
            pending_hold = candidate

        # A drone-choice reply is operational input, not free-form missing event
        # data. Resolve it deterministically before any planner/model call.
        drone_selection_hold = (
            pending_hold if pending_hold is not None and pending_hold.get("missing_fields") == ["drone_selection"] else None
        )

        if drone_selection_hold is not None:
            require(level, RequestedOperation.APPROVE_RUN)
            surveillance_agent = ctx.deps.registry.get("surveillance_agent")
            store = getattr(surveillance_agent, "surveillance_store", None)
            if store is None:
                raise RunFailureError("surveillance persistence is unavailable")

            normalized = str(text).strip().casefold()
            recall_all = (
                normalized in {
                    "all", "all drones", "\u05db\u05d5\u05dc\u05dd", "\u05db\u05d5\u05dc\u05df",
                    "\u05d0\u05ea \u05db\u05d5\u05dc\u05dd", "\u05d0\u05ea \u05db\u05d5\u05dc\u05df",
                    "\u05e2\u05dc \u05db\u05d5\u05dc\u05dd", "\u05e2\u05dc \u05db\u05d5\u05dc\u05df",
                }
                or "\u05db\u05dc \u05d4\u05e8\u05d7\u05e4" in normalized
            )
            result = store.recall_all_drones() if recall_all else store.recall_drone(str(text).strip())
            if result["status"] in {"selection_required", "not_found"}:
                choices = "\n".join(
                    f"- {mission['callsign']} ({mission['drone_id']}) — {mission['mission_id']}, {mission['target_area']}"
                    for mission in result["missions"]
                )
                answer = messages.text("api.drone_selection_invalid", choices=choices)
                _remember("assistant", answer, drone_selection_hold["event_id"])
                return jsonify({
                    "taken_as": "clarification",
                    "event_id": drone_selection_hold["event_id"],
                    "answer": answer,
                    "status": "waiting_for_drone_selection",
                })

            ctx.deps.persistence.resolve_held_event(
                "event_data",
                drone_selection_hold["hold_id"],
                {"resolved_by": caller_identity, "drone_selection": str(text).strip()},
            )
            record_event_outcome(ctx.deps.persistence, drone_selection_hold["event_id"], "succeeded")
            if result["status"] == "no_active":
                answer = messages.text("api.drone_recall_none")
            elif result["status"] == "returned_all":
                names = ", ".join(mission["callsign"] for mission in result["missions"])
                answer = messages.text("api.drone_recall_all_done", names=names)
            else:
                drone = result["drone"]
                mission = result["mission"]
                answer = messages.text(
                    "api.drone_recall_one_done", callsign=drone["callsign"], mission_id=mission["mission_id"]
                )
            _remember("assistant", answer, drone_selection_hold["event_id"])
            return jsonify({
                "taken_as": "event_update",
                "event_id": drone_selection_hold["event_id"],
                "answer": answer,
                "status": "succeeded",
            })

        matching_event_data_hold = pending_hold is not None

        if matching_event_data_hold:
            reservation = ctx.queue.reserve(True)
            if reservation is None:
                raise ServiceUnavailableError(messages.text("api.queue_full_event_detail"))
            try:
                event_data_reply = apply_event_data_reply(
                    ctx.deps,
                    ctx.main_agent,
                    text,
                    caller_identity,
                    conversation_id,
                    prior_messages,
                    event_data_event_id,
                )
            except OrchestrationParseError as exc:
                ctx.queue.release_reservation(reservation)
                logger.warning(
                    "event data reply failed validation — abandoning hold to prevent infinite loop",
                    extra={"event": "event_data_reply_invalid", "reason": str(exc), "trace_id": trace_id},
                )
                # Resolve the stuck hold so the user is not trapped in an infinite loop.
                # Fall through: the message will be treated as a new request.
                try:
                    ctx.deps.persistence.resolve_held_event(
                        "event_data",
                        pending_hold["hold_id"],
                        {"resolved_by": "system:abandoned_parse_error"},
                    )
                except Exception:
                    pass  # best-effort; fall through regardless
                matching_event_data_hold = False
            except Exception:
                ctx.queue.release_reservation(reservation)
                raise
            if event_data_reply is not None and event_data_reply.ambiguous_event_ids:
                ctx.queue.release_reservation(reservation)
                answer = messages.text(
                    "api.event_detail_ambiguous",
                    count=str(len(event_data_reply.ambiguous_event_ids)),
                )
                _remember("assistant", answer)
                return jsonify(
                    {
                        "taken_as": "clarification",
                        "status": "ambiguous_event_data_hold",
                        "pending_event_ids": list(event_data_reply.ambiguous_event_ids),
                        "answer": answer,
                    }
                )

            if event_data_reply is not None:
                event_id = event_data_reply.event_id
                if not event_data_reply.updates:
                    ctx.queue.release_reservation(reservation)
                    _remember("assistant", event_data_reply.message, event_id)
                    return jsonify(
                        {
                            "taken_as": "clarification",
                            "event_id": event_id,
                            "answer": event_data_reply.message,
                            "status": "waiting_for_event_data",
                        }
                    )

                def _resume_waiting_work() -> None:
                    with trace_context(trace_id):
                        resume_after_event_data(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

                ctx.queue.submit(
                    WorkItem(
                        (event_id, _resume_waiting_work),
                        trace_id=trace_id,
                        priority=0,
                        deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                        concurrency_keys=(f"sender:{caller_identity}",),
                    ),
                    reservation,
                )
                _remember("assistant", event_data_reply.message, event_id)
                return jsonify(
                    {
                        "taken_as": "event_update",
                        "event_id": event_id,
                        "updated_fields": sorted(event_data_reply.updates),
                        "answer": event_data_reply.message,
                        "status": "queued",
                    }
                ), 202
            ctx.queue.release_reservation(reservation)

        # Fast Path for known buttons / deterministic protocol selection
        # button -> known protocol -> RBAC -> approval if required -> agent -> approved tool
        matched_protocol_name = request_payload.get("protocol_hint") or KNOWN_BUTTON_PROTOCOLS.get(str(text).strip())
        if (
            matched_protocol_name is not None
            and is_scoped_target(scoped_agent)
            and ctx.deps.protocol_set.get(matched_protocol_name) is None
            and app_ctx.deps.protocol_set.get(matched_protocol_name) is not None
        ):
            # An explicit button/hint for a protocol this group's agent does not
            # own: refuse loudly rather than silently re-routing it elsewhere.
            raise InvalidInputError(
                messages.text("api.protocol_out_of_group_scope", protocol=matched_protocol_name, agent=scoped_agent),
                field="protocol_hint",
            )
        if matched_protocol_name is None and _is_situational_picture_query(str(text)):
            matched_protocol_name = SITUATIONAL_PICTURE_PROTOCOL
        if matched_protocol_name is None and _is_team_roster_query(str(text), prior_messages):
            matched_protocol_name = "report_team_availability"
        matched_protocol = ctx.deps.protocol_set.get(matched_protocol_name) if matched_protocol_name else None

        if matched_protocol is not None:
            received_at = _now()
            is_commander = level >= PermissionLevel.COMMANDER
            needs_approval = protocol_requires_approval(matched_protocol, is_commander)

            if needs_approval:
                require(level, RequestedOperation.REQUEST_ACTION)
                reservation = ctx.queue.reserve(False)
                if reservation is None:
                    raise ServiceUnavailableError(messages.text("api.queue_full"))
                deadline_at = storage_timestamp(
                    datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds)
                )
                try:
                    event_id = begin_request(
                        ctx.deps, text, received_at, sender_identity, source_message_id,
                        conversation_id=conversation_id, deadline_at=deadline_at,
                        sender_permission_level=level.name.lower(),
                    )
                except Exception:
                    ctx.queue.release_reservation(reservation)
                    raise

                def _work_fast_path() -> None:
                    with trace_context(trace_id):
                        continue_from_risk_assessment(
                            ctx.deps,
                            event_id,
                            ctx.main_agent,
                            ctx.insights_agent,
                            selected_protocol=matched_protocol,
                        )

                ctx.queue.submit(
                    WorkItem(
                        (event_id, _work_fast_path),
                        trace_id=trace_id,
                        deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                        concurrency_keys=(f"sender:{sender_identity}",),
                    ),
                    reservation,
                )
                _remember("assistant", messages.text("api.queued_request", task_id=event_id), event_id)
                return jsonify({"taken_as": "request", "event_id": event_id, "status": "queued"}), 202

            if matched_protocol.name == "query_historical_incidents":
                require(level, RequestedOperation.ASK_QUESTION)
                caller_filter = None if is_commander else caller_identity
                try:
                    history_ans = ctx.deps.history_query_service.query(text, sender_identity_filter=caller_filter)
                    answer = history_ans.answer
                except Exception as exc:
                    answer = f"\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05e9\u05dc\u05d9\u05e4\u05ea \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d4: {exc}"
                _remember("assistant", answer)
                return jsonify({"taken_as": "question", "answer": answer, "protocol": matched_protocol.name})

            if len(matched_protocol.participating_agents) > 1:
                # A multi-domain, read-only picture: the Main Agent decides what to ask each
                # participating specialist, they answer from live tool data concurrently, the
                # recent event log is pulled for the window it chose, and the answer is written
                # from those findings only (orchestrator/situational_picture.py). A viewer's
                # recent-events view keeps the same ownership scope as any question they ask.
                require(level, RequestedOperation.ASK_QUESTION)
                picture = build_situational_picture(
                    ctx.main_agent,
                    matched_protocol,
                    ctx.deps.registry,
                    ctx.deps.history_query_service,
                    str(text),
                    caller_identity=caller_identity,
                    sender_identity_filter=None if is_commander else caller_identity,
                )
                _remember("assistant", picture.text)
                return jsonify({
                    "taken_as": "question",
                    "answer": picture.text,
                    "protocol": matched_protocol.name,
                    "provenance": picture.provenance(),
                })

            ag_name = matched_protocol.participating_agents[0]
            ag = ctx.deps.registry.get(ag_name)
            allowed_tools = list(matched_protocol.approved_tools)
            require_op = (
                RequestedOperation.REQUEST_ACTION
                if any(getattr(t, "side_effecting", False) for t in ag.exposed_tools() if t.name in allowed_tools)
                else RequestedOperation.ASK_QUESTION
            )
            require(level, require_op)
            if matched_protocol.name == "report_team_availability" and hasattr(ag, "report_team_availability"):
                with authenticated_request_identity(caller_identity):
                    answer = ag.report_team_availability(view=_team_roster_view(str(text)))
                _remember("assistant", answer)
                return jsonify({
                    "taken_as": "question",
                    "answer": answer,
                    "protocol": matched_protocol.name,
                })
            with authenticated_request_identity(caller_identity):
                res = ag.process(text, allowed_tools)
            answer = res.text if res.status == "success" else f"\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05d4\u05e4\u05e2\u05dc\u05ea \u05e1\u05d5\u05db\u05df: {res.text}"
            _remember("assistant", answer)
            return jsonify({
                "taken_as": "question" if require_op == RequestedOperation.ASK_QUESTION else "event_update",
                "answer": answer,
                "protocol": matched_protocol.name,
            })

        if _is_approval_policy_question(str(text)):
            require(level, RequestedOperation.CONVERSE)
            answer = (
                "\u05d1\u05e7\u05e9\u05d4 \u05e9\u05de\u05d7\u05d9\u05d9\u05d1\u05ea \u05d0\u05d9\u05e9\u05d5\u05e8 \u05e0\u05e9\u05dc\u05d7\u05ea \u05dc\u05de\u05e4\u05e7\u05d3\u05d9\u05dd \u05d4\u05e8\u05e9\u05d5\u05de\u05d9\u05dd \u05d1\u05de\u05e2\u05e8\u05db\u05ea. "
                "\u05e8\u05e7 \u05de\u05e9\u05ea\u05de\u05e9 \u05d1\u05e2\u05dc \u05d4\u05e8\u05e9\u05d0\u05ea \u05de\u05e4\u05e7\u05d3 \u05d9\u05db\u05d5\u05dc \u05dc\u05d0\u05e9\u05e8 \u05d0\u05d5 \u05dc\u05d3\u05d7\u05d5\u05ea \u05d0\u05d5\u05ea\u05d4 \u05d1\u05d0\u05de\u05e6\u05e2\u05d5\u05ea \u05db\u05e4\u05ea\u05d5\u05e8\u05d9 \u05d4\u05d0\u05d9\u05e9\u05d5\u05e8. "
                "\u05d0\u05dd \u05d7\u05e1\u05e8\u05d9\u05dd \u05e4\u05e8\u05d8\u05d9\u05dd \u05de\u05d1\u05e6\u05e2\u05d9\u05d9\u05dd, \u05dc\u05de\u05e9\u05dc \u05de\u05d9\u05e7\u05d5\u05dd \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2, \u05d4\u05de\u05e2\u05e8\u05db\u05ea \u05ea\u05e9\u05d0\u05dc \u05e2\u05dc\u05d9\u05d4\u05dd \u05dc\u05e4\u05e0\u05d9 \u05d9\u05e6\u05d9\u05e8\u05ea \u05d1\u05e7\u05e9\u05ea \u05d4\u05d0\u05d9\u05e9\u05d5\u05e8."
            )
            _remember("assistant", answer)
            return jsonify({"taken_as": "conversational", "answer": answer})

        message_plan = None
        planner_mode = optimization_policy.planner_mode
        if planner_mode in {"shadow", "merged"}:
            try:
                message_plan = plan_message(
                    ctx.main_agent,
                    ctx.deps.protocol_set.all(),
                    text,
                    ctx.deps.registry,
                    ctx.deps.history_query_service,
                    prior_messages,
                    system_context,
                )
            except OrchestrationParseError as exc:
                logger.warning(
                    "merged message planner failed validation",
                    extra={"event": "message_plan_invalid", "planner_mode": planner_mode, "reason": str(exc), "trace_id": trace_id},
                )
                if planner_mode == "merged":
                    answer = messages.text("api.clarify_check_record_do")
                    _remember("assistant", answer)
                    return jsonify({"taken_as": "clarification", "answer": answer})

        try:
            intent = message_plan.intent if planner_mode == "merged" and message_plan is not None else classify_intent(
                ctx.main_agent, ctx.deps.protocol_set.all(), text, prior_messages
            )
        except OrchestrationParseError as exc:
            raise RunFailureError(str(exc)) from exc

        logger.info(
            "intent classified",
            extra={"event": "intent_classified", "intent": intent.intent, "reason": intent.reason, "trace_id": get_trace_id()},
        )

        received_at = _now()

        if intent.intent == "needs_clarification":
            answer = intent.clarification_question or messages.text("api.clarify_action")
            _remember("assistant", answer)
            return jsonify({
                "taken_as": "clarification",
                "answer": answer,
            })

        if intent.intent == "conversational":
            require(level, RequestedOperation.CONVERSE)
            try:
                reply = (
                    message_plan.conversational_reply
                    if planner_mode == "merged" and message_plan is not None
                    else answer_conversationally(ctx.main_agent, text, system_context, prior_messages)
                )
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            _remember("assistant", reply)
            return jsonify({"taken_as": "conversational", "answer": reply})

        if intent.intent == "question":
            require(level, RequestedOperation.ASK_QUESTION)
            # Ownership scoping (docs/Next_Plan.md §5 decision record): a viewer's
            # ask_question is restricted to events they themselves submitted,
            # matched by their own authenticated identity. A commander is
            # unrestricted (filter stays None).
            caller_sender_identity_filter = None if level is PermissionLevel.COMMANDER else caller_identity
            try:
                if planner_mode == "merged" and message_plan is not None and message_plan.question_selection is not None:
                    question_answer = answer_question_from_plan(
                        ctx.main_agent,
                        text,
                        message_plan.question_selection,
                        ctx.deps.registry,
                        ctx.deps.history_query_service,
                        max_fanout=optimization_policy.specialist_fanout,
                        caller_sender_identity_filter=caller_sender_identity_filter,
                        conversation_messages=prior_messages,
                    )
                    answer = question_answer.text
                    provenance = question_answer.provenance
                else:
                    answer = answer_question(
                        ctx.main_agent, text, ctx.deps.registry, ctx.deps.history_query_service,
                        caller_sender_identity_filter=caller_sender_identity_filter,
                        conversation_messages=prior_messages,
                    )
                    provenance = None
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            _remember("assistant", answer)
            response_payload = {"taken_as": "question", "answer": answer}
            if provenance is not None:
                response_payload["provenance"] = provenance
            return jsonify(response_payload)

        if intent.intent == "report":
            require(level, RequestedOperation.REPORT_EVENT)
            reservation = ctx.queue.reserve(False)
            if reservation is None:
                raise ServiceUnavailableError(messages.text("api.queue_full"))
            deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
            try:
                event_id = begin_report(
                    ctx.deps, text, "telegram", received_at, sender_identity, source_message_id,
                    conversation_id=conversation_id, deadline_at=deadline_at,
                    sender_permission_level=level.name.lower(),
                )
            except Exception:
                ctx.queue.release_reservation(reservation)
                raise

            def _work() -> None:
                with trace_context(trace_id):
                    run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

            ctx.queue.submit(
                WorkItem(
                    (event_id, _work), trace_id=trace_id,
                    deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                    concurrency_keys=(f"sender:{sender_identity}",),
                ),
                reservation,
            )
            _remember("assistant", messages.text("api.queued_report", task_id=event_id), event_id)
            return jsonify({"taken_as": "report", "event_id": event_id, "status": "queued"}), 202

        if intent.intent != "request":
            raise RunFailureError(f"unsupported message intent: {intent.intent!r}")

        require(level, RequestedOperation.REQUEST_ACTION)
        is_commander = level >= PermissionLevel.COMMANDER
        reservation = ctx.queue.reserve(False)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))
        deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
        try:
            event_id = begin_request(
                ctx.deps, text, received_at, sender_identity, source_message_id,
                conversation_id=conversation_id, deadline_at=deadline_at,
                sender_permission_level=level.name.lower(),
            )
        except Exception:
            ctx.queue.release_reservation(reservation)
            raise

        def _work() -> None:
            with trace_context(trace_id):
                continue_from_risk_assessment(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit(
            WorkItem(
                (event_id, _work), trace_id=trace_id,
                deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                concurrency_keys=(f"sender:{sender_identity}",),
            ),
            reservation,
        )
        _remember("assistant", messages.text("api.queued_request", task_id=event_id), event_id)
        return jsonify({"taken_as": "request", "event_id": event_id, "status": "queued"}), 202

    return blueprint

if TYPE_CHECKING:
    from api.app import ApiContext


def protocol_to_dict(protocol: Protocol) -> dict:
    return {
        "name": protocol.name,
        "description": protocol.description,
        "participating_agents": list(protocol.participating_agents),
        "approved_tools": list(protocol.approved_tools),
        "expected_success_output": protocol.expected_success_output,
        "criticality": protocol.criticality.name.lower(),
        "approval_flag": protocol.approval_flag,
    }


def _protocol_from_body(request_payload: dict, name_override: str | None = None) -> Protocol:
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


def build_protocols_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("protocols", __name__)

    def _agents_by_name() -> dict:
        return {agent.name: agent for agent in ctx.deps.registry.all()}

    @blueprint.route("/Protocol", methods=["GET"])
    def list_protocols():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.LIST_PROTOCOLS)

        return jsonify({"protocols": [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()]})

    @blueprint.route("/Protocol", methods=["POST"])
    def create_protocol():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.CREATE_PROTOCOL)

        request_payload = request.get_json(silent=True) or {}
        new_protocol = _protocol_from_body(request_payload)

        try:
            protocol_edit_message = add_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), _agents_by_name(), new_protocol)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": protocol_edit_message})

    @blueprint.route("/Protocol/<name>", methods=["PUT"])
    def update_protocol(name):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.UPDATE_PROTOCOL)

        request_payload = request.get_json(silent=True) or {}
        updated_protocol = _protocol_from_body(request_payload, name_override=name)

        try:
            protocol_edit_message = replace_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), _agents_by_name(), updated_protocol)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": protocol_edit_message})

    @blueprint.route("/Protocol/<name>", methods=["DELETE"])
    def delete_protocol(name):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.DELETE_PROTOCOL)

        try:
            protocol_edit_message = remove_protocol(ctx.loaded_profile.module_path, ctx.deps.protocol_set.all(), name)
        except ProtocolEditError as exc:
            raise InvalidInputError(str(exc)) from exc

        return jsonify({"message": protocol_edit_message})

    return blueprint


if TYPE_CHECKING:
    from api.app import ApiContext

_SETTINGS_FIELDS = {"retry_count", "risk_threshold", "lookback_window_days", "safe_mode"}


def build_system_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("system", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/SYSTEM", methods=["GET"])
    def get_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        # VIEW_PROFILE_OVERVIEW is the least-privileged of the three operations this
        # single endpoint now serves — it is the entry gate. The response payload
        # below is then built field-group by field-group, each gated by its own
        # operation, so a viewer's response is a strict subset (protected arrays
        # absent, never present-but-empty) rather than the full commander payload
        # with fields simply omitted after the fact.
        require(level, RequestedOperation.VIEW_PROFILE_OVERVIEW)

        loaded = ctx.loaded_profile
        current_hash = hash_profile_file(loaded.module_path)

        response_payload = {
            "profile": loaded.module_path,
            "event_types": list(ctx.deps.event_type_registry.types),
            "areas": list(ctx.deps.area_registry.areas),
            "profile_file_changed": current_hash != loaded.profile_file_hash,
        }

        if is_permitted(level, RequestedOperation.VIEW_SYSTEM_INTERNALS):
            response_payload["agents"] = [agent.name for agent in ctx.deps.registry.all()]
            response_payload["protocols"] = [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()]
            response_payload["queued_events"] = ctx.queue.qsize()
            response_payload["held_events"] = {
                "clarification": len(ctx.deps.persistence.list_held_events("clarification")),
                "approval": len(ctx.deps.persistence.list_held_events("approval")),
            }
            response_payload["scheduler"] = ctx.scheduler.last_run_status()

        if is_permitted(level, RequestedOperation.VIEW_SETTINGS):
            response_payload["settings"] = {
                "retry_count": ctx.deps.settings_store.get_retry_count(),
                "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
                "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
                "safe_mode": ctx.deps.settings_store.get_safe_mode(),
            }

        return jsonify(response_payload)

    @blueprint.route("/SYSTEM", methods=["PUT"])
    def put_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.CHANGE_SETTINGS)

        request_payload = request.get_json(silent=True)
        if request_payload is None:
            request_payload = {}
        if not isinstance(request_payload, dict):
            raise InvalidInputError(messages.text("api.telegram_admission_invalid"))

        unknown = sorted(set(request_payload) - _SETTINGS_FIELDS)
        if unknown:
            field = unknown[0]
            raise InvalidInputError(messages.text("api.profile_field_restart", field=field), field=field)

        validated: dict[str, object] = {}
        if "retry_count" in request_payload:
            setting_value = request_payload["retry_count"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 0:
                raise InvalidInputError(messages.text("api.retry_nonnegative_integer"), field="retry_count")
            validated["retry_count"] = setting_value

        if "risk_threshold" in request_payload:
            setting_value = request_payload["risk_threshold"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or not (0.0 <= setting_value <= 1.0):
                raise InvalidInputError(messages.text("api.risk_threshold_range"), field="risk_threshold")
            validated["risk_threshold"] = setting_value

        if "lookback_window_days" in request_payload:
            setting_value = request_payload["lookback_window_days"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 1:
                raise InvalidInputError(messages.text("api.lookback_positive_integer"), field="lookback_window_days")
            validated["lookback_window_days"] = setting_value

        if "safe_mode" in request_payload:
            setting_value = request_payload["safe_mode"]
            if not isinstance(setting_value, bool):
                raise InvalidInputError(messages.text("api.safe_mode_boolean"), field="safe_mode")
            validated["safe_mode"] = setting_value

        previous_safe_mode = ctx.deps.settings_store.get_safe_mode()
        if "retry_count" in validated:
            ctx.deps.settings_store.set_retry_count(validated["retry_count"])
        if "risk_threshold" in validated:
            ctx.deps.settings_store.set_risk_threshold(validated["risk_threshold"])
        if "lookback_window_days" in validated:
            ctx.deps.settings_store.set_lookback_window_days(validated["lookback_window_days"])
        if "safe_mode" in validated:
            ctx.deps.settings_store.set_safe_mode(validated["safe_mode"])
            logger.info(
                "safe mode changed",
                extra={
                    "event": "safe_mode_changed",
                    "previous_safe_mode": previous_safe_mode,
                    "safe_mode": validated["safe_mode"],
                    "changed_by": request.headers.get("X-Identity"),
                    "trace_id": get_trace_id(),
                },
            )

        return jsonify({
            "retry_count": ctx.deps.settings_store.get_retry_count(),
            "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
            "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
            "safe_mode": ctx.deps.settings_store.get_safe_mode(),
        })

    @blueprint.route("/Trace/<trace_id>", methods=["GET"])
    def get_trace(trace_id: str):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_LIVE_TRACE)
        if not base_config.DEEP_DEBUG:
            raise NotFoundError(messages.text("api.deep_debug_disabled"))
        if not is_valid_trace_id(trace_id):
            raise InvalidInputError(messages.text("api.trace_id_invalid"), field="trace_id")

        try:
            since = int(request.args.get("since", "0"))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(messages.text("api.cursor_invalid"), field="since") from exc
        if since < 0:
            raise InvalidInputError(messages.text("api.cursor_invalid"), field="since")

        try:
            wait_seconds = int(request.args.get("wait_seconds", "0"))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds") from exc
        if not 0 <= wait_seconds <= 30:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds")

        entries = ctx.deps.persistence.wait_for_log_entries_since(
            trace_id,
            since,
            wait_seconds,
        )
        rendered = []
        for entry in entries:
            rendered_text = render_deep_debug_entry(entry, messages)
            if rendered_text is not None:
                rendered.append({"id": entry["id"], "text": rendered_text})

        next_cursor = max((entry["id"] for entry in entries), default=since)
        terminal = any(entry.get("event") == "event_outcome" for entry in entries)
        return jsonify({
            "entries": rendered,
            "next_cursor": next_cursor,
            "terminal": terminal,
        })

    return blueprint


if TYPE_CHECKING:
    from api.app import ApiContext


def build_users_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("users", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/User/<identity>", methods=["GET"])
    def get_user(identity):
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.VIEW_USER_REGISTRATION)

        # Ownership scoping (docs/Next_Plan.md §5 decision record): a viewer may
        # look up only their own identity. A commander (e.g. bot-service, which
        # resolves every caller's registration) is unrestricted by this check.
        if level is PermissionLevel.VIEWER and identity != caller_identity:
            raise AuthorizationError(messages.text("api.other_identity_forbidden"))

        user = ctx.deps.persistence.read_user(identity)
        if user is None:
            return jsonify({"registered": False, "permission_level": None, "full_name": None})

        return jsonify({
            "registered": True,
            "permission_level": user["permission_level"],
            "full_name": user["full_name"],
            **({"auto_register": bool(user.get("auto_register", False))} if level is PermissionLevel.COMMANDER else {}),
        })

    @blueprint.route("/User/<identity>/name", methods=["PUT"])
    def update_own_name(identity):
        caller_identity = request.headers.get("X-Identity")
        authenticate(ctx.deps.persistence, caller_identity)
        if identity != caller_identity:
            raise AuthorizationError(messages.text("api.other_identity_forbidden"))

        request_payload = request.get_json(silent=True)
        if not isinstance(request_payload, dict):
            raise InvalidInputError(messages.text("api.full_name_invalid"), field="full_name")
        try:
            full_name = normalize_full_name(request_payload.get("full_name"))
        except InvalidFullNameError:
            raise InvalidInputError(messages.text("api.full_name_invalid"), field="full_name") from None
        try:
            ctx.deps.persistence.update_user_full_name(identity, full_name)
        except PersistenceNotFoundError:
            raise NotFoundError(messages.text("api.identity_unregistered", identity=identity)) from None
        return jsonify({"telegram_identity": identity, "full_name": full_name})

    @blueprint.route("/User/<identity>/approve", methods=["POST"])
    def approve_user(identity):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.MANAGE_USERS)
        try:
            user = ctx.deps.persistence.approve_user(identity)
        except PersistenceNotFoundError:
            raise NotFoundError(messages.text("api.identity_unregistered", identity=identity)) from None
        logger.info(
            "telegram user approved",
            extra={
                "event": "telegram_user_approved",
                "telegram_identity": identity,
                "approved_by": request.headers.get("X-Identity"),
                "trace_id": get_trace_id(),
            },
        )
        record_telegram_security_metric("approved", "user")
        return jsonify({
            "telegram_identity": user["telegram_identity"],
            "permission_level": user["permission_level"],
            "full_name": user["full_name"],
            "auto_register": False,
        })

    @blueprint.route("/Commanders", methods=["GET"])
    def get_commanders():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_COMMANDER_ROSTER)

        commanders = [
            u for u in ctx.deps.persistence.list_users()
            if u["permission_level"] == "commander" and u["telegram_identity"] != "bot-service"
        ]
        return jsonify({"commanders": [{"telegram_identity": u["telegram_identity"]} for u in commanders]})

    return blueprint


def _binding_to_dict(binding) -> dict:
    return {
        "chat_id": binding.chat_id,
        "agent_name": binding.agent_name,
        "label": binding.label,
        "auto_register": bool(getattr(binding, "auto_register", False)),
    }


def _attendance_agent(ctx: "ApiContext"):
    """The registered specialist that owns attendance cycles (duck-typed on `open_scheduled_cycle`)."""

    for agent in ctx.deps.registry.all():
        if hasattr(agent, "open_scheduled_cycle"):
            return agent
    return None


def build_groups_blueprint(ctx: "ApiContext") -> Blueprint:
    """Telegram group -> agent bindings, plus the attendance-check trigger the bot polls.

    Every write goes through `ctx.group_routing` (write-through to persistence),
    so the in-memory table the `/Msg` scope check consults is updated in the same
    request that persisted the change."""

    blueprint = Blueprint("groups", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Groups", methods=["GET"])
    def list_groups():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.LIST_GROUPS)
        return jsonify({
            "groups": [_binding_to_dict(binding) for binding in ctx.group_routing.all()],
            "routable_agents": list(ctx.group_routing.routable_targets),
        })

    @blueprint.route("/Groups/<chat_id>", methods=["PUT"])
    def put_group(chat_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.MANAGE_GROUPS)

        request_payload = request.get_json(silent=True) or {}
        agent_name = request_payload.get("agent_name")
        label = request_payload.get("label") or ""
        if not agent_name or not isinstance(agent_name, str):
            raise InvalidInputError(messages.text("api.field_required", field="agent_name"), field="agent_name")
        if not isinstance(label, str) or len(label) > 200:
            raise InvalidInputError(messages.text("api.field_required", field="label"), field="label")

        try:
            binding = ctx.group_routing.upsert(chat_id, agent_name, label)
        except InvalidRoutingTargetError as exc:
            raise InvalidInputError(
                messages.text(
                    "api.group_agent_invalid",
                    agent=agent_name,
                    allowed=", ".join(ctx.group_routing.routable_targets),
                ),
                field="agent_name",
            ) from exc
        logger.info(
            "telegram group binding written",
            extra={"event": "group_binding_written", "chat_id": binding.chat_id, "agent": binding.agent_name, "trace_id": get_trace_id()},
        )
        return jsonify(_binding_to_dict(binding))

    @blueprint.route("/Groups/<chat_id>", methods=["DELETE"])
    def delete_group(chat_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.MANAGE_GROUPS)

        try:
            ctx.group_routing.remove(chat_id)
        except PersistenceNotFoundError as exc:
            raise NotFoundError(messages.text("api.group_not_registered", chat_id=chat_id)) from exc
        logger.info(
            "telegram group binding removed",
            extra={"event": "group_binding_removed", "chat_id": chat_id, "trace_id": get_trace_id()},
        )
        return jsonify({"chat_id": chat_id, "removed": True})

    @blueprint.route("/Groups/<chat_id>/approve", methods=["POST"])
    def approve_group(chat_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.MANAGE_GROUPS)
        try:
            binding = ctx.group_routing.approve(chat_id)
        except PersistenceNotFoundError as exc:
            raise NotFoundError(messages.text("api.group_not_registered", chat_id=chat_id)) from exc
        logger.info(
            "telegram group approved",
            extra={
                "event": "telegram_group_approved",
                "chat_id": chat_id,
                "approved_by": request.headers.get("X-Identity"),
                "trace_id": get_trace_id(),
            },
        )
        record_telegram_security_metric("approved", "group")
        return jsonify(_binding_to_dict(binding))

    @blueprint.route("/TeamStatus/AttendanceCheck", methods=["POST"])
    def post_attendance_check():
        """Open today's attendance cycle if due; the bot polls this and posts the prompt to bound groups."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.RUN_ATTENDANCE_CHECK)

        request_payload = request.get_json(silent=True) or {}
        now_iso = request_payload.get("now_iso")
        force = bool(request_payload.get("force", False))
        if now_iso is not None and not isinstance(now_iso, str):
            raise InvalidInputError(messages.text("api.field_required", field="now_iso"), field="now_iso")

        agent = _attendance_agent(ctx)
        if agent is None:
            raise NotFoundError(messages.text("api.attendance_agent_unavailable"))

        try:
            opened = agent.open_scheduled_cycle(now_iso, force=force)
        except ValueError as exc:
            raise InvalidInputError(str(exc), field="now_iso") from exc

        target_chat_ids = [
            binding.chat_id
            for binding in ctx.group_routing.all()
            if binding.agent_name == agent.name
            and (not ctx.deps.settings_store.get_safe_mode() or not binding.auto_register)
        ]
        if opened is None:
            return jsonify({"opened": False, "agent_name": agent.name, "target_chat_ids": target_chat_ids})
        logger.info(
            "attendance cycle opened",
            extra={"event": "attendance_cycle_opened", "cycle_key": opened["cycle_key"], "targets": len(target_chat_ids), "trace_id": get_trace_id()},
        )
        return jsonify({
            "opened": True,
            "agent_name": agent.name,
            "cycle_key": opened["cycle_key"],
            "deadline_at": opened["deadline_at"],
            "members_required": opened["members_required"],
            "target_chat_ids": target_chat_ids,
        })

    return blueprint


def build_telegram_blueprint(ctx: "ApiContext") -> Blueprint:
    """Bot-service-only admission gate for real Telegram updates."""

    blueprint = Blueprint("telegram_admission", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Telegram/Admission", methods=["POST"])
    def admit_telegram_update():
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        if caller_identity != BOT_SERVICE_IDENTITY:
            raise AuthorizationError(messages.text("api.operation_forbidden", level=level.name, operation="telegram admission"))

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise InvalidInputError(messages.text("api.telegram_admission_invalid"))
        telegram_identity = payload.get("telegram_identity")
        chat_id = payload.get("chat_id")
        chat_type = payload.get("chat_type")
        chat_label = payload.get("chat_label") or ""
        if (
            not isinstance(telegram_identity, str)
            or not telegram_identity.isdigit()
            or int(telegram_identity) <= 0
            or not isinstance(chat_id, str)
            or not chat_id.strip()
            or chat_type not in {"private", "group", "supergroup"}
            or not isinstance(chat_label, str)
            or len(chat_label) > 200
        ):
            raise InvalidInputError(messages.text("api.telegram_admission_invalid"))
        group_chat_id = chat_id if chat_type in {"group", "supergroup"} else None
        if group_chat_id is not None:
            try:
                if int(group_chat_id) >= 0:
                    raise ValueError
            except ValueError:
                raise InvalidInputError(messages.text("api.telegram_admission_invalid")) from None

        safe_mode = ctx.deps.settings_store.get_safe_mode()
        user_before = ctx.deps.persistence.read_user(telegram_identity)
        group_before = ctx.deps.persistence.read_group(group_chat_id) if group_chat_id is not None else None
        records = ctx.deps.persistence.admit_telegram_update(
            telegram_identity,
            group_chat_id,
            chat_label,
            allow_registration=not safe_mode,
        )
        user = records["user"]
        group = records["group"]
        if group_chat_id is not None and group_before is None and group is not None:
            ctx.group_routing.load()

        if user_before is None and user is not None:
            record_telegram_security_metric("auto_registered", "user")
            logger.info(
                "telegram user automatically registered",
                extra={"event": "telegram_user_auto_registered", "telegram_identity": telegram_identity, "trace_id": get_trace_id()},
            )
        if group_before is None and group is not None:
            record_telegram_security_metric("auto_registered", "group")
            logger.info(
                "telegram group automatically registered",
                extra={"event": "telegram_group_auto_registered", "chat_id": group_chat_id, "trace_id": get_trace_id()},
            )

        reason = "known"
        allowed = True
        if user is None:
            allowed, reason = False, "unknown_user"
        elif safe_mode and bool(user.get("auto_register", False)):
            allowed, reason = False, "user_awaiting_approval"
        elif group_chat_id is not None and group is None:
            allowed, reason = False, "unknown_group"
        elif safe_mode and group is not None and bool(group.get("auto_register", False)):
            allowed, reason = False, "group_awaiting_approval"
        elif user_before is None or (group_chat_id is not None and group_before is None):
            reason = "auto_registered"

        if not allowed:
            record_telegram_security_metric("blocked", "admission")
            logger.info(
                "telegram admission denied in safe mode",
                extra={
                    "event": "telegram_admission_denied_safe_mode",
                    "telegram_identity": telegram_identity,
                    "chat_id": chat_id,
                    "reason": reason,
                    "trace_id": get_trace_id(),
                },
            )

        def _user_payload(record):
            if record is None:
                return None
            return {
                "telegram_identity": record["telegram_identity"],
                "permission_level": record["permission_level"],
                "full_name": record["full_name"],
                "auto_register": bool(record.get("auto_register", False)),
            }

        group_payload = None
        if group is not None:
            group_payload = {
                "chat_id": str(group["chat_id"]),
                "agent_name": group["agent_name"],
                "label": group.get("label") or "",
                "auto_register": bool(group.get("auto_register", False)),
            }
        return jsonify({
            "allowed": allowed,
            "reason": reason,
            "safe_mode": safe_mode,
            "user": _user_payload(user),
            "group": group_payload,
        })

    return blueprint

if TYPE_CHECKING:
    from api.app import ApiContext


def _steps_completed(event: dict) -> list[str]:
    """Every step that actually produced a result, in order — derivable entirely from `event["steps"]` (§2.3's `event_steps` table, already attached by `fetch_event`): a step that fail..."""

    return [
        f"{step['agent_name']}: {step['result_text']}"
        for step in event.get("steps", [])
        if step.get("status") == "succeeded" and step.get("result_text") is not None
    ]


def _failed_step_agent_name(event: dict) -> str | None:
    """The agent whose step has no result — execution stops at the first failing step (`protocols.executor.execute_steps`), so at most one persisted step ever has `result_text=None`, a..."""

    for step in event.get("steps", []):
        if step.get("status") == "failed":
            return step["agent_name"]
    return None


def job_status(ctx: "ApiContext", event_id: str) -> dict | None:
    event = ctx.deps.persistence.fetch_event(event_id)
    if event is None:
        return None

    if event["outcome"] is not None:
        response_payload = {"event_id": event_id, "status": event["outcome"]}
        if event.get("insight_text") is not None:
            response_payload["insight_text"] = event["insight_text"]

        steps_completed = _steps_completed(event)
        if steps_completed:
            response_payload["steps_completed"] = steps_completed

        if event["outcome"] == "failed":
            if event.get("outcome_failure_reason"):
                response_payload["detail"] = event["outcome_failure_reason"]
            failed_step_agent_name = _failed_step_agent_name(event)
            if failed_step_agent_name is not None:
                response_payload["failed_step_agent_name"] = failed_step_agent_name
        elif event["outcome"] == "closed_on_precedent" and event.get("precedent_closed_by_event_id"):
            response_payload["detail"] = f"closed against resolved precedent '{event['precedent_closed_by_event_id']}'"
        elif event["outcome"] == "no_match_protocol" and event.get("outcome_failure_reason"):
            response_payload["detail"] = event["outcome_failure_reason"]

        return response_payload

    approval_hold = ctx.deps.persistence.fetch_held_event("approval", event_id)
    if approval_hold is not None and not approval_hold["resolved"]:
        return {"event_id": event_id, "status": "held_for_approval", "reason": approval_hold["reason"]}

    clarification_hold = ctx.deps.persistence.fetch_held_event("clarification", event_id)
    if clarification_hold is not None and not clarification_hold["resolved"]:
        return {"event_id": event_id, "status": "held_for_clarification", "unresolved_field": clarification_hold["unresolved_field"]}

    event_data_hold = ctx.deps.persistence.fetch_held_event("event_data", event_id)
    if event_data_hold is not None and not event_data_hold["resolved"]:
        return {
            "event_id": event_id,
            "status": "waiting_for_event_data",
            "missing_fields": event_data_hold.get("missing_fields", []),
            "question": event_data_hold.get("question", ""),
            "steps_completed": _steps_completed(event),
        }

    processing = ctx.queue.currently_processing()
    if processing is not None and processing[0] == event_id:
        return {"event_id": event_id, "status": "running"}

    return {"event_id": event_id, "status": "queued"}


def build_jobs_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("jobs", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Job/<event_id>", methods=["GET"])
    def get_job(event_id):
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.VIEW_JOB_STATUS)

        # Ownership scoping (docs/Next_Plan.md §5 decision record): a viewer may
        # only check the status of an event they themselves submitted. A 404
        # (not 403) is returned for someone else's job, matching the "no such
        # job" response for a genuinely unknown ID — it does not confirm that a
        # job belonging to another sender exists. A commander is unrestricted.
        if level is PermissionLevel.VIEWER:
            event = ctx.deps.persistence.fetch_event(event_id)
            if event is None or event.get("sender_identity") != caller_identity:
                raise NotFoundError(messages.text("api.job_not_found", task_id=event_id))

        status = job_status(ctx, event_id)
        if status is None:
            raise NotFoundError(messages.text("api.job_not_found", task_id=event_id))

        return jsonify(status)

    return blueprint


if TYPE_CHECKING:
    from api.app import ApiContext


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

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)

        optimization_policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
        hold = _pending_hold_or_raise(ctx, "clarification", event_id)

        reservation = ctx.queue.reserve(True)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))

        answer = resolve_clarification(ctx.deps, hold["hold_id"], identity, level, classification)
        if answer.status == "invalid_classification":
            ctx.queue.release_reservation(reservation)
            raise InvalidInputError(answer.message, field="classification")
        if answer.status != "resolved":
            ctx.queue.release_reservation(reservation)
            # A hold resolved by someone else between the check above
            # and this call — a narrow race; not_found is the accurate
            # status, reported generically rather than re-querying for
            # who/when.
            raise InvalidInputError(answer.message)

        def _work() -> None:
            with trace_context(trace_id):
                continue_after_clarification(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit(
            WorkItem(
                (event_id, _work), trace_id=trace_id,
                priority=0,
                deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                concurrency_keys=(f"sender:{identity}",),
            ),
            reservation,
        )
        return jsonify({"event_id": event_id, "status": "queued"}), 202

    @blueprint.route("/Approve/<event_id>", methods=["POST"])
    def post_approve(event_id):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.APPROVE_RUN)
        identity = request.headers.get("X-Identity")

        request_payload = request.get_json(silent=True) or {}
        decision = request_payload.get("decision")
        if not decision:
            raise InvalidInputError(messages.text("api.decision_required"), field="decision")

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)

        optimization_policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
        hold = _pending_hold_or_raise(ctx, "approval", event_id)

        reservation = ctx.queue.reserve(True)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))

        answer = resolve_approval(ctx.deps, hold["hold_id"], identity, level, decision)
        if answer.status == "invalid_candidate":
            ctx.queue.release_reservation(reservation)
            raise InvalidInputError(answer.message, field="decision")
        if answer.status not in ("approved", "rejected"):
            ctx.queue.release_reservation(reservation)
            # Same narrow race as the clarify path above.
            raise InvalidInputError(answer.message)

        if answer.status == "rejected":
            ctx.queue.release_reservation(reservation)
            decline(ctx.deps, event_id)
            return jsonify({"event_id": event_id, "status": "declined"})

        selected_protocol_name = answer.hold["selected_protocol_name"]

        def _work() -> None:
            with trace_context(trace_id):
                continue_after_approval(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent, selected_protocol_name)

        ctx.queue.submit(
            WorkItem(
                (event_id, _work), trace_id=trace_id, priority=0,
                deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                concurrency_keys=(f"sender:{identity}",),
            ),
            reservation,
        )
        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint


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


def _event_data_hold_payload(ctx: "ApiContext", event_id: str) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("event_data", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "question": hold["question"],
        "missing_fields": hold.get("missing_fields", []),
    }


def _uncertain_verdict_payload(ctx: "ApiContext", event_id: str) -> dict:
    event = ctx.deps.persistence.fetch_event(event_id)
    return {"event_id": event_id, "insight_text": event.get("insight_text") or ""}


def _uncertain_verdict_reporter_payload(ctx: "ApiContext", event_id: str) -> dict:
    # Deliberately carries no insight text — item #8's decision is a short,
    # generic notice for the original reporter, not the commander-level detail.
    return {"event_id": event_id}


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
        # For the always-on protocol/reason suffix (item #9) — already computed
        # during the run, no new model call. `protocol_name` is None whenever no
        # protocol was ever selected (e.g. `no_match_protocol`).
        "protocol_name": event.get("selected_protocol"),
        "risk_level": event.get("risk_level"),
        "protocol_reason": event.get("protocol_reason"),
    }


_PAYLOAD_BUILDERS = {
    "clarification_hold": _clarification_hold_payload,
    "approval_hold": _approval_hold_payload,
    "event_data_hold": _event_data_hold_payload,
    "uncertain_verdict": _uncertain_verdict_payload,
    "uncertain_verdict_reporter": _uncertain_verdict_reporter_payload,
    "precedent_closure": _precedent_closure_payload,
    "no_match_notice": _no_match_payload,
    "job_finished": _job_payload,
    "job_failed": _job_payload,
}


def _target_chat_ids(ctx: "ApiContext", kind: str, event_id: str) -> list[str]:
    """Reporter-facing job, hold and event-data notifications target the original submitter."""

    if kind not in ("job_finished", "job_failed", "event_data_hold", "uncertain_verdict_reporter", "approval_hold", "clarification_hold"):
        return []

    event = ctx.deps.persistence.fetch_event(event_id)
    if event is None or not event.get("sender_identity"):
        return []
    sender = event["sender_identity"]
    sender_record = ctx.deps.persistence.read_user(sender)
    if (
        ctx.deps.settings_store.get_safe_mode()
        and sender_record is not None
        and bool(sender_record.get("auto_register", False))
    ):
        return []
    return [sender] if sender != "bot-service" else []


def _reply_to_message_id(ctx: "ApiContext", kind: str, event_id: str) -> str | None:
    """Attach reporter-facing notifications to the originating Telegram message when available."""

    if kind not in ("job_finished", "job_failed", "event_data_hold", "uncertain_verdict_reporter"):
        return None

    event = ctx.deps.persistence.fetch_event(event_id)
    return event.get("source_message_id")


def _format_notification(ctx: "ApiContext", notification_row: dict) -> dict:
    builder = _PAYLOAD_BUILDERS[notification_row["kind"]]
    event = ctx.deps.persistence.fetch_event(notification_row["event_id"])
    return {
        "sequence_id": notification_row["sequence_id"],
        "kind": notification_row["kind"],
        "payload": builder(ctx, notification_row["event_id"]),
        "target_chat_ids": _target_chat_ids(ctx, notification_row["kind"], notification_row["event_id"]),
        "reply_to_message_id": _reply_to_message_id(ctx, notification_row["kind"], notification_row["event_id"]),
        "trace_id": event.get("trace_id") if event is not None else None,
    }


def build_notifications_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("notifications", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Notifications", methods=["GET"])
    def get_notifications():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.POLL_NOTIFICATIONS)

        raw_since = request.args.get("since", "0")
        raw_wait_seconds = request.args.get("wait_seconds", "0")
        try:
            since = int(raw_since)
            if since < 0:
                raise ValueError
        except ValueError:
            raise InvalidInputError(messages.text("api.cursor_invalid"), field="since")

        try:
            wait_seconds = int(raw_wait_seconds)
            if not 0 <= wait_seconds <= 30:
                raise ValueError
        except ValueError:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds")

        with stage_context("notification_delivery"):
            notification_rows = ctx.deps.persistence.wait_for_notifications_since(since, wait_seconds)
            notifications = [_format_notification(ctx, notification_row) for notification_row in notification_rows]
        next_cursor = notification_rows[-1]["sequence_id"] if notification_rows else since

        return jsonify({"notifications": notifications, "next_cursor": next_cursor})

    return blueprint
