"""User lookup, self-name update, approval, and commander roster."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.request_boundary import BOT_SERVICE_IDENTITY, AuthorizationError, InvalidInputError, NotFoundError, authenticate, require
from auth.permissions import InvalidFullNameError, PermissionLevel, RequestedOperation, normalize_full_name
from persistence import NotFoundError as PersistenceNotFoundError
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

def build_users_blueprint(ctx: "ApiContext") -> Blueprint:
    """JSON routes for registered Telegram users and the commander roster."""

    blueprint = Blueprint("users", __name__)
    messages = ctx.loaded_profile.message_catalog

    @blueprint.route("/User/<identity>", methods=["GET"])
    def get_user(identity):
        """Return one registered user's public registration fields."""

        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.VIEW_USER_REGISTRATION)

        # A viewer may look up only their own identity; a commander (including
        # bot-service resolving callers) is unrestricted.
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
        """Let the authenticated caller update their own display name."""

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
        """Approve an auto-registered Telegram user so they may use the system."""

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
        """List commander identities, excluding the bot-service account."""

        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, RequestedOperation.VIEW_COMMANDER_ROSTER)

        commanders = [
            u for u in ctx.deps.persistence.list_users()
            if u["permission_level"] == "commander" and u["telegram_identity"] != BOT_SERVICE_IDENTITY
        ]
        return jsonify({"commanders": [{"telegram_identity": u["telegram_identity"]} for u in commanders]})

    return blueprint
