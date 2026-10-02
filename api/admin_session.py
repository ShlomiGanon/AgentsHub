"""Admin login, logout, CSRF-gated session, and acting-identity setup."""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, request, session, url_for

from api.admin_chrome import _ACTING_IDENTITY_TEMPLATE
from api.admin_support import AdminPanel, catalog_text, client_source, issue_session
from api.request_boundary import SYSTEM_ADMIN_IDENTITY, secrets_equal
from messages import get_current_catalog
from tools import get_trace_id

logger = logging.getLogger(__name__)


def register_session_routes(blueprint: Blueprint, panel: AdminPanel) -> None:
    """Mount login, logout, and the one-time acting-identity form."""

    @blueprint.route("/login", methods=["GET", "POST"])
    def login():
        """Authenticate the shared operator login, or show the login page."""

        if request.method == "GET":
            if session.get("admin_authenticated") and not panel.session_expired():
                if panel.session_identity_valid():
                    return redirect(url_for("admin.dashboard"))
                return redirect(url_for("admin.acting_identity"))
            return panel.render_login()

        source = client_source()
        remaining = panel.rate_limiter.remaining_minutes()
        if remaining > 0:
            logger.info(
                "admin login attempt while locked out",
                extra={"event": "admin_login_locked_out", "source_ip": source, "trace_id": get_trace_id()},
            )
            return redirect(url_for("admin.login"))

        submitted_username = request.form.get("username", "")
        submitted_password = request.form.get("password", "")
        username_ok = secrets_equal(submitted_username, panel.config.username)
        password_ok = secrets_equal(submitted_password, panel.config.password)

        if username_ok and password_ok:
            panel.rate_limiter.record_success()
            issue_session(panel.config)
            logger.info(
                "admin login succeeded",
                extra={"event": "admin_login_succeeded", "source_ip": source, "trace_id": get_trace_id()},
            )
            return redirect(url_for("admin.acting_identity"))

        remaining_after_failure = panel.rate_limiter.record_failure()
        logger.warning(
            "admin login failed",
            extra={
                "event": "admin_login_failed", "source_ip": source,
                "attempted_username": submitted_username, "trace_id": get_trace_id(),
            },
        )
        if remaining_after_failure <= 0:
            flash(get_current_catalog().text("admin.login_wrong_credentials"), "error")
        return redirect(url_for("admin.login"))

    @blueprint.route("/logout", methods=["POST"])
    def logout():
        """End the admin session and return to login."""

        redirect_response = panel.require_session(require_identity=False)
        if redirect_response is not None:
            return redirect_response
        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        logger.info("admin logout", extra={"event": "admin_logout", "trace_id": get_trace_id()})
        session.clear()
        flash(catalog_text("admin.signed_out"), "ok")
        return redirect(url_for("admin.login"))

    @blueprint.route("/acting-identity", methods=["GET", "POST"])
    def acting_identity():
        """One-time post-login choice of the Telegram identity this session acts as."""

        redirect_response = panel.require_session(require_identity=False)
        if redirect_response is not None:
            return redirect_response

        users = panel.api_users()
        if request.method == "GET":
            if panel.session_identity_valid():
                return redirect(url_for("admin.dashboard"))
            return panel.render(
                _ACTING_IDENTITY_TEMPLATE,
                csrf_token=session["csrf_token"],
                api_users=users,
            )

        csrf_response = panel.require_csrf()
        if csrf_response is not None:
            return csrf_response

        if request.form.get("use_system_admin"):
            session["api_identity"] = SYSTEM_ADMIN_IDENTITY
            flash(catalog_text("admin.api.identity_selected", identity=SYSTEM_ADMIN_IDENTITY), "ok")
            return redirect(url_for("admin.dashboard"))

        identity = request.form.get("api_identity", "").strip()
        if identity not in {user["telegram_identity"] for user in users}:
            flash(catalog_text("admin.api.identity_invalid"), "error")
            return redirect(url_for("admin.acting_identity"))

        session["api_identity"] = identity
        flash(catalog_text("admin.api.identity_selected", identity=identity), "ok")
        return redirect(url_for("admin.dashboard"))
