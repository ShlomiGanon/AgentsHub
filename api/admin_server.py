"""Admin server page: profile switch and database reset."""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, request, session, url_for

from api.admin_chrome import _SERVER_TEMPLATE
from api.admin_support import AdminPanel, catalog_text
from config import discover_profiles, read_server_status, submit_server_command, supervisor_available
from tools import get_trace_id

logger = logging.getLogger(__name__)


def register_server_routes(blueprint: Blueprint, panel: AdminPanel) -> None:
    """Mount server status and control routes on the admin blueprint."""

    ctx = panel.ctx

    @blueprint.route("/server", methods=["GET"])
    def server():
        """Show the active profile, supervisor status, and safe-mode counts."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        return panel.render(
            _SERVER_TEMPLATE,
            active_module=ctx.loaded_profile.module_path,
            active_profile=ctx.loaded_profile.profile_name,
            profiles=discover_profiles(),
            supervisor=supervisor_available(),
            status=read_server_status(),
            safe_mode=ctx.deps.settings_store.get_safe_mode(),
            automatic_users=sum(
                bool(user.get("auto_register", False))
                for user in ctx.deps.persistence.list_users()
            ),
            automatic_groups=sum(
                bool(group.auto_register) for group in ctx.group_routing.all()
            ),
            csrf_token=session["csrf_token"],
            **panel.api_page_context("server"),
        )

    @blueprint.route("/server/profile", methods=["POST"])
    def switch_profile():
        """Ask the supervisor to restart onto another profile module."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response
        module_path = request.form.get("profile_module", "").strip()
        selected = next((profile for profile in discover_profiles() if profile.module_path == module_path), None)
        if selected is None:
            flash(catalog_text("admin.server_profile_invalid"), "error")
            return redirect(url_for("admin.server"))
        try:
            submit_server_command("switch_profile", profile_module=module_path)
        except RuntimeError:
            flash(catalog_text("admin.server_unavailable"), "error")
            return redirect(url_for("admin.server"))
        logger.info("admin requested profile switch", extra={"event": "admin_profile_switch", "profile_module": module_path, "trace_id": get_trace_id()})
        return panel.restart_page(selected.api_port)

    @blueprint.route("/server/reset", methods=["POST"])
    def reset_server():
        """Ask the supervisor to wipe this profile's database and restart."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response
        if request.form.get("confirm") != "yes":
            flash(catalog_text("admin.server_reset_confirmation_missing"), "error")
            return redirect(url_for("admin.server"))
        try:
            submit_server_command("reset")
        except RuntimeError:
            flash(catalog_text("admin.server_unavailable"), "error")
            return redirect(url_for("admin.server"))
        logger.warning("admin requested database reset", extra={"event": "admin_database_reset", "profile_module": ctx.loaded_profile.module_path, "trace_id": get_trace_id()})
        return panel.restart_page(ctx.loaded_profile.api_port)
