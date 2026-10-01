"""Consolidated responsibility module for routes."""

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

def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))

_STORE_KEYS_BY_AGENT = {
    "roster_agent": "store:roster",
    "team_status_agent": "store:roster",
    "surveillance_agent": "store:cameras",
    "neighboring_forces_agent": "store:forces",
}

def _work_concurrency_keys(sender_identity: str, scoped_agent: str | None = None) -> tuple[str, ...]:
    keys = [f"sender:{sender_identity}"]
    store_key = _STORE_KEYS_BY_AGENT.get(scoped_agent or "")
    if store_key:
        keys.append(store_key)
    return tuple(keys)

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

        received_at = _now()
        raw_timestamp = request_payload.get("timestamp")
        if raw_timestamp is not None and raw_timestamp != "":
            if not isinstance(raw_timestamp, str):
                raise InvalidInputError(
                    messages.text("api.invalid_timestamp", field="timestamp"), field="timestamp"
                )
            try:
                received_at = storage_timestamp(parse_timestamp(raw_timestamp))
            except (ValueError, TypeError):
                raise InvalidInputError(
                    messages.text("api.invalid_timestamp", field="timestamp"), field="timestamp"
                )

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)
        deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
        try:
            event_id = begin_report(
                ctx.deps,
                text,
                "sensor",
                received_at,
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
                concurrency_keys=_work_concurrency_keys(sender_identity, "surveillance_agent"),
            ),
            reservation,
        )

        return jsonify({"event_id": event_id, "status": "queued", "trace_id": trace_id}), 202

    return blueprint

logger = logging.getLogger(__name__)

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

_SETTINGS_FIELDS = {
    "retry_count", "risk_threshold", "lookback_window_days", "safe_mode", "rich_reports_enabled",
    "hold_reminder_minutes", "hold_escalation_minutes", "hold_expiry_hours",
}

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
            if ctx.hold_sweep_scheduler is not None:
                response_payload["hold_sweep_scheduler"] = ctx.hold_sweep_scheduler.last_run_status()

        if is_permitted(level, RequestedOperation.VIEW_SETTINGS):
            response_payload["settings"] = {
                "retry_count": ctx.deps.settings_store.get_retry_count(),
                "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
                "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
                "safe_mode": ctx.deps.settings_store.get_safe_mode(),
                "rich_reports_enabled": ctx.deps.settings_store.get_rich_reports_enabled(),
                "hold_reminder_minutes": ctx.deps.settings_store.get_hold_reminder_minutes(),
                "hold_escalation_minutes": ctx.deps.settings_store.get_hold_escalation_minutes(),
                "hold_expiry_hours": ctx.deps.settings_store.get_hold_expiry_hours(),
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

        if "rich_reports_enabled" in request_payload:
            setting_value = request_payload["rich_reports_enabled"]
            if not isinstance(setting_value, bool):
                raise InvalidInputError(messages.text("api.rich_reports_enabled_boolean"), field="rich_reports_enabled")
            validated["rich_reports_enabled"] = setting_value

        if "hold_reminder_minutes" in request_payload:
            setting_value = request_payload["hold_reminder_minutes"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or setting_value <= 0:
                raise InvalidInputError(messages.text("api.hold_reminder_minutes_positive"), field="hold_reminder_minutes")
            validated["hold_reminder_minutes"] = setting_value

        if "hold_escalation_minutes" in request_payload:
            setting_value = request_payload["hold_escalation_minutes"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or setting_value <= 0:
                raise InvalidInputError(messages.text("api.hold_escalation_minutes_positive"), field="hold_escalation_minutes")
            validated["hold_escalation_minutes"] = setting_value

        if "hold_expiry_hours" in request_payload:
            setting_value = request_payload["hold_expiry_hours"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or setting_value <= 0:
                raise InvalidInputError(messages.text("api.hold_expiry_hours_positive"), field="hold_expiry_hours")
            validated["hold_expiry_hours"] = setting_value

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
        if "rich_reports_enabled" in validated:
            ctx.deps.settings_store.set_rich_reports_enabled(validated["rich_reports_enabled"])
        if "hold_reminder_minutes" in validated:
            ctx.deps.settings_store.set_hold_reminder_minutes(validated["hold_reminder_minutes"])
        if "hold_escalation_minutes" in validated:
            ctx.deps.settings_store.set_hold_escalation_minutes(validated["hold_escalation_minutes"])
        if "hold_expiry_hours" in validated:
            ctx.deps.settings_store.set_hold_expiry_hours(validated["hold_expiry_hours"])

        return jsonify({
            "retry_count": ctx.deps.settings_store.get_retry_count(),
            "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
            "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
            "safe_mode": ctx.deps.settings_store.get_safe_mode(),
            "rich_reports_enabled": ctx.deps.settings_store.get_rich_reports_enabled(),
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
            if u["permission_level"] == "commander" and u["telegram_identity"] != BOT_SERVICE_IDENTITY
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

    event_trace_id = event.get("trace_id") if event is not None else None

    if event["outcome"] is not None:
        response_payload = {"event_id": event_id, "status": event["outcome"], "trace_id": event_trace_id}
        if event.get("insight_text") is not None:
            response_payload["insight_text"] = event["insight_text"]
        if event.get("report_text"):
            response_payload["report_text"] = event["report_text"]

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
            "steps_completed": _steps_completed(event),
            "trace_id": event_trace_id,
        }

    processing = ctx.queue.currently_processing()
    if processing is not None and processing[0] == event_id:
        return {"event_id": event_id, "status": "running", "trace_id": event_trace_id}

    return {"event_id": event_id, "status": "queued", "trace_id": event_trace_id}

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

        try:
            wait_seconds = int(request.args.get("wait_seconds", "0"))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds") from exc
        if not 0 <= wait_seconds <= 30:
            raise InvalidInputError(messages.text("api.wait_invalid"), field="wait_seconds")

        waiter = getattr(ctx.deps.persistence, "wait_for_notifications_since", None)
        if wait_seconds and waiter is not None and status.get("status") in {"queued", "running"}:
            deadline = time.monotonic() + wait_seconds
            existing = ctx.deps.persistence.fetch_notifications_since(0)
            since = existing[-1]["sequence_id"] if existing else 0
            while status.get("status") in {"queued", "running"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                waiter(since, remaining)
                status = job_status(ctx, event_id)
                if status is None:
                    raise NotFoundError(messages.text("api.job_not_found", task_id=event_id))
                rows = ctx.deps.persistence.fetch_notifications_since(since)
                if rows:
                    since = rows[-1]["sequence_id"]

        return jsonify(status)

    return blueprint

def build_simulations_blueprint(ctx: "ApiContext") -> Blueprint:
    """Discovery and materialization for a profile's declared simulations
    (docs/profile_simulations_design.md) — the server-side JSON adapter the admin
    panel's simulator page queries instead of maintaining its own knowledge of
    simulation user/group IDs. `GET /Simulations/<key>` returns the exact
    existing admin-simulator scenario JSON shape, ready to feed straight into
    the same `loadScenario()` a manually pasted/uploaded scenario already uses —
    execution from there goes through `POST /Msg`/`POST /Event` exactly as today,
    unchanged."""

    blueprint = Blueprint("simulations", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/Simulations", methods=["GET"])
    def list_simulations():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_SIMULATIONS)
        return jsonify({"simulations": simulation_catalog_payload(ctx.loaded_profile)})

    @blueprint.route("/Simulations/<key>", methods=["GET"])
    def get_simulation(key):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_SIMULATIONS)
        scenario = find_simulation_scenario(ctx.loaded_profile, key)
        if scenario is None:
            raise NotFoundError(messages.text("api.simulation_not_found", simulation_key=key))
        return jsonify(
            materialize_simulation(scenario, ctx.loaded_profile.simulation_users, ctx.loaded_profile.simulation_groups)
        )

    return blueprint

    return blueprint

from api.routes_messages import (
    KNOWN_BUTTON_PROTOCOLS,
    SITUATIONAL_PICTURE_PROTOCOL,
    build_messages_blueprint,
)
from api.routes_holds import build_holds_blueprint
from api.routes_notifications import build_notifications_blueprint
