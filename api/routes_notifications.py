"""Notification polling routes and payload builders."""

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

from api.routes import _failed_step_agent_name, _steps_completed

def _clarification_hold_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("clarification", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "raw_text": hold["raw_text"],
        "unresolved_field": hold["unresolved_field"],
        "available_classifications": list(ctx.deps.event_type_registry.types),
    }

def _approval_hold_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
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

def _event_data_hold_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    hold = ctx.deps.persistence.fetch_held_event("event_data", event_id)
    return {
        "hold_id": hold["hold_id"],
        "event_id": event_id,
        "question": hold["question"],
        "missing_fields": hold.get("missing_fields", []),
    }

def _uncertain_verdict_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    return {"event_id": event_id, "insight_text": event.get("insight_text") or ""}

def _uncertain_verdict_reporter_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    # Deliberately carries no insight text — item #8's decision is a short,
    # generic notice for the original reporter, not the commander-level detail.
    return {"event_id": event_id}

def _resource_unavailable_alert_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    # Commander-only detail (fact + concrete alternatives), persisted on its own column by
    # orchestrator/flows.py::_finish_with_resource_unavailable -- never insight_text or
    # report_text, so it can never reach the reporter's own job_finished notification.
    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    return {"event_id": event_id, "alert_text": event.get("commander_alert_text") or ""}

def _hold_escalation_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    # Item 8: an unresolved hold escalated to commanders after get_hold_escalation_minutes with
    # no answer -- the alert text is composed once, at escalation time (orchestrator.flows'
    # _escalate_unresolved_hold), and persisted the same way commander_alert_text already is.
    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    return {"event_id": event_id, "alert_text": event.get("hold_escalation_alert_text") or ""}

def _precedent_closure_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    matched_id = event["precedent_closed_by_event_id"]
    matched_event = ctx.deps.persistence.fetch_event(matched_id)
    return {
        "event_id": event_id,
        "raw_text": event["raw_text"],
        "matched_precedent_event_id": matched_id,
        "precedent_ending": matched_event["outcome"] if matched_event is not None else "unknown",
    }

def _no_match_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    return {
        "event_id": event_id,
        "raw_text": event["raw_text"],
        "reason": event.get("outcome_failure_reason") or "",
        "risk_level": event.get("risk_level") or "",
        "risk_reason": event.get("risk_reason") or "",
    }

def _job_payload(ctx: "ApiContext", event_id: str, event: dict | None = None) -> dict:
    if event is None:
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
        # Composed once, when the run finished (orchestrator.flows._record_outcome_with_report)
        # — a pure read here, never a model call. Absent (not just empty) when rich reporting
        # was disabled for this run, so the bot falls back to its own fixed-template rendering.
        **({"report_text": event["report_text"]} if event.get("report_text") else {}),
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
    "resource_unavailable_alert": _resource_unavailable_alert_payload,
    "hold_escalation": _hold_escalation_payload,
}

def _target_chat_ids(ctx: "ApiContext", kind: str, event_id: str, event: dict | None = None) -> list[str]:
    """Reporter-facing job, hold and event-data notifications target the original submitter's
    chat. For `job_finished`/`job_failed` specifically, that's the chat the report actually came
    from — group or private — stored on the event at submission time (`telegram_chat_id`); the
    other kinds are unchanged (holds keep their current sender-private-chat behavior for now)."""

    if kind not in ("job_finished", "job_failed", "event_data_hold", "uncertain_verdict_reporter", "approval_hold", "clarification_hold"):
        return []

    if event is None:
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
    if sender == BOT_SERVICE_IDENTITY:
        return []
    if kind in ("job_finished", "job_failed"):
        # Falls back to the sender's own identity for events that predate this column, or
        # weren't submitted with a known chat (e.g. a non-Telegram "sensor" source). Never
        # widened to include commanders — a `handled_resource_unavailable` outcome's commander
        # alert is an entirely separate notification kind (`resource_unavailable_alert`,
        # below), delivered only to each commander's own private chat, precisely so the
        # reporter's own chat (this one, possibly a group) never receives it.
        return [event.get("telegram_chat_id") or sender]
    return [sender]

def _reply_to_message_id(ctx: "ApiContext", kind: str, event_id: str, event: dict | None = None) -> str | None:
    """The reply target for a *new* message — the fallback path when there's no ack message to
    edit in place, or editing it failed. Attaches to the originating Telegram message."""

    if kind not in ("job_finished", "job_failed", "event_data_hold", "uncertain_verdict_reporter"):
        return None

    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    return event.get("source_message_id")

def _ack_message_id(ctx: "ApiContext", kind: str, event_id: str, event: dict | None = None) -> str | None:
    """The status/ack message to edit in place with the final result — job_finished/job_failed
    only; None for every other kind, and None when the event has no stored ack (predates this
    column, or wasn't submitted through the normal ack lifecycle)."""

    if kind not in ("job_finished", "job_failed"):
        return None

    if event is None:
        event = ctx.deps.persistence.fetch_event(event_id)
    return event.get("ack_message_id") if event is not None else None

def _format_notification(ctx: "ApiContext", notification_row: dict) -> dict:
    builder = _PAYLOAD_BUILDERS[notification_row["kind"]]
    event_id = notification_row["event_id"]
    event = ctx.deps.persistence.fetch_event(event_id)
    return {
        "sequence_id": notification_row["sequence_id"],
        "kind": notification_row["kind"],
        "payload": builder(ctx, event_id, event),
        "target_chat_ids": _target_chat_ids(ctx, notification_row["kind"], event_id, event),
        "reply_to_message_id": _reply_to_message_id(ctx, notification_row["kind"], event_id, event),
        "ack_message_id": _ack_message_id(ctx, notification_row["kind"], event_id, event),
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
