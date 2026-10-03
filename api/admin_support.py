"""Shared admin-panel helpers: session, CSRF, acting identity, and page render."""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING

from auth.permissions import PermissionLevel
from flask import flash, get_flashed_messages, redirect, render_template_string, request, session, url_for

from api.admin_chrome import _LOGIN_TEMPLATE, _RESTART_POLL_MS, _RESTART_TIMEOUT_MS, _SERVER_WAIT_TEMPLATE
from api.admin_config import AdminConfig, LoginRateLimiter
from api.request_boundary import BOT_SERVICE_IDENTITY, SYSTEM_ADMIN_IDENTITY, secrets_equal
from messages import get_current_catalog
from tools import get_trace_id

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)


def client_source() -> str:
    """Remote address for login-audit logs only; lockout itself is global."""

    return request.remote_addr or "unknown"


def format_duration_phrase(remaining_minutes: float) -> str:
    """Human-friendly remaining lockout duration from one snapshot of minutes."""

    catalog = get_current_catalog()
    if remaining_minutes < 1:
        return catalog.text("admin.lockout_less_than_a_minute")
    if remaining_minutes < 60:
        minutes = max(1, round(remaining_minutes))
        if minutes == 1:
            return catalog.text("admin.lockout_one_minute")
        return catalog.text("admin.lockout_minutes", minutes=minutes)
    hours = round(remaining_minutes / 30) / 2
    if hours <= 1:
        return catalog.text("admin.lockout_one_hour")
    hours_value: float | int = int(hours) if hours == int(hours) else hours
    return catalog.text("admin.lockout_hours", hours=hours_value)


def catalog_text(key: str, **values: object) -> str:
    """Admin-panel catalog text in the request's profile language."""

    return get_current_catalog().text(key, **values)


def text_direction() -> str:
    """HTML dir for the current catalog language."""

    return "rtl" if get_current_catalog().language == "he" else "ltr"


def render_template(template: str, **context) -> str:
    """Render an admin template with catalog text, lang, and dir."""

    return render_template_string(
        template, t=catalog_text, lang=get_current_catalog().language, dir=text_direction(), **context
    )


def issue_session(config: AdminConfig) -> None:
    """Start a signed admin session with a fresh CSRF token and no acting identity."""

    session.clear()
    session["admin_authenticated"] = True
    session["last_activity"] = time.time()
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True


class AdminPanel:
    """Per-blueprint helpers shared by every admin route module."""

    def __init__(self, ctx: "ApiContext", config: AdminConfig) -> None:
        """Bind this panel to one API context, config, and a fresh login lockout tracker."""

        self.ctx = ctx
        self.config = config
        self.rate_limiter = LoginRateLimiter(config.login_max_attempts, config.login_lockout_minutes)
        self.levels = [level.name.lower() for level in PermissionLevel]

    def acting_identity_status(self) -> str:
        """Topbar label for the session's acting identity, or empty if none."""

        identity = str(session.get("api_identity") or "")
        if not identity:
            return ""
        user = self.ctx.deps.persistence.read_user(identity) or {}
        name = (user.get("full_name") or "").strip() or catalog_text("admin.api.missing_name")
        level_key = user.get("permission_level") or ""
        level = catalog_text(f"admin.permission.{level_key}") if level_key in {"commander", "viewer"} else level_key
        return catalog_text("admin.connected_as", name=name, identity=identity, level=level)

    def render(self, template: str, **context) -> str:
        """Render a page, injecting admin tables and the acting-identity topbar."""

        context.setdefault("admin_tables", self.ctx.loaded_profile.admin_tables)
        identity = str(session.get("api_identity") or "")
        if identity:
            context.setdefault("api_identity", identity)
            context.setdefault("acting_identity_status", self.acting_identity_status())
        return render_template(template, **context)

    def api_users(self) -> list[dict]:
        """Human identities the acting-identity form may list (not bot-service or Admin)."""

        reserved = {BOT_SERVICE_IDENTITY, SYSTEM_ADMIN_IDENTITY}
        return sorted(
            (
                user
                for user in self.ctx.deps.persistence.list_users()
                if user["telegram_identity"] not in reserved
            ),
            key=lambda user: (
                user["permission_level"] != "commander",
                user["full_name"].casefold(),
                user["telegram_identity"],
            ),
        )

    def allowed_api_identities(self) -> set[str]:
        """Selectable humans plus the console system administrator; never bot-service."""

        return {user["telegram_identity"] for user in self.api_users()} | {SYSTEM_ADMIN_IDENTITY}

    def session_identity_valid(self) -> bool:
        """Whether the session's api_identity is still a permitted acting identity."""

        selected = str(session.get("api_identity") or "")
        return selected in self.allowed_api_identities()

    def api_page_context(self, current_page: str) -> dict:
        """Template values shared by JSON-console admin pages."""

        selected = str(session.get("api_identity") or "")
        return {
            "api_users": self.api_users(),
            "api_identity": selected,
            "current_page": current_page,
        }

    def session_expired(self) -> bool:
        """Whether the inactivity window has elapsed."""

        last_activity = session.get("last_activity")
        if last_activity is None:
            return True
        return (time.time() - last_activity) > self.config.session_timeout_minutes * 60

    def require_session(self, *, require_identity: bool = True):
        """None if the admin session is live; otherwise a redirect response."""

        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login"))
        if self.session_expired():
            session.clear()
            flash(catalog_text("admin.session_expired"), "error")
            return redirect(url_for("admin.login"))
        session["last_activity"] = time.time()
        if require_identity and not self.session_identity_valid():
            return redirect(url_for("admin.acting_identity"))
        return None

    def lockout_context(self, remaining_minutes: float) -> dict:
        """Lockout banner values recomputed from remaining minutes."""

        duration = self.config.login_lockout_minutes
        elapsed = max(0.0, duration - remaining_minutes)
        percent_elapsed = min(100, max(0, round(elapsed / duration * 100)))
        return {
            "message": get_current_catalog().text(
                "admin.login_locked_out", duration=format_duration_phrase(remaining_minutes)
            ),
            "percent_elapsed": percent_elapsed,
        }

    def render_login(self) -> str:
        """Login page: lockout banner if locked, otherwise queued flash messages."""

        remaining = self.rate_limiter.remaining_minutes()
        if remaining > 0:
            get_flashed_messages()
            return self.render(_LOGIN_TEMPLATE, lockout=self.lockout_context(remaining))
        return self.render(_LOGIN_TEMPLATE, lockout=None)

    def require_csrf(self):
        """None if the form CSRF token matches; otherwise a dashboard redirect."""

        expected = session.get("csrf_token")
        provided = request.form.get("csrf_token")
        if not secrets_equal(provided, expected):
            logger.warning(
                "admin CSRF token mismatch", extra={"event": "admin_csrf_rejected", "route": request.path, "trace_id": get_trace_id()}
            )
            flash(catalog_text("admin.csrf_failed"), "error")
            return redirect(url_for("admin.dashboard"))
        return None

    def restart_page(self, port: int) -> str:
        """Wait page shown after a profile switch or database reset."""

        host = request.host.split(":", 1)[0]
        target_url = f"{request.scheme}://{host}:{port}/admin/server"
        old_url = f"{request.scheme}://{host}:{self.ctx.loaded_profile.api_port}/admin/server"
        return self.render(
            _SERVER_WAIT_TEMPLATE,
            target_url=target_url,
            old_url=old_url,
            timeout_ms=_RESTART_TIMEOUT_MS,
            poll_ms=_RESTART_POLL_MS,
            csrf_token=session.get("csrf_token", ""),
        )
