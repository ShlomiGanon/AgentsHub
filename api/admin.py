"""Admin web panel (login-gated, server-rendered HTML): users, Telegram group routing, and the
scenario simulator (`api/admin_simulator.py`).

Every string the panel shows comes from the `admin.*` keys of the message catalog
(`messages/en.py`, `messages/he.py`), read through `get_current_catalog()` — which `api/app.py`
sets from the profile's DEFAULT_LANGUAGE before each request — so the panel renders in Hebrew
(right-to-left, Bootstrap's RTL build) for a `he` profile and in English for an `en` one.

Mounted on the same Flask app and port as the JSON API (`api/app.py`), under
`/admin` — a deliberate choice for now: this deployment's whole surface is a
single process on a single port, and the panel's own login + signed-cookie
session + CSRF + per-IP lockout (all below) are judged sufficient for that.
**If this deployment's security requirements grow** (compliance needs,
higher-value data, a genuinely public audience), revisiting a separate
process/port for this panel is worth doing then — it isn't done now because
nothing here calls for it yet.

Authentication here is entirely separate from the rest of the API: every
other route in this package authenticates a caller via the `X-Identity`
header (`api/request_boundary.py`), checked against the `users` table with
no notion of a password at all. This panel is the one place a human types a
password, so it needs its own mechanism — a single shared operator login
(`ADMIN_USERNAME`/`ADMIN_PASSWORD`), a signed session cookie (Flask's own,
no server-side session store), and CSRF protection on every state-changing
form, since a cookie (unlike an explicit header the bot always sends
deliberately) rides along with a browser's requests automatically.

**Required before deploying this beyond localhost: HTTPS.** Every one of the
protections below — the login password, the session cookie, the CSRF token
— travels in the request/response bodies and headers exactly like
`BOT_SERVICE_KEY` (`bot/contracts.py`) and the `X-Identity` scheme
(`api/request_boundary.py`, `docs/PRODUCTION_READY.md` Task 7) do: safe on
localhost, sent in the clear over plain HTTP otherwise. This module does not
set the session cookie's `Secure` flag (forcing that on would break login
over plain HTTP in local development) and implements no TLS itself — put a
TLS-terminating reverse proxy (or equivalent) in front before this panel is
reachable from anywhere but localhost, and only then consider also setting
`SESSION_COOKIE_SECURE=True` on the Flask app.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import TYPE_CHECKING

import httpx
from flask import Blueprint, flash, get_flashed_messages, jsonify, redirect, render_template_string, request, session, url_for

from api.admin_simulator import simulator_page_context
from api.routes import protocol_to_dict
from api.admin_tables import (
    AdminFormError,
    find_admin_table,
    parse_admin_table_form,
)
from api.request_boundary import (
    BOT_SERVICE_IDENTITY,
    BOT_SERVICE_KEY_ENV_VAR,
    SERVICE_KEY_HEADER,
    SYSTEM_ADMIN_IDENTITY,
    secrets_equal,
)
from auth.permissions import InvalidFullNameError, PermissionLevel, normalize_full_name
from config import discover_profiles, read_server_status, submit_server_command, supervisor_available
from messages import get_current_catalog
from orchestrator.flows import InvalidRoutingTargetError
from persistence import (
    EventSearchCriteria,
    FireStoreError,
    NeighboringForceStoreError,
    NotFoundError,
    PersistenceError,
    SurveillancePersistenceError,
    TeamStatusPersistenceError,
)

_ADMIN_TABLE_WRITE_ERRORS = (
    PersistenceError, SurveillancePersistenceError, TeamStatusPersistenceError,
    NeighboringForceStoreError, FireStoreError,
)
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

from api.admin_config import AdminConfig, AdminConfigError, LoginRateLimiter, resolve_admin_config
from api.admin_chrome import (
    _ADMIN_TABLES_EDIT_TEMPLATE,
    _ADMIN_TABLES_LIST_TEMPLATE,
    _BOOTSTRAP_CSS_LINK,
    _DASHBOARD_STYLE,
    _EVENTS_TEMPLATE,
    _GROUPS_TEMPLATE,
    _ACTING_IDENTITY_TEMPLATE,
    _LOGIN_TEMPLATE,
    _MENU_TEMPLATE,
    _PROFILES_TEMPLATE,
    _PROTOCOLS_TEMPLATE,
    _SERVER_TEMPLATE,
    _SERVER_WAIT_TEMPLATE,
    _SHELL_CLOSE,
    _SHELL_OPEN,
    _SIMULATOR_TEMPLATE,
    _RESTART_POLL_MS,
    _RESTART_TIMEOUT_MS,
    _USERS_TEMPLATE,
)

def _client_source() -> str:
    """For audit logging only (who attempted/failed a login) — never used to scope the lockout
    itself; see LoginRateLimiter's docstring for why lockout state is deliberately global."""

    return request.remote_addr or "unknown"


def _format_duration_phrase(remaining_minutes: float) -> str:
    """A coarse, human-friendly phrase for a remaining lockout duration — minutes and hours only,
    never seconds, and never derived from a live-ticking value (the caller passes one snapshot of
    `LoginRateLimiter.remaining_minutes()`, computed once for this page load/response)."""

    catalog = get_current_catalog()
    if remaining_minutes < 1:
        return catalog.text("admin.lockout_less_than_a_minute")
    if remaining_minutes < 60:
        minutes = max(1, round(remaining_minutes))
        if minutes == 1:
            return catalog.text("admin.lockout_one_minute")
        return catalog.text("admin.lockout_minutes", minutes=minutes)
    # Nearest half hour reads more naturally for a coarse wait estimate than a raw minute count
    # (e.g. "about 1.5 hours" rather than "about 90 minutes") — doesn't need to be exact.
    hours = round(remaining_minutes / 30) / 2
    if hours <= 1:
        return catalog.text("admin.lockout_one_hour")
    hours_value: float | int = int(hours) if hours == int(hours) else hours
    return catalog.text("admin.lockout_hours", hours=hours_value)


def _t(key: str, **values: object) -> str:
    """Admin-panel text through the request's message catalog (`api/app.py` sets it from the
    profile's DEFAULT_LANGUAGE before every request), so the panel follows the profile's
    language — Hebrew for a `he` profile, English for an `en` one — with no second mechanism."""

    return get_current_catalog().text(key, **values)


def _text_direction() -> str:
    return "rtl" if get_current_catalog().language == "he" else "ltr"


def _render(template: str, **context) -> str:
    """render_template_string plus the three values every admin template needs: the catalog
    text function `t`, and the `lang`/`dir` attributes for the `<html>` element."""

    return render_template_string(
        template, t=_t, lang=get_current_catalog().language, dir=_text_direction(), **context
    )


def _issue_session(config: AdminConfig) -> None:
    session.clear()
    session["admin_authenticated"] = True
    session["last_activity"] = time.time()
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True


def build_admin_blueprint(ctx: "ApiContext", config: AdminConfig) -> Blueprint:
    blueprint = Blueprint("admin", __name__, url_prefix="/admin")
    rate_limiter = LoginRateLimiter(config.login_max_attempts, config.login_lockout_minutes)
    levels = [level.name.lower() for level in PermissionLevel]
    _page_render = globals()["_render"]

    def _acting_identity_status() -> str:
        identity = str(session.get("api_identity") or "")
        if not identity:
            return ""
        user = ctx.deps.persistence.read_user(identity) or {}
        name = (user.get("full_name") or "").strip() or _t("admin.api.missing_name")
        level_key = user.get("permission_level") or ""
        level = _t(f"admin.permission.{level_key}") if level_key in {"commander", "viewer"} else level_key
        return _t("admin.connected_as", name=name, identity=identity, level=level)

    def _render(template: str, **context) -> str:
        context.setdefault("admin_tables", ctx.loaded_profile.admin_tables)
        identity = str(session.get("api_identity") or "")
        if identity:
            context.setdefault("api_identity", identity)
            context.setdefault("acting_identity_status", _acting_identity_status())
        return _page_render(template, **context)

    def _api_users() -> list[dict]:
        """Human identities available in the acting-identity select.

        `bot-service` cannot be chosen at all. The console system administrator
        (`Admin`) is also omitted here — the setup checkbox is the only way to
        select it.
        """

        reserved = {BOT_SERVICE_IDENTITY, SYSTEM_ADMIN_IDENTITY}
        return sorted(
            (
                user
                for user in ctx.deps.persistence.list_users()
                if user["telegram_identity"] not in reserved
            ),
            key=lambda user: (
                user["permission_level"] != "commander",
                user["full_name"].casefold(),
                user["telegram_identity"],
            ),
        )

    def _allowed_api_identities() -> set[str]:
        """Human users from `_api_users()` plus the console system administrator.

        `bot-service` is never a valid acting identity."""

        return {user["telegram_identity"] for user in _api_users()} | {SYSTEM_ADMIN_IDENTITY}

    def _session_identity_valid() -> bool:
        selected = str(session.get("api_identity") or "")
        return selected in _allowed_api_identities()

    def _api_page_context(current_page: str) -> dict:
        selected = str(session.get("api_identity") or "")
        return {
            "api_users": _api_users(),
            "api_identity": selected,
            "current_page": current_page,
        }

    def _session_expired() -> bool:
        last_activity = session.get("last_activity")
        if last_activity is None:
            return True
        return (time.time() - last_activity) > config.session_timeout_minutes * 60

    def _require_session(*, require_identity: bool = True):
        """None if the caller has a live admin session (and refreshes its inactivity window);
        otherwise a redirect response the route must return immediately.

        After login the session has no acting identity (`_issue_session` clears it).
        Every page except login, logout, and the one-time setup form must have a
        still-registered `api_identity` (a human user, or `Admin`)."""

        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login"))
        if _session_expired():
            session.clear()
            flash(_t("admin.session_expired"), "error")
            return redirect(url_for("admin.login"))
        session["last_activity"] = time.time()
        if require_identity and not _session_identity_valid():
            return redirect(url_for("admin.acting_identity"))
        return None

    def _lockout_context(remaining_minutes: float) -> dict:
        """Template values for the lockout banner + its static elapsed/remaining progress bar —
        recomputed fresh from `remaining_minutes` every call (see LoginRateLimiter's docstring:
        there is no stored "remaining time", only a live computation from one timestamp)."""

        duration = config.login_lockout_minutes
        elapsed = max(0.0, duration - remaining_minutes)
        percent_elapsed = min(100, max(0, round(elapsed / duration * 100)))
        return {
            "message": get_current_catalog().text(
                "admin.login_locked_out", duration=_format_duration_phrase(remaining_minutes)
            ),
            "percent_elapsed": percent_elapsed,
        }

    def _render_login():
        """The single place that renders the login page — a plain GET and a failed POST both
        redirect here (Post/Redirect/Get, so a browser refresh never resubmits credentials), and
        this is the only place that decides what to show: a locked-out endpoint always shows the
        lockout banner (recomputed live), taking precedence over any queued flash message; only
        when not locked out does a queued flash (wrong credentials, session expired, ...) render."""

        remaining = rate_limiter.remaining_minutes()
        if remaining > 0:
            get_flashed_messages()  # discard — the lockout banner takes precedence, never both
            return _render(_LOGIN_TEMPLATE, lockout=_lockout_context(remaining))
        return _render(_LOGIN_TEMPLATE, lockout=None)

    def _require_csrf():
        """None if the submitted csrf_token matches this session's; otherwise a redirect the
        route must return immediately. Checked as bytes (see api/request_boundary.py's identical
        reasoning): hmac.compare_digest raises on a non-ASCII str instead of just returning False."""

        expected = session.get("csrf_token")
        provided = request.form.get("csrf_token")
        if not secrets_equal(provided, expected):
            logger.warning(
                "admin CSRF token mismatch", extra={"event": "admin_csrf_rejected", "route": request.path, "trace_id": get_trace_id()}
            )
            flash(_t("admin.csrf_failed"), "error")
            return redirect(url_for("admin.dashboard"))
        return None

    @blueprint.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("admin_authenticated") and not _session_expired():
                if _session_identity_valid():
                    return redirect(url_for("admin.dashboard"))
                return redirect(url_for("admin.acting_identity"))
            return _render_login()

        source = _client_source()  # audit logging only — the lockout itself is global, see above
        remaining = rate_limiter.remaining_minutes()
        if remaining > 0:
            logger.info(
                "admin login attempt while locked out",
                extra={"event": "admin_login_locked_out", "source_ip": source, "trace_id": get_trace_id()},
            )
            return redirect(url_for("admin.login"))

        submitted_username = request.form.get("username", "")
        submitted_password = request.form.get("password", "")
        username_ok = secrets_equal(submitted_username, config.username)
        password_ok = secrets_equal(submitted_password, config.password)

        if username_ok and password_ok:
            rate_limiter.record_success()
            _issue_session(config)
            logger.info(
                "admin login succeeded",
                extra={"event": "admin_login_succeeded", "source_ip": source, "trace_id": get_trace_id()},
            )
            return redirect(url_for("admin.acting_identity"))

        remaining_after_failure = rate_limiter.record_failure()
        logger.warning(
            "admin login failed",
            extra={
                "event": "admin_login_failed", "source_ip": source,
                "attempted_username": submitted_username, "trace_id": get_trace_id(),
            },
        )
        if remaining_after_failure <= 0:
            # Deliberately generic — never says which of username/password was wrong.
            flash(get_current_catalog().text("admin.login_wrong_credentials"), "error")
        # else: this failure just crossed the lockout threshold. Don't flash the generic message
        # for it — the redirect below re-renders via _render_login(), which recomputes
        # remaining_minutes() fresh and shows the lockout banner instead, so the one attempt that
        # actually causes a lockout tells the user that, not "you mistyped your password."
        return redirect(url_for("admin.login"))

    @blueprint.route("/logout", methods=["POST"])
    def logout():
        redirect_response = _require_session(require_identity=False)
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        logger.info("admin logout", extra={"event": "admin_logout", "trace_id": get_trace_id()})
        session.clear()
        flash(_t("admin.signed_out"), "ok")
        return redirect(url_for("admin.login"))

    @blueprint.route("/acting-identity", methods=["GET", "POST"])
    def acting_identity():
        """One-time post-login choice of the Telegram identity this session acts as.

        There is no later switcher: change identity by logging out and completing
        this step again. The system administrator (`Admin`) is only available
        through the checkbox. `bot-service` cannot be chosen."""

        redirect_response = _require_session(require_identity=False)
        if redirect_response is not None:
            return redirect_response

        users = _api_users()
        if request.method == "GET":
            if _session_identity_valid():
                return redirect(url_for("admin.dashboard"))
            return _render(
                _ACTING_IDENTITY_TEMPLATE,
                csrf_token=session["csrf_token"],
                api_users=users,
            )

        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        if request.form.get("use_system_admin"):
            session["api_identity"] = SYSTEM_ADMIN_IDENTITY
            flash(_t("admin.api.identity_selected", identity=SYSTEM_ADMIN_IDENTITY), "ok")
            return redirect(url_for("admin.dashboard"))

        identity = request.form.get("api_identity", "").strip()
        if identity not in {user["telegram_identity"] for user in users}:
            flash(_t("admin.api.identity_invalid"), "error")
            return redirect(url_for("admin.acting_identity"))

        session["api_identity"] = identity
        flash(_t("admin.api.identity_selected", identity=identity), "ok")
        return redirect(url_for("admin.dashboard"))

    @blueprint.route("/", methods=["GET"])
    def dashboard():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response

        return _render(
            _MENU_TEMPLATE, csrf_token=session["csrf_token"], admin_tables=ctx.loaded_profile.admin_tables
        )

    @blueprint.route("/tables/<table_key>", methods=["GET"])
    def admin_table_list(table_key):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None:
            return redirect(url_for("admin.dashboard"))
        rows = table.list_fn(ctx.deps)
        return _render(_ADMIN_TABLES_LIST_TEMPLATE, table=table, rows=rows, csrf_token=session["csrf_token"])

    @blueprint.route("/tables/<table_key>/edit/<row_id>", methods=["GET", "POST"])
    def admin_table_edit(table_key, row_id):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None:
            return redirect(url_for("admin.dashboard"))

        if request.method == "GET":
            row = table.get_fn(ctx.deps, row_id)
            if row is None:
                flash(_t("admin.tables.row_not_found"), "error")
                return redirect(url_for("admin.admin_table_list", table_key=table_key))
            return _render(
                _ADMIN_TABLES_EDIT_TEMPLATE, table=table, row=row, row_id=row_id, csrf_token=session["csrf_token"]
            )

        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            values = parse_admin_table_form(table, request.form)
        except AdminFormError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_table_edit", table_key=table_key, row_id=row_id))
        try:
            table.write_fn(ctx.deps, {table.primary_key: row_id, **values})
        except _ADMIN_TABLE_WRITE_ERRORS as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_table_edit", table_key=table_key, row_id=row_id))
        logger.info(
            "admin edited a table row",
            extra={"event": "admin_table_row_edited", "table_key": table_key, "row_id": row_id, "trace_id": get_trace_id()},
        )
        flash(_t("admin.tables.saved"), "ok")
        return redirect(url_for("admin.admin_table_list", table_key=table_key))

    @blueprint.route("/tables/<table_key>/new", methods=["POST"])
    def admin_table_new(table_key):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None or not table.allow_create:
            return redirect(url_for("admin.dashboard"))
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            values = parse_admin_table_form(table, request.form)
        except AdminFormError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_table_list", table_key=table_key))
        try:
            table.write_fn(ctx.deps, values)
        except _ADMIN_TABLE_WRITE_ERRORS as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_table_list", table_key=table_key))
        flash(_t("admin.tables.saved"), "ok")
        return redirect(url_for("admin.admin_table_list", table_key=table_key))

    @blueprint.route("/tables/<table_key>/<row_id>/delete", methods=["POST"])
    def admin_table_delete(table_key, row_id):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None or table.delete_fn is None:
            return redirect(url_for("admin.dashboard"))
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            table.delete_fn(ctx.deps, row_id)
        except _ADMIN_TABLE_WRITE_ERRORS as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.admin_table_list", table_key=table_key))
        flash(_t("admin.tables.deleted"), "ok")
        return redirect(url_for("admin.admin_table_list", table_key=table_key))

    @blueprint.route("/profiles", methods=["GET"])
    def profiles():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        return _render(
            _PROFILES_TEMPLATE,
            profile_name=ctx.loaded_profile.profile_name,
            profile_module=ctx.loaded_profile.module_path,
            agents=tuple(sorted(agent.name for agent in ctx.deps.registry.all())),
            protocols=tuple(sorted(protocol.name for protocol in ctx.deps.protocol_set.all())),
            event_types=ctx.deps.event_type_registry.types,
            areas=ctx.deps.area_registry.areas,
            settings={
                "retry_count": ctx.deps.settings_store.get_retry_count(),
                "risk_threshold": ctx.deps.settings_store.get_risk_threshold(),
                "lookback_window_days": ctx.deps.settings_store.get_lookback_window_days(),
            },
            csrf_token=session["csrf_token"],
            **_api_page_context("profiles"),
        )

    @blueprint.route("/protocols", methods=["GET"])
    def protocols():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        agents = tuple(sorted(agent.name for agent in ctx.deps.registry.all()))
        tools = tuple(
            sorted(
                {
                    tool.name
                    for agent in ctx.deps.registry.all()
                    for tool in agent.exposed_tools()
                }
            )
        )
        return _render(
            _PROTOCOLS_TEMPLATE,
            agents=agents,
            tools=tools,
            protocols=tuple(protocol_to_dict(protocol) for protocol in ctx.deps.protocol_set.all()),
            csrf_token=session["csrf_token"],
            **_api_page_context("protocols"),
        )

    @blueprint.route("/events", methods=["GET"])
    def events():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        from api.routes import job_status

        registered_users = {
            user["telegram_identity"]: user for user in ctx.deps.persistence.list_users()
        }
        recent = ctx.deps.persistence.search_events(
            EventSearchCriteria(time_basis="received_at", order="newest", limit=50)
        )
        recent_events = []
        for event in recent:
            status = job_status(ctx, event["event_id"]) or {"status": "unknown"}
            sender = registered_users.get(event.get("sender_identity"))
            recent_events.append(
                {
                    "event_id": event["event_id"],
                    "text": event.get("raw_text") or "",
                    "source": event.get("source") or "",
                    "sender_identity": event.get("sender_identity") or "",
                    "sender_name": (sender or {}).get("full_name") or "",
                    "classification": event.get("classification") or "",
                    "area": event.get("area") or "",
                    "received_at": event.get("received_at") or "",
                    "status": status.get("status") or "unknown",
                }
            )
        return _render(
            _EVENTS_TEMPLATE,
            event_types=ctx.deps.event_type_registry.types,
            recent_events=recent_events,
            csrf_token=session["csrf_token"],
            **_api_page_context("events"),
        )

    @blueprint.route("/users", methods=["GET"])
    def users():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        registered_users = sorted(
            ctx.deps.persistence.list_users(), key=lambda user: user["telegram_identity"]
        )
        return _render(
            _USERS_TEMPLATE,
            users=registered_users,
            levels=levels,
            safe_mode=ctx.deps.settings_store.get_safe_mode(),
            csrf_token=session["csrf_token"],
            bot_service_identity=BOT_SERVICE_IDENTITY,
        )

    @blueprint.route("/groups", methods=["GET"])
    def groups():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        return _render(
            _GROUPS_TEMPLATE,
            groups=ctx.group_routing.all(),
            routable_agents=ctx.group_routing.routable_targets,
            safe_mode=ctx.deps.settings_store.get_safe_mode(),
            csrf_token=session["csrf_token"],
        )

    @blueprint.route("/server", methods=["GET"])
    def server():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        return _render(
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
            **_api_page_context("server"),
        )

    def _restart_page(port: int):
        host = request.host.split(":", 1)[0]
        target_url = f"{request.scheme}://{host}:{port}/admin/server"
        old_url = f"{request.scheme}://{host}:{ctx.loaded_profile.api_port}/admin/server"
        return _render(
            _SERVER_WAIT_TEMPLATE,
            target_url=target_url,
            old_url=old_url,
            timeout_ms=_RESTART_TIMEOUT_MS,
            poll_ms=_RESTART_POLL_MS,
            csrf_token=session.get("csrf_token", ""),
        )

    @blueprint.route("/server/profile", methods=["POST"])
    def switch_profile():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        module_path = request.form.get("profile_module", "").strip()
        selected = next((profile for profile in discover_profiles() if profile.module_path == module_path), None)
        if selected is None:
            flash(_t("admin.server_profile_invalid"), "error")
            return redirect(url_for("admin.server"))
        try:
            submit_server_command("switch_profile", profile_module=module_path)
        except RuntimeError:
            flash(_t("admin.server_unavailable"), "error")
            return redirect(url_for("admin.server"))
        logger.info("admin requested profile switch", extra={"event": "admin_profile_switch", "profile_module": module_path, "trace_id": get_trace_id()})
        return _restart_page(selected.api_port)

    @blueprint.route("/server/reset", methods=["POST"])
    def reset_server():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        if request.form.get("confirm") != "yes":
            flash(_t("admin.server_reset_confirmation_missing"), "error")
            return redirect(url_for("admin.server"))
        try:
            submit_server_command("reset")
        except RuntimeError:
            flash(_t("admin.server_unavailable"), "error")
            return redirect(url_for("admin.server"))
        logger.warning("admin requested database reset", extra={"event": "admin_database_reset", "profile_module": ctx.loaded_profile.module_path, "trace_id": get_trace_id()})
        return _restart_page(ctx.loaded_profile.api_port)

    @blueprint.route("/simulator", methods=["GET"])
    def simulator():
        """Scenario simulator (api/admin_simulator.py). Session-gated like every other admin
        page; the steps it releases are then sent by the browser to the real /Msg and /Event
        under each step's own X-Identity, so this route itself only serves the page + data."""

        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response

        api_context = _api_page_context("simulator")
        return _render(
            _SIMULATOR_TEMPLATE,
            page_data=simulator_page_context(
                ctx, get_current_catalog(), BOT_SERVICE_IDENTITY, api_identity=api_context["api_identity"]
            ),
            csrf_token=session["csrf_token"],
            **api_context,
        )

    def _forward_to_simulator(method: str, path: str, **kwargs):
        """Shared plumbing for every `bot.simulator_app` proxy route (§4.4, and
        Priority 3's polling extension) — no business logic, just the HTTP call
        and its two failure shapes. `**kwargs` (`json=`/`params=`) pass straight
        through to `httpx`. Returns a `(flask_response, status_code)` pair the
        caller returns directly."""

        simulator_port = ctx.loaded_profile.simulator_port
        if not simulator_port:
            return jsonify({"error": {"message": _t("admin.simulator.bot_mode_unconfigured")}}), 501

        service_key = os.environ.get(BOT_SERVICE_KEY_ENV_VAR) or ""
        forward_headers = {SERVICE_KEY_HEADER: service_key}
        fwd_trace = request.headers.get("X-Trace-ID") or (kwargs.get("json", {}).get("trace_id") if isinstance(kwargs.get("json"), dict) else None)
        if fwd_trace:
            forward_headers["X-Trace-ID"] = str(fwd_trace)
        try:
            response = httpx.request(
                method,
                f"http://localhost:{simulator_port}{path}",
                headers=forward_headers,
                timeout=httpx.Timeout(connect=2.0, pool=2.0, write=5.0, read=75.0),
                **kwargs,
            )
        except httpx.HTTPError:
            logger.warning(
                "simulation-mode bot process unreachable",
                extra={"event": "admin_simulator_bot_unreachable", "trace_id": get_trace_id()},
            )
            return jsonify({"error": {"message": _t("admin.simulator.bot_mode_unreachable")}}), 502

        try:
            body = response.json()
        except ValueError:
            body = {}
        return jsonify(body), response.status_code

    @blueprint.route("/simulator/bot-msg", methods=["POST"])
    def simulator_bot_msg():
        """Proxies one message-kind simulation step to `bot.simulator_app`'s own
        `POST /Simulator-msg` (docs/bot_simulation_mode_design.md §4.4) — pure plumbing,
        no business logic here. The browser never talks to the simulator process
        directly (same-origin only, like every other admin call); an HTTP call rather
        than a Python import because `tests/test_architecture.py`'s package boundary
        lets `api` import only `bot`/`bot.app`, never a new submodule directly — the
        same reason `bot/transports.py`'s `HttpApiClient` reaches `api.app` over HTTP
        rather than importing it.

        Session-gated like every other admin route; no CSRF token, since this is a
        JSON `fetch()` call (`request.form` is always empty for it, which is what
        `_require_csrf()` checks) rather than an HTML form submission — the same
        distinction Flask's own CSRF guidance draws, and this route makes no state
        change of its own besides what the simulator process's own identity-allowlist
        gate (§4.3) already permits.
        """

        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response

        payload = request.get_json(silent=True) or {}
        return _forward_to_simulator("POST", "/Simulator-msg", json=payload)

    @blueprint.route("/simulator/bot-poll", methods=["GET"])
    def simulator_bot_poll():
        """Proxies to `bot.simulator_app`'s `GET /Simulator-msg/poll` (Priority 3,
        docs/work_process.md §16) — lets the admin page ask whether anything new
        has arrived in a chat since a previous reply/poll's watermark, so a real,
        unmodified `run_notification_poll_loop` delivery (a job result, a held-
        approval/clarification prompt, ...) actually reaches the operator instead
        of the earlier dead-end promise. Same session gating, same reasoning, as
        the POST route above — a read-only GET, so no CSRF concern at all."""

        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response

        params = {
            "chat_id": request.args.get("chat_id", ""),
            "status_len": request.args.get("status_len", "0"),
            "sent_len": request.args.get("sent_len", "0"),
        }
        return _forward_to_simulator("GET", "/Simulator-msg/poll", params=params)

    from api.admin_trace import register_trace_routes
    register_trace_routes(blueprint, ctx, _require_session)

    @blueprint.route("/groups", methods=["POST"])
    def write_group():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
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
                flash(_t("admin.attendance_hour_invalid"), "error")
                return redirect(url_for("admin.groups"))
            if attendance_check_hour < 0 or attendance_check_hour > 23:
                flash(_t("admin.attendance_hour_invalid"), "error")
                return redirect(url_for("admin.groups"))
        if not chat_id:
            flash(_t("admin.chat_id_required"), "error")
            return redirect(url_for("admin.groups"))

        existed = ctx.group_routing.get(chat_id) is not None
        try:
            # The same write-through path PUT /Groups and cli.group_admin use — one
            # source of truth for the routing table either way.
            ctx.group_routing.upsert(
                chat_id,
                agent_name,
                label,
                attendance_check_enabled=attendance_check_enabled,
                attendance_check_hour=attendance_check_hour,
            )
        except InvalidRoutingTargetError:
            flash(_t("admin.group_agent_invalid", agent=agent_name), "error")
            return redirect(url_for("admin.groups"))
        logger.info(
            "admin wrote a telegram group binding",
            extra={
                "event": "admin_group_updated" if existed else "admin_group_added",
                "chat_id": chat_id, "agent_name": agent_name, "trace_id": get_trace_id(),
            },
        )
        flash(_t("admin.group_routed", chat_id=chat_id, agent=agent_name), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/groups/<chat_id>/remove", methods=["POST"])
    def remove_group(chat_id):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        try:
            ctx.group_routing.remove(chat_id)
        except NotFoundError:
            flash(_t("admin.group_not_found", chat_id=chat_id), "error")
            return redirect(url_for("admin.groups"))

        logger.info(
            "admin removed a telegram group binding",
            extra={"event": "admin_group_removed", "chat_id": chat_id, "trace_id": get_trace_id()},
        )
        flash(_t("admin.group_removed", chat_id=chat_id), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/groups/<chat_id>/approve", methods=["POST"])
    def approve_group(chat_id):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            ctx.group_routing.approve(chat_id)
        except NotFoundError:
            flash(_t("admin.group_not_found", chat_id=chat_id), "error")
            return redirect(url_for("admin.groups"))
        logger.info(
            "admin approved a telegram group",
            extra={"event": "telegram_group_approved", "chat_id": chat_id, "approved_by": "admin-session", "trace_id": get_trace_id()},
        )
        record_telegram_security_metric("approved", "group")
        flash(_t("admin.group_approved", chat_id=chat_id), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/groups/<chat_id>/rename", methods=["POST"])
    def rename_group(chat_id):
        """Change a group's chat_id in place — generic (any group, not simulation-specific);
        e.g. an operator replacing a simulation group's reserved placeholder chat ID with a
        real Telegram group ID once one exists (docs/profile_simulations_design.md)."""

        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        new_chat_id = request.form.get("new_chat_id", "").strip()
        if not new_chat_id:
            flash(_t("admin.new_chat_id_required"), "error")
            return redirect(url_for("admin.groups"))
        if not new_chat_id.lstrip("-").isdigit() or not new_chat_id.startswith("-") or int(new_chat_id) >= 0:
            flash(_t("admin.new_chat_id_invalid"), "error")
            return redirect(url_for("admin.groups"))

        try:
            ctx.group_routing.rename(chat_id, new_chat_id)
        except NotFoundError:
            flash(_t("admin.group_not_found", chat_id=chat_id), "error")
            return redirect(url_for("admin.groups"))
        except PersistenceError:
            flash(_t("admin.group_chat_id_taken", chat_id=new_chat_id), "error")
            return redirect(url_for("admin.groups"))

        logger.info(
            "admin renamed a telegram group's chat ID",
            extra={
                "event": "admin_group_renamed",
                "old_chat_id": chat_id, "new_chat_id": new_chat_id, "trace_id": get_trace_id(),
            },
        )
        flash(_t("admin.group_renamed", old_chat_id=chat_id, new_chat_id=new_chat_id), "ok")
        return redirect(url_for("admin.groups"))

    @blueprint.route("/users", methods=["POST"])
    def write_user():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        identity = request.form.get("telegram_identity", "").strip()
        level = request.form.get("permission_level", "")
        raw_full_name = request.form.get("full_name")
        if not identity:
            flash(_t("admin.identity_required"), "error")
            return redirect(url_for("admin.users"))
        if level not in levels:
            flash(_t("admin.level_invalid", level=level), "error")
            return redirect(url_for("admin.users"))

        existed = ctx.deps.persistence.read_user(identity) is not None
        try:
            full_name = (
                None
                if raw_full_name is None and existed
                else normalize_full_name(raw_full_name or "", allow_empty=True)
            )
        except InvalidFullNameError:
            flash(_t("admin.full_name_invalid"), "error")
            return redirect(url_for("admin.users"))

        ctx.deps.persistence.write_user(identity, level, full_name)
        logger.info(
            "admin wrote a user",
            extra={
                "event": "admin_user_updated" if existed else "admin_user_added",
                "telegram_identity": identity, "permission_level": level, "trace_id": get_trace_id(),
            },
        )
        flash(_t("admin.user_written", identity=identity, level=level), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.route("/users/<identity>/remove", methods=["POST"])
    def remove_user(identity):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        try:
            ctx.deps.persistence.delete_user(identity)
        except NotFoundError:
            flash(_t("admin.user_not_found", identity=identity), "error")
            return redirect(url_for("admin.users"))

        logger.info(
            "admin removed a user",
            extra={"event": "admin_user_removed", "telegram_identity": identity, "trace_id": get_trace_id()},
        )
        flash(_t("admin.user_removed", identity=identity), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.route("/users/<identity>/approve", methods=["POST"])
    def approve_user(identity):
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        try:
            ctx.deps.persistence.approve_user(identity)
        except NotFoundError:
            flash(_t("admin.user_not_found", identity=identity), "error")
            return redirect(url_for("admin.users"))
        logger.info(
            "admin approved a telegram user",
            extra={"event": "telegram_user_approved", "telegram_identity": identity, "approved_by": "admin-session", "trace_id": get_trace_id()},
        )
        record_telegram_security_metric("approved", "user")
        flash(_t("admin.user_approved", identity=identity), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.route("/bot-service/provision", methods=["POST"])
    def provision_bot_service():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        # Same write as api.app.ensure_bot_service (startup auto-register) — one source of truth.
        from api.app import ensure_bot_service

        ensure_bot_service(ctx.deps.persistence)
        logger.info(
            "admin (re-)provisioned the bot-service identity",
            extra={"event": "admin_bot_service_provisioned", "trace_id": get_trace_id()},
        )
        flash(_t("admin.bot_service_provisioned", identity=BOT_SERVICE_IDENTITY), "ok")
        return redirect(url_for("admin.users"))

    @blueprint.errorhandler(Exception)
    def _admin_unexpected_error(error: Exception):
        logger.exception(
            "unhandled exception in an admin request", extra={"event": "admin_unexpected_error", "trace_id": get_trace_id()}
        )
        return _render(
            '<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8">'
            "<title>{{ t('admin.error_title') }}</title>" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE
            + "</head>" + _SHELL_OPEN
            + '<div class="ls-page"><h1 class="mb-2">{{ t(\'admin.error_title\') }}</h1>'
            '<p class="subtitle">{{ t(\'admin.error_subtitle\') }}</p></div>'
            + _SHELL_CLOSE,
            csrf_token=session.get("csrf_token", ""),
        ), 500

    return blueprint

