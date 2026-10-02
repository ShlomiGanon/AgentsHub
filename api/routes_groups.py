"""Telegram group bindings and the attendance-check trigger the bot polls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import InvalidInputError, NotFoundError, authenticate, require
from auth.permissions import RequestedOperation
from orchestrator.flows import InvalidRoutingTargetError, attendance_dispatch
from persistence import NotFoundError as PersistenceNotFoundError
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

def _binding_to_dict(binding) -> dict:
    """Public JSON shape for one telegram-group binding."""

    return {
        "chat_id": binding.chat_id,
        "agent_name": binding.agent_name,
        "label": binding.label,
        "auto_register": bool(getattr(binding, "auto_register", False)),
        "attendance_check_enabled": bool(getattr(binding, "attendance_check_enabled", False)),
        "attendance_check_hour": int(binding.attendance_check_hour),
    }

def _optional_attendance_enabled(value):
    """Parse an optional boolean attendance-enabled field, or None if omitted."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise InvalidInputError("attendance_check_enabled must be a boolean", field="attendance_check_enabled")

def _optional_attendance_hour(value):
    """Parse an optional hour 0-23, or None if omitted."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError("attendance_check_hour must be an integer hour", field="attendance_check_hour")
    if value < 0 or value > 23:
        raise InvalidInputError("attendance_check_hour must be between 0 and 23", field="attendance_check_hour")
    return value

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
        attendance_check_enabled = _optional_attendance_enabled(request_payload.get("attendance_check_enabled"))
        attendance_check_hour = _optional_attendance_hour(request_payload.get("attendance_check_hour"))

        try:
            binding = ctx.group_routing.upsert(
                chat_id,
                agent_name,
                label,
                attendance_check_enabled=attendance_check_enabled,
                attendance_check_hour=attendance_check_hour,
            )
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

        dispatch = attendance_dispatch(
            ctx.group_routing.all(),
            agent.name,
            safe_mode=ctx.deps.settings_store.get_safe_mode(),
        )
        try:
            opened = agent.open_scheduled_cycle(now_iso, force=force, check_hour=dispatch.check_hour)
        except ValueError as exc:
            raise InvalidInputError(str(exc), field="now_iso") from exc

        target_chat_ids = list(dispatch.target_chat_ids)
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
