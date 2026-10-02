"""Admin user roster: write, remove, approve, and bot-service provision."""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, request, session, url_for

from api.admin_chrome import _USERS_TEMPLATE
from api.admin_support import AdminPanel, catalog_text
from api.request_boundary import BOT_SERVICE_IDENTITY
from auth.permissions import InvalidFullNameError, normalize_full_name
from persistence import NotFoundError
from tools import get_trace_id, record_telegram_security_metric

logger = logging.getLogger(__name__)


def register_user_routes(blueprint: Blueprint, panel: AdminPanel) -> None:
    """Mount user list and write routes on the admin blueprint."""

    ctx = panel.ctx

    @blueprint.route("/users", methods=["GET"])
    def users():
        """List registered Telegram users."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        registered_users = sorted(
            ctx.deps.persistence.list_users(), key=lambda user: user["telegram_identity"]
        )
        return panel.render(
            _USERS_TEMPLATE,
            users=registered_users,
            levels=panel.levels,
            safe_mode=ctx.deps.settings_store.get_safe_mode(),
            csrf_token=session["csrf_token"],
            bot_service_identity=BOT_SERVICE_IDENTITY,
        )

    @blueprint.route("/users", methods=["POST"])
    def write_user():
        """Create or update a registered Telegram user."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        identity = request.form.get("telegram_identity", "").strip()
        level = request.form.get("permission_level", "")
        raw_full_name = request.form.get("full_name")
        if not identity:
            flash(catalog_text("admin.identity_required"), "error")
            return redirect(url_for("admin.users"))
        if level not in panel.levels:
            flash(catalog_text("admin.level_invalid", level=level), "error")
            return redirect(url_for("admin.users"))

        existed = ctx.deps.persistence.read_user(identity) is not None
        try:
            full_name = (
                None
                if raw_full_name is None and existed
                else normalize_full_name(raw_full_name or "", allow_empty=True)
            )
        except InvalidFullNameError:
            flash(catalog_text("admin.full_name_invalid"), "error")
            return redirect(url_for("admin.users"))

        ctx.deps.persistence.write_user(identity, level, full_name)
        logger.info(
            "admin wrote a user",
            extra={
                "event": "admin_user_updated" if existed else "admin_user_added",
                "telegram_identity": identity, "permission_level": level, "trace_id": get_trace_id(),
            },
        )
        flash(catalog_text("admin.user_written", identity=identity, level=level), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.route("/users/<identity>/remove", methods=["POST"])
    def remove_user(identity):
        """Delete a registered Telegram user."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        try:
            ctx.deps.persistence.delete_user(identity)
        except NotFoundError:
            flash(catalog_text("admin.user_not_found", identity=identity), "error")
            return redirect(url_for("admin.users"))

        logger.info(
            "admin removed a user",
            extra={"event": "admin_user_removed", "telegram_identity": identity, "trace_id": get_trace_id()},
        )
        flash(catalog_text("admin.user_removed", identity=identity), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.route("/users/<identity>/approve", methods=["POST"])
    def approve_user(identity):
        """Clear auto-register on a Telegram user so they may use the system in safe mode."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            ctx.deps.persistence.approve_user(identity)
        except NotFoundError:
            flash(catalog_text("admin.user_not_found", identity=identity), "error")
            return redirect(url_for("admin.users"))
        logger.info(
            "admin approved a telegram user",
            extra={"event": "telegram_user_approved", "telegram_identity": identity, "approved_by": "admin-session", "trace_id": get_trace_id()},
        )
        record_telegram_security_metric("approved", "user")
        flash(catalog_text("admin.user_approved", identity=identity), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.route("/bot-service/provision", methods=["POST"])
    def provision_bot_service():
        """Ensure the bot-service commander identity exists."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        from api.app import ensure_bot_service

        ensure_bot_service(ctx.deps.persistence)
        logger.info(
            "admin (re-)provisioned the bot-service identity",
            extra={"event": "admin_bot_service_provisioned", "trace_id": get_trace_id()},
        )
        flash(catalog_text("admin.bot_service_provisioned", identity=BOT_SERVICE_IDENTITY), "ok")
        return redirect(url_for("admin.users"))
