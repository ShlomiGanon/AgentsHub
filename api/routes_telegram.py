"""Bot-service-only admission gate for real Telegram updates."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import BOT_SERVICE_IDENTITY, AuthorizationError, InvalidInputError, authenticate
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

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
