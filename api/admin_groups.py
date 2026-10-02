"""Admin Telegram group bindings: write, remove, approve, and rename."""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, request, session, url_for

from api.admin_chrome import _GROUPS_TEMPLATE
from api.admin_support import AdminPanel, catalog_text
from orchestrator.flows import InvalidRoutingTargetError
from persistence import NotFoundError, PersistenceError
from tools import get_trace_id, record_telegram_security_metric

logger = logging.getLogger(__name__)


def register_group_routes(blueprint: Blueprint, panel: AdminPanel) -> None:
    """Mount group list and write routes on the admin blueprint."""

    ctx = panel.ctx

    @blueprint.route("/groups", methods=["GET"])
    def groups():
        """List Telegram group-to-agent bindings."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        return panel.render(
            _GROUPS_TEMPLATE,
            groups=ctx.group_routing.all(),
            routable_agents=ctx.group_routing.routable_targets,
            safe_mode=ctx.deps.settings_store.get_safe_mode(),
            csrf_token=session["csrf_token"],
        )

    @blueprint.route("/groups", methods=["POST"])
    def write_group():
        """Create or update a Telegram group binding, including attendance flags."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        chat_id = request.form.get("chat_id", "").strip()
        agent_name = request.form.get("agent_name", "").strip()
        label = request.form.get("label", "").strip()
        enabled_values = request.form.getlist("attendance_check_enabled")
        attendance_check_enabled = enabled_values[-1] == "1" if enabled_values else None
        hour_raw = request.form.get("attendance_check_hour", "").strip()
        attendance_check_hour = None
        if hour_raw:
            try:
                attendance_check_hour = int(hour_raw)
            except ValueError:
                flash(catalog_text("admin.attendance_hour_invalid"), "error")
                return redirect(url_for("admin.groups"))
            if attendance_check_hour < 0 or attendance_check_hour > 23:
                flash(catalog_text("admin.attendance_hour_invalid"), "error")
                return redirect(url_for("admin.groups"))
        if not chat_id:
            flash(catalog_text("admin.chat_id_required"), "error")
            return redirect(url_for("admin.groups"))

        existed = ctx.group_routing.get(chat_id) is not None
        try:
            ctx.group_routing.upsert(
                chat_id,
                agent_name,
                label,
                attendance_check_enabled=attendance_check_enabled,
                attendance_check_hour=attendance_check_hour,
            )
        except InvalidRoutingTargetError:
            flash(catalog_text("admin.group_agent_invalid", agent=agent_name), "error")
            return redirect(url_for("admin.groups"))
        logger.info(
            "admin wrote a telegram group binding",
            extra={
                "event": "admin_group_updated" if existed else "admin_group_added",
                "chat_id": chat_id, "agent_name": agent_name, "trace_id": get_trace_id(),
            },
        )
        flash(catalog_text("admin.group_routed", chat_id=chat_id, agent=agent_name), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/groups/<chat_id>/remove", methods=["POST"])
    def remove_group(chat_id):
        """Delete a Telegram group binding."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        try:
            ctx.group_routing.remove(chat_id)
        except NotFoundError:
            flash(catalog_text("admin.group_not_found", chat_id=chat_id), "error")
            return redirect(url_for("admin.groups"))

        logger.info(
            "admin removed a telegram group binding",
            extra={"event": "admin_group_removed", "chat_id": chat_id, "trace_id": get_trace_id()},
        )
        flash(catalog_text("admin.group_removed", chat_id=chat_id), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/groups/<chat_id>/approve", methods=["POST"])
    def approve_group(chat_id):
        """Clear auto-register on a Telegram group so it may be used in safe mode."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            ctx.group_routing.approve(chat_id)
        except NotFoundError:
            flash(catalog_text("admin.group_not_found", chat_id=chat_id), "error")
            return redirect(url_for("admin.groups"))
        logger.info(
            "admin approved a telegram group",
            extra={"event": "telegram_group_approved", "chat_id": chat_id, "approved_by": "admin-session", "trace_id": get_trace_id()},
        )
        record_telegram_security_metric("approved", "group")
        flash(catalog_text("admin.group_approved", chat_id=chat_id), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/groups/<chat_id>/rename", methods=["POST"])
    def rename_group(chat_id):
        """Change a group's chat_id in place."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        new_chat_id = request.form.get("new_chat_id", "").strip()
        if not new_chat_id:
            flash(catalog_text("admin.new_chat_id_required"), "error")
            return redirect(url_for("admin.groups"))
        if not new_chat_id.lstrip("-").isdigit() or not new_chat_id.startswith("-") or int(new_chat_id) >= 0:
            flash(catalog_text("admin.new_chat_id_invalid"), "error")
            return redirect(url_for("admin.groups"))

        try:
            ctx.group_routing.rename(chat_id, new_chat_id)
        except NotFoundError:
            flash(catalog_text("admin.group_not_found", chat_id=chat_id), "error")
            return redirect(url_for("admin.groups"))
        except PersistenceError:
            flash(catalog_text("admin.group_chat_id_taken", chat_id=new_chat_id), "error")
            return redirect(url_for("admin.groups"))

        logger.info(
            "admin renamed a telegram group's chat ID",
            extra={
                "event": "admin_group_renamed",
                "old_chat_id": chat_id, "new_chat_id": new_chat_id, "trace_id": get_trace_id(),
            },
        )
        flash(catalog_text("admin.group_renamed", old_chat_id=chat_id, new_chat_id=new_chat_id), "ok")
        return redirect(url_for("admin.groups"))
