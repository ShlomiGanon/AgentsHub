"""Helpers for `POST /Msg` validation, scoping, and queued-answer text."""

import dataclasses
from typing import TYPE_CHECKING

from api.request_boundary import AuthorizationError, InvalidInputError
from orchestrator.flows import GroupNotRegisteredError, is_scoped_target, resolve_scope, scope_deps
from tools import get_trace_id

if TYPE_CHECKING:
    from api.app import ApiContext

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


def queued_answer_text(messages, kind: str, task_id: str) -> str:
    """Return the queued report/request answer; include the task id only in deep debug."""
    from api import routes_messages
    if routes_messages.deep_debug_enabled():
        return messages.text(f"api.queued_{kind}_debug", task_id=task_id)
    return messages.text(f"api.queued_{kind}")


def apply_group_scope(app_ctx: "ApiContext", telegram_chat_id, telegram_chat_type, messages):
    """Return the request context scoped to a bound group agent, or the app context."""
    try:
        scoped_agent = resolve_scope(
            app_ctx.group_routing,
            str(telegram_chat_id) if telegram_chat_id is not None else None,
            str(telegram_chat_type) if telegram_chat_type is not None else None,
        )
    except GroupNotRegisteredError as exc:
        raise AuthorizationError(messages.text("api.group_not_registered", chat_id=exc.chat_id)) from exc
    ctx = app_ctx
    if is_scoped_target(scoped_agent):
        ctx = dataclasses.replace(app_ctx, deps=scope_deps(app_ctx.deps, scoped_agent))
    return ctx, scoped_agent


def validate_message_fields(request_payload: dict, caller_identity: str, messages) -> tuple[str, str]:
    """Return text and sender_identity, or raise if the body is incomplete or mismatched."""
    text = request_payload.get("text")
    sender_identity = request_payload.get("sender_identity")
    conversation_id = request_payload.get("conversation_id")
    event_data_event_id = request_payload.get("event_data_event_id")
    if not text:
        raise InvalidInputError(messages.text("api.field_required", field="text"), field="text")
    if not sender_identity:
        raise InvalidInputError(
            messages.text("api.field_required", field="sender_identity"), field="sender_identity"
        )
    if sender_identity != caller_identity:
        raise AuthorizationError(messages.text("api.sender_identity_mismatch"))
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or not conversation_id.strip() or len(conversation_id) > 200
    ):
        raise InvalidInputError(messages.text("api.conversation_id_invalid"), field="conversation_id")
    if event_data_event_id is not None and (
        not isinstance(event_data_event_id, str) or not event_data_event_id.strip()
    ):
        raise InvalidInputError(messages.text("api.event_data_event_id_invalid"), field="event_data_event_id")
    return str(text), str(sender_identity)
