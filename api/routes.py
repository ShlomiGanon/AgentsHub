"""Consolidated responsibility module for routes."""

from datetime import datetime, timedelta, timezone
import time

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import AuthorizationError, ConflictError, InvalidInputError, NotFoundError, RunFailureError, ServiceUnavailableError, authenticate, require
from history import storage_timestamp

from orchestrator.flows import begin_report, run_report_extraction

from tools import (
    get_trace_id,
    is_valid_trace_id,
    new_trace_id,
    render_deep_debug_entry,
    set_trace_id,
    stage_context,
    trace_context,
)
from config import environment as base_config

import logging

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from agents import set_invocation_deadline

from orchestrator.flows import (
    OrchestrationParseError,
    answer_conversationally,
    answer_question,
    answer_question_from_plan,
    apply_event_data_reply,
    build_role_aware_system_context,
    begin_report,
    begin_request,
    classify_intent,
    plan_message,
    WorkItem,
    continue_from_risk_assessment,
    run_report_extraction,
    resume_after_event_data,
)

from protocols import CriticalityLevel, Protocol, ProtocolEditError, add_protocol, remove_protocol, replace_protocol

from profiles.loader import hash_profile_file
from profiles import HUMAN_ACTIVATION_TYPE, OptimizationPolicy

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
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
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

        reservation = ctx.queue.reserve(False)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)
        deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
        try:
            event_id = begin_report(ctx.deps, text, "sensor", _now(), sender_identity, deadline_at=deadline_at)
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


def build_messages_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("messages", __name__)
    messages = ctx.loaded_profile.message_catalog
    non_human_activation_event_types = tuple(
        event_type
        for event_type in ctx.deps.event_type_registry.types
        if event_type != HUMAN_ACTIVATION_TYPE
    )
    areas = tuple(ctx.deps.area_registry.areas)

    @blueprint.route("/Msg", methods=["POST"])
    def post_msg():
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.SUBMIT_MESSAGE)

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

        request_payload = request.get_json(silent=True) or {}
        text = request_payload.get("text")
        sender_identity = request_payload.get("sender_identity")
        source_message_id = request_payload.get("source_message_id")
        conversation_id = request_payload.get("conversation_id")

        if not text:
            raise InvalidInputError(messages.text("api.field_required", field="text"), field="text")
        if not sender_identity:
            raise InvalidInputError(
                messages.text("api.field_required", field="sender_identity"), field="sender_identity"
            )
        if conversation_id is not None and (not isinstance(conversation_id, str) or not conversation_id.strip() or len(conversation_id) > 200):
            raise InvalidInputError(messages.text("api.conversation_id_invalid"), field="conversation_id")

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

        matching_event_data_hold = False
        if conversation_id is not None:
            for pending_hold in reversed(ctx.deps.persistence.list_held_events("event_data")):
                pending_event = ctx.deps.persistence.fetch_event(pending_hold["event_id"])
                if (
                    pending_event is not None
                    and pending_event.get("conversation_id") == conversation_id
                    and pending_event.get("sender_identity") == caller_identity
                ):
                    matching_event_data_hold = True
                    break

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
                )
            except OrchestrationParseError as exc:
                ctx.queue.release_reservation(reservation)
                logger.warning(
                    "event data reply failed validation",
                    extra={"event": "event_data_reply_invalid", "reason": str(exc), "trace_id": trace_id},
                )
                question = pending_hold.get("question", messages.text("api.event_detail_again"))
                _remember("assistant", question, pending_hold["event_id"])
                return jsonify(
                    {
                        "taken_as": "clarification",
                        "event_id": pending_hold["event_id"],
                        "answer": question,
                        "status": "waiting_for_event_data",
                    }
                )
            except Exception:
                ctx.queue.release_reservation(reservation)
                raise
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
            )
        except Exception:
            ctx.queue.release_reservation(reservation)
            raise

        def _work() -> None:
            with trace_context(trace_id):
                continue_from_risk_assessment(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent, is_commander)

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

_SETTINGS_FIELDS = {"retry_count", "risk_threshold", "lookback_window_days"}


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
            }

        return jsonify(response_payload)

    @blueprint.route("/SYSTEM", methods=["PUT"])
    def put_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.CHANGE_SETTINGS)

        request_payload = request.get_json(silent=True) or {}

        unknown = sorted(set(request_payload) - _SETTINGS_FIELDS)
        if unknown:
            field = unknown[0]
            raise InvalidInputError(messages.text("api.profile_field_restart", field=field), field=field)

        if "retry_count" in request_payload:
            setting_value = request_payload["retry_count"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 0:
                raise InvalidInputError(messages.text("api.retry_nonnegative_integer"), field="retry_count")
            ctx.deps.settings_store.set_retry_count(setting_value)

        if "risk_threshold" in request_payload:
            setting_value = request_payload["risk_threshold"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or not (0.0 <= setting_value <= 1.0):
                raise InvalidInputError(messages.text("api.risk_threshold_range"), field="risk_threshold")
            ctx.deps.settings_store.set_risk_threshold(setting_value)

        if "lookback_window_days" in request_payload:
            setting_value = request_payload["lookback_window_days"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 1:
                raise InvalidInputError(messages.text("api.lookback_positive_integer"), field="lookback_window_days")
            ctx.deps.settings_store.set_lookback_window_days(setting_value)

        return jsonify({
            "retry_count": ctx.deps.settings_store.get_retry_count(),
            "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
            "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
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
            return jsonify({"registered": False, "permission_level": None})

        return jsonify({"registered": True, "permission_level": user["permission_level"]})

    @blueprint.route("/Commanders", methods=["GET"])
    def get_commanders():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_COMMANDER_ROSTER)

        commanders = [u for u in ctx.deps.persistence.list_users() if u["permission_level"] == "commander"]
        return jsonify({"commanders": [{"telegram_identity": u["telegram_identity"]} for u in commanders]})

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
    }


_PAYLOAD_BUILDERS = {
    "clarification_hold": _clarification_hold_payload,
    "approval_hold": _approval_hold_payload,
    "event_data_hold": _event_data_hold_payload,
    "uncertain_verdict": _uncertain_verdict_payload,
    "precedent_closure": _precedent_closure_payload,
    "no_match_notice": _no_match_payload,
    "job_finished": _job_payload,
    "job_failed": _job_payload,
}


def _target_chat_ids(ctx: "ApiContext", kind: str, event_id: str) -> list[str]:
    """Reporter-facing job and event-data notifications target the original submitter."""

    if kind not in ("job_finished", "job_failed", "event_data_hold"):
        return []

    event = ctx.deps.persistence.fetch_event(event_id)
    return [event["sender_identity"]]


def _reply_to_message_id(ctx: "ApiContext", kind: str, event_id: str) -> str | None:
    """Attach reporter-facing notifications to the originating Telegram message when available."""

    if kind not in ("job_finished", "job_failed", "event_data_hold"):
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
