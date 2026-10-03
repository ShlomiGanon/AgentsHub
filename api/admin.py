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
(`api/request_boundary.py`) do: safe on
localhost, sent in the clear over plain HTTP otherwise. This module does not
set the session cookie's `Secure` flag (forcing that on would break login
over plain HTTP in local development) and implements no TLS itself — put a
TLS-terminating reverse proxy (or equivalent) in front before this panel is
reachable from anywhere but localhost, and only then consider also setting
`SESSION_COOKIE_SECURE=True` on the Flask app.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from flask import Blueprint, flash, redirect, request, session, url_for

from api._route_deps import job_status, protocol_to_dict
from api.admin_tables import (
    AdminFormError,
    find_admin_table,
    parse_admin_table_form,
)
from persistence import (
    EventSearchCriteria,
    FireStoreError,
    NeighboringForceStoreError,
    PersistenceError,
    SurveillancePersistenceError,
    TeamStatusPersistenceError,
)
from tools import get_trace_id

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
    _MENU_TEMPLATE,
    _PROFILES_TEMPLATE,
    _PROTOCOLS_TEMPLATE,
    _SHELL_CLOSE,
    _SHELL_OPEN,
)
from api.admin_groups import register_group_routes
from api.admin_server import register_server_routes
from api.admin_session import register_session_routes
from api.admin_simulator_proxy import register_simulator_routes
from api.admin_support import (
    AdminPanel,
    catalog_text,
    client_source,
    format_duration_phrase,
    issue_session,
    render_template,
    text_direction,
)
from api.admin_users import register_user_routes

_ADMIN_TABLE_WRITE_ERRORS = (
    PersistenceError, SurveillancePersistenceError, TeamStatusPersistenceError,
    NeighboringForceStoreError, FireStoreError,
)

# Test and caller aliases kept on this module so existing imports still resolve.
_format_duration_phrase = format_duration_phrase
_t = catalog_text
_render = render_template
_issue_session = issue_session
_client_source = client_source
_text_direction = text_direction


def build_admin_blueprint(ctx: "ApiContext", config: AdminConfig) -> Blueprint:
    """Assemble the /admin blueprint from session, page, user, group, server, and simulator routes."""

    blueprint = Blueprint("admin", __name__, url_prefix="/admin")
    panel = AdminPanel(ctx, config)

    register_session_routes(blueprint, panel)

    @blueprint.route("/", methods=["GET"])
    def dashboard():
        """Admin home: the service-card menu."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response

        return panel.render(
            _MENU_TEMPLATE, csrf_token=session["csrf_token"], admin_tables=ctx.loaded_profile.admin_tables
        )

    @blueprint.route("/tables/<table_key>", methods=["GET"])
    def admin_table_list(table_key):
        """List rows for one profile-declared admin table."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None:
            return redirect(url_for("admin.dashboard"))
        rows = table.list_fn(ctx.deps)
        return panel.render(_ADMIN_TABLES_LIST_TEMPLATE, table=table, rows=rows, csrf_token=session["csrf_token"])

    @blueprint.route("/tables/<table_key>/edit/<row_id>", methods=["GET", "POST"])
    def admin_table_edit(table_key, row_id):
        """Edit one row of a profile-declared admin table."""

        redirect_response = panel.require_session()
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
            return panel.render(
                _ADMIN_TABLES_EDIT_TEMPLATE, table=table, row=row, row_id=row_id, csrf_token=session["csrf_token"]
            )

        csrf_response = panel.require_csrf()
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
        """Create a row in a profile-declared admin table that allows inserts."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None or not table.allow_create:
            return redirect(url_for("admin.dashboard"))
        csrf_response = panel.require_csrf()
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
        """Delete a row from a profile-declared admin table that allows deletes."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        table = find_admin_table(ctx.loaded_profile, table_key)
        if table is None or table.delete_fn is None:
            return redirect(url_for("admin.dashboard"))
        csrf_response = panel.require_csrf()
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
        """JSON-console page for the loaded profile's overview and settings."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response
        return panel.render(
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
            **panel.api_page_context("profiles"),
        )

    @blueprint.route("/protocols", methods=["GET"])
    def protocols():
        """JSON-console page for listing and editing protocols."""

        redirect_response = panel.require_session()
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
        return panel.render(
            _PROTOCOLS_TEMPLATE,
            agents=agents,
            tools=tools,
            protocols=tuple(protocol_to_dict(protocol) for protocol in ctx.deps.protocol_set.all()),
            csrf_token=session["csrf_token"],
            **panel.api_page_context("protocols"),
        )

    @blueprint.route("/events", methods=["GET"])
    def events():
        """JSON-console page for recent events and job status."""

        redirect_response = panel.require_session()
        if redirect_response is not None:
            return redirect_response

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
        return panel.render(
            _EVENTS_TEMPLATE,
            event_types=ctx.deps.event_type_registry.types,
            recent_events=recent_events,
            csrf_token=session["csrf_token"],
            **panel.api_page_context("events"),
        )

    register_user_routes(blueprint, panel)
    register_group_routes(blueprint, panel)
    register_server_routes(blueprint, panel)
    register_simulator_routes(blueprint, panel)

    from api.admin_trace import register_trace_routes
    register_trace_routes(blueprint, ctx, panel.require_session)

    @blueprint.errorhandler(Exception)
    def _admin_unexpected_error(error: Exception):
        """Render a generic admin error page for an unhandled exception."""

        logger.exception(
            "unhandled exception in an admin request", extra={"event": "admin_unexpected_error", "trace_id": get_trace_id()}
        )
        return panel.render(
            '<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8">'
            "<title>{{ t('admin.error_title') }}</title>" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE
            + "</head>" + _SHELL_OPEN
            + '<div class="ls-page"><h1 class="mb-2">{{ t(\'admin.error_title\') }}</h1>'
            '<p class="subtitle">{{ t(\'admin.error_subtitle\') }}</p></div>'
            + _SHELL_CLOSE,
            csrf_token=session.get("csrf_token", ""),
        ), 500

    return blueprint
