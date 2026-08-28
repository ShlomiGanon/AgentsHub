"""Consolidated responsibility module for routes."""

from datetime import datetime, timezone

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import ConflictError, InvalidInputError, NotFoundError, RunFailureError, authenticate, require
from history import storage_timestamp

from orchestrator.flows import begin_report, run_report_extraction

from tools import get_trace_id, new_trace_id, set_trace_id, trace_context

import logging

from auth.permissions import PermissionLevel

from orchestrator.flows import (
    OrchestrationParseError,
    answer_conversationally,
    answer_question,
    begin_report,
    begin_request,
    classify_intent,
    continue_from_risk_assessment,
    run_report_extraction,
)

from protocols import CriticalityLevel, Protocol, ProtocolEditError, add_protocol, remove_protocol, replace_protocol

from profiles.loader import hash_profile_file

from orchestrator.flows import continue_after_approval, continue_after_clarification, decline, resolve_approval, resolve_clarification

if TYPE_CHECKING:
    from api.app import ApiContext


def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))


def build_events_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("events", __name__)

    @blueprint.route("/Event", methods=["POST"])
    def post_event():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "send_message")

        request_payload = request.get_json(silent=True) or {}
        text = request_payload.get("text")
        sender_identity = request_payload.get("sender_identity")

        if not text:
            raise InvalidInputError("'text' is required", field="text")
        if not sender_identity:
            raise InvalidInputError("'sender_identity' is required", field="sender_identity")

        trace_id = new_trace_id()
        set_trace_id(trace_id)
        event_id = begin_report(ctx.deps, text, "sensor", _now(), sender_identity)

        def _work() -> None:
            with trace_context(trace_id):
                run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit((event_id, _work))

        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint


if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)


def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))


def build_messages_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("messages", __name__)

    @blueprint.route("/Msg", methods=["POST"])
    def post_msg():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "send_message")

        request_payload = request.get_json(silent=True) or {}
        text = request_payload.get("text")
        sender_identity = request_payload.get("sender_identity")
        source_message_id = request_payload.get("source_message_id")

        if not text:
            raise InvalidInputError("'text' is required", field="text")
        if not sender_identity:
            raise InvalidInputError("'sender_identity' is required", field="sender_identity")

        trace_id = new_trace_id()
        set_trace_id(trace_id)

        try:
            intent = classify_intent(ctx.main_agent, ctx.deps.protocol_set.all(), text)
        except OrchestrationParseError as exc:
            raise RunFailureError(str(exc)) from exc

        logger.info(
            "intent classified",
            extra={"event": "intent_classified", "intent": intent.intent, "reason": intent.reason, "trace_id": get_trace_id()},
        )

        received_at = _now()

        if intent.intent == "needs_clarification":
            return jsonify({
                "taken_as": "clarification",
                "answer": intent.clarification_question or "Could you clarify what you want me to do?",
            })

        if intent.intent == "conversational":
            try:
                reply = answer_conversationally(ctx.main_agent, text)
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            return jsonify({"taken_as": "conversational", "answer": reply})

        if intent.intent == "question":
            require(level, "view_history")
            try:
                answer = answer_question(ctx.main_agent, text, ctx.deps.registry, ctx.deps.history_query_service)
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            return jsonify({"taken_as": "question", "answer": answer})

        if intent.intent == "report":
            event_id = begin_report(ctx.deps, text, "telegram", received_at, sender_identity, source_message_id)

            def _work() -> None:
                with trace_context(trace_id):
                    run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

            ctx.queue.submit((event_id, _work))
            return jsonify({"taken_as": "report", "event_id": event_id, "status": "queued"}), 202

        if intent.intent != "request":
            raise RunFailureError(f"unsupported message intent: {intent.intent!r}")

        is_commander = level >= PermissionLevel.COMMANDER
        event_id = begin_request(ctx.deps, text, received_at, sender_identity, source_message_id)

        def _work() -> None:
            with trace_context(trace_id):
                continue_from_risk_assessment(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent, is_commander)

        ctx.queue.submit((event_id, _work))
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
        raise InvalidInputError(f"missing required field: {exc.args[0]}", field=str(exc.args[0])) from exc
    except (TypeError, AttributeError) as exc:
        raise InvalidInputError(f"malformed protocol body: {exc}") from exc


def build_protocols_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("protocols", __name__)

    def _agents_by_name() -> dict:
        return {agent.name: agent for agent in ctx.deps.registry.all()}

    @blueprint.route("/Protocol", methods=["GET"])
    def list_protocols():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        return jsonify({"protocols": [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()]})

    @blueprint.route("/Protocol", methods=["POST"])
    def create_protocol():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "edit_profile")

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
        require(level, "edit_profile")

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
        require(level, "edit_profile")

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

    @blueprint.route("/SYSTEM", methods=["GET"])
    def get_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        loaded = ctx.loaded_profile
        current_hash = hash_profile_file(loaded.module_path)

        return jsonify({
            "profile": loaded.module_path,
            "agents": [agent.name for agent in ctx.deps.registry.all()],
            "protocols": [protocol_to_dict(p) for p in ctx.deps.protocol_set.all()],
            "event_types": list(ctx.deps.event_type_registry.types),
            "areas": list(ctx.deps.area_registry.areas),
            "queued_events": ctx.queue.qsize(),
            "held_events": {
                "clarification": len(ctx.deps.persistence.list_held_events("clarification")),
                "approval": len(ctx.deps.persistence.list_held_events("approval")),
            },
            "scheduler": ctx.scheduler.last_run_status(),
            "settings": {
                "retry_count": ctx.deps.settings_store.get_retry_count(),
                "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
                "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
            },
            "profile_file_changed": current_hash != loaded.profile_file_hash,
        })

    @blueprint.route("/SYSTEM", methods=["PUT"])
    def put_system():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "change_settings")

        request_payload = request.get_json(silent=True) or {}

        unknown = sorted(set(request_payload) - _SETTINGS_FIELDS)
        if unknown:
            field = unknown[0]
            raise InvalidInputError(f"'{field}' belongs to the profile and takes effect only on a restart", field=field)

        if "retry_count" in request_payload:
            setting_value = request_payload["retry_count"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 0:
                raise InvalidInputError("'retry_count' must be a non-negative integer", field="retry_count")
            ctx.deps.settings_store.set_retry_count(setting_value)

        if "risk_threshold" in request_payload:
            setting_value = request_payload["risk_threshold"]
            if not isinstance(setting_value, (int, float)) or isinstance(setting_value, bool) or not (0.0 <= setting_value <= 1.0):
                raise InvalidInputError("'risk_threshold' must be a number between 0.0 and 1.0", field="risk_threshold")
            ctx.deps.settings_store.set_risk_threshold(setting_value)

        if "lookback_window_days" in request_payload:
            setting_value = request_payload["lookback_window_days"]
            if not isinstance(setting_value, int) or isinstance(setting_value, bool) or setting_value < 1:
                raise InvalidInputError("'lookback_window_days' must be a positive integer", field="lookback_window_days")
            ctx.deps.settings_store.set_lookback_window_days(setting_value)

        return jsonify({
            "retry_count": ctx.deps.settings_store.get_retry_count(),
            "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
            "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
        })

    return blueprint


if TYPE_CHECKING:
    from api.app import ApiContext


def build_users_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("users", __name__)

    @blueprint.route("/User/<identity>", methods=["GET"])
    def get_user(identity):
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_history")

        user = ctx.deps.persistence.read_user(identity)
        if user is None:
            return jsonify({"registered": False, "permission_level": None})

        return jsonify({"registered": True, "permission_level": user["permission_level"]})

    @blueprint.route("/Commanders", methods=["GET"])
    def get_commanders():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "view_commander_roster")

        commanders = [u for u in ctx.deps.persistence.list_users() if u["permission_level"] == "commander"]
        return jsonify({"commanders": [{"telegram_identity": u["telegram_identity"]} for u in commanders]})

    return blueprint

if TYPE_CHECKING:
    from api.app import ApiContext


def _steps_completed(event: dict) -> list[str]:
    """Every step that actually produced a result, in order — derivable entirely from `event["steps"]` (§2.3's `event_steps` table, already attached by `fetch_event`): a step that fail..."""

    return [f"{step['agent_name']}: {step['result_text']}" for step in event.get("steps", []) if step.get("result_text") is not None]


def _failed_step_agent_name(event: dict) -> str | None:
    """The agent whose step has no result — execution stops at the first failing step (`protocols.executor.execute_steps`), so at most one persisted step ever has `result_text=None`, a..."""

    for step in event.get("steps", []):
        if step.get("result_text") is None:
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

        request_payload = request.get_json(silent=True) or {}
        classification = request_payload.get("classification")
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

        request_payload = request.get_json(silent=True) or {}
        decision = request_payload.get("decision")
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
    """`job_finished`/`job_failed` are addressed to whoever submitted the original event (§8.9: "deliver to whoever submitted it") — its `sender_identity` doubles as the chat to reach..."""

    if kind not in ("job_finished", "job_failed"):
        return []

    event = ctx.deps.persistence.fetch_event(event_id)
    return [event["sender_identity"]]


def _reply_to_message_id(ctx: "ApiContext", kind: str, event_id: str) -> str | None:
    """The originating Telegram message's own ID (work_plan.md §2.3's `source_message_id` column), so `job_finished`/`job_failed` — the two kinds ever delivered via `TelegramClient.sen..."""

    if kind not in ("job_finished", "job_failed"):
        return None

    event = ctx.deps.persistence.fetch_event(event_id)
    return event.get("source_message_id")


def _format_notification(ctx: "ApiContext", notification_row: dict) -> dict:
    builder = _PAYLOAD_BUILDERS[notification_row["kind"]]
    return {
        "sequence_id": notification_row["sequence_id"],
        "kind": notification_row["kind"],
        "payload": builder(ctx, notification_row["event_id"]),
        "target_chat_ids": _target_chat_ids(ctx, notification_row["kind"], notification_row["event_id"]),
        "reply_to_message_id": _reply_to_message_id(ctx, notification_row["kind"], notification_row["event_id"]),
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

        notification_rows = ctx.deps.persistence.fetch_notifications_since(since)
        notifications = [_format_notification(ctx, notification_row) for notification_row in notification_rows]
        next_cursor = notification_rows[-1]["sequence_id"] if notification_rows else since

        return jsonify({"notifications": notifications, "next_cursor": next_cursor})

    return blueprint
