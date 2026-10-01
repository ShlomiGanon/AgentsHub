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

import hmac
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from flask import Blueprint, flash, get_flashed_messages, jsonify, redirect, render_template_string, request, session, url_for

from api.admin_simulator import SIMULATOR_BODY, SIMULATOR_STYLE, simulator_page_context
from api.admin_api_pages import (
    API_CLIENT_SCRIPT,
    API_CONSOLE_STYLE,
    EVENTS_BODY,
    IDENTITY_BAR,
    PROFILES_BODY,
    PROTOCOLS_BODY,
)
from api.admin_tables import (
    ADMIN_TABLES_EDIT_BODY,
    ADMIN_TABLES_LIST_BODY,
    AdminFormError,
    find_admin_table,
    parse_admin_table_form,
)
from api.request_boundary import BOT_SERVICE_IDENTITY, BOT_SERVICE_KEY_ENV_VAR, SERVICE_KEY_HEADER
from auth.permissions import InvalidFullNameError, PermissionLevel, normalize_full_name
from config import discover_profiles, read_server_status, submit_server_command, supervisor_available
from messages import get_current_catalog
from orchestrator.flows import InvalidRoutingTargetError
from persistence import (
    EventSearchCriteria,
    NeighboringForceStoreError,
    NotFoundError,
    PersistenceError,
    SurveillancePersistenceError,
    TeamStatusPersistenceError,
)

_ADMIN_TABLE_WRITE_ERRORS = (PersistenceError, SurveillancePersistenceError, TeamStatusPersistenceError, NeighboringForceStoreError)
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)


class AdminConfigError(Exception):
    """The admin panel is enabled (ADMIN_USERNAME/ADMIN_PASSWORD are set) but is otherwise misconfigured."""


@dataclass(frozen=True)
class AdminConfig:
    username: str
    password: str
    session_secret: str
    session_timeout_minutes: int
    login_max_attempts: int
    login_lockout_minutes: int


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise AdminConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if value <= 0:
        raise AdminConfigError(f"{name} must be a positive number, got {value}")
    return value


def resolve_admin_config() -> AdminConfig | None:
    """The admin panel's configuration, or None if it isn't enabled.

    Enabled only when both ADMIN_USERNAME and ADMIN_PASSWORD are set (mirrors how a missing
    BOT_TOKEN skips Telegram wiring in bot.app.build_deps) — when disabled, the caller must not
    register any /admin route at all, not register them behind a lock. When enabled,
    ADMIN_SESSION_SECRET is required (no default, no silent fallback) — raises AdminConfigError
    if it's missing, the same "fail startup loudly" shape as a profile's required BOT_TOKEN_ENV/
    MODEL_CREDENTIAL_ENVS (profiles/loader.py's _resolve_secrets). All of these are read directly
    from the process environment, like BOT_SERVICE_KEY — no profile-level indirection.
    """

    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        return None

    session_secret = os.environ.get("ADMIN_SESSION_SECRET")
    if not session_secret:
        raise AdminConfigError(
            "ADMIN_USERNAME and ADMIN_PASSWORD are set, so the admin panel is enabled, but "
            "ADMIN_SESSION_SECRET is not — it's required to sign admin sessions. Generate one "
            'with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )

    return AdminConfig(
        username=username,
        password=password,
        session_secret=session_secret,
        session_timeout_minutes=_positive_int_env("ADMIN_SESSION_TIMEOUT_MINUTES", default=15),
        login_max_attempts=_positive_int_env("ADMIN_LOGIN_MAX_ATTEMPTS", default=5),
        login_lockout_minutes=_positive_int_env("ADMIN_LOGIN_LOCKOUT_MINUTES", default=15),
    )


class LoginRateLimiter:
    """In-memory, GLOBAL login lockout for the whole `/admin/login` endpoint — one shared failure
    count and one shared lockout state, deliberately not scoped per-IP or per-session.

    Per-IP scoping was tried first and diagnosed
    (docs/IMPROVES/ADMIN_LOGIN_LOCKOUT_DIAGNOSIS.MD): for this deployment's single shared
    credential, it behaved identically to one global lock in every situation that actually
    mattered — any two visitors sharing an observed source address (the common case behind a
    reverse proxy, a NAT'd office network, or just testing from localhost) already shared one
    lockout bucket. Global is now the intended design, not a bug to route around — do not
    reintroduce IP (or session) scoping here, even as a fallback.

    Reset on process restart, no shared store — fine for the single-process deployment this whole
    panel already assumes (SingleInstanceLock elsewhere in this codebase makes the same
    assumption).

    Storage is deliberately minimal: one failure count, and one lockout timestamp. There is no
    separate "locked" boolean and no separately-maintained "remaining time" value — whether the
    endpoint is currently locked out, and for how much longer, is always computed fresh from
    `_locked_at_monotonic` at the moment it's asked (`remaining_minutes`), never cached or updated
    on a timer. `time.monotonic()` is used only because that's what Python's clock returns
    seconds in; the lockout *duration* itself (`_lockout_minutes`) is defined in minutes from the
    start and never derived from a seconds-based constant.
    """

    def __init__(self, max_attempts: int, lockout_minutes: int):
        self._max_attempts = max_attempts
        self._lockout_minutes = lockout_minutes
        self._lock = threading.Lock()
        self._failure_count = 0
        self._locked_at_monotonic: float | None = None

    def _remaining_minutes_locked(self) -> float:
        """Caller must hold self._lock. May be negative once the lockout has expired."""

        elapsed_minutes = (time.monotonic() - self._locked_at_monotonic) / 60
        return self._lockout_minutes - elapsed_minutes

    def remaining_minutes(self) -> float:
        """Minutes remaining locked out right now, or 0.0 if not locked out — always a live
        computation from the one stored timestamp (see class docstring), never a stored value."""

        with self._lock:
            if self._locked_at_monotonic is None:
                return 0.0
            remaining = self._remaining_minutes_locked()
            if remaining <= 0:
                self._locked_at_monotonic = None
                self._failure_count = 0
                return 0.0
            return remaining

    def record_failure(self) -> float:
        """Record one failed attempt. Returns the minutes remaining locked out AFTER this
        failure — 0.0 if this failure did not trigger a lockout, a positive value if it did (or
        if the endpoint was already locked out), so the caller can tell whether THIS specific
        attempt is the one that just crossed the threshold and show the right message for it."""

        with self._lock:
            if self._locked_at_monotonic is not None:
                remaining = self._remaining_minutes_locked()
                if remaining > 0:
                    return remaining
                self._locked_at_monotonic = None
                self._failure_count = 0

            self._failure_count += 1
            if self._failure_count >= self._max_attempts:
                self._locked_at_monotonic = time.monotonic()
                self._failure_count = 0
                return self._lockout_minutes
            return 0.0

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._locked_at_monotonic = None


# Bootstrap ships a mirrored build for right-to-left pages; the template picks one by the
# catalog's language (`dir` below), so the Hebrew catalog gets a genuinely RTL layout rather
# than an LTR grid with Hebrew text poured into it.
_FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
)
_BOOTSTRAP_CSS_LINK = _FONTS_LINK + (
    '{% if dir == "rtl" %}'
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.rtl.min.css" rel="stylesheet">'
    "{% else %}"
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">'
    "{% endif %}"
)

# Shared LeadSpotting-style chrome for every authenticated admin page. Physical left/right
# properties are written as CSS logical properties so the same stylesheet lays out correctly
# under both `dir="ltr"` (sidebar on the start/left edge) and `dir="rtl"` (sidebar on the
# start/right edge).
_DASHBOARD_STYLE = """
<style>
  :root {
    --navy: #0B192C;
    --navy-soft: #15263d;
    --bg: #f5f7fb;
    --panel: #ffffff;
    --panel-muted: #f4f7fb;
    --line: #e2e8f0;
    --line-strong: #cbd5e1;
    --text: #0B192C;
    --text-dim: #5b6b80;
    --text-faint: #8b9bb0;
    --sidebar: #ffffff;
    --sidebar-text: #0B192C;
    --teal: #0f766e;
    --lime: #84cc16;
    --lime-hover: #65a30d;
    --lime-dim: #ecfccb;
    --lime-text: #0B192C;
    --blue: #2563eb;
    --blue-hover: #1d4ed8;
    --blue-dim: #dbeafe;
    --commander: #84cc16;
    --commander-dim: #ecfccb;
    --viewer: #2563eb;
    --viewer-dim: #dbeafe;
    --danger: #b91c1c;
    --danger-dim: #fee2e2;
    --warning: #b45309;
    --warning-dim: #fef3c7;
    --shadow: 0 1px 2px rgba(11, 25, 44, .04), 0 6px 18px rgba(11, 25, 44, .05);
    --shadow-lg: 0 8px 22px rgba(11, 25, 44, .09);
    --radius: 10px;
    --radius-sm: 6px;
    --control-h: 36px;
    --btn-h: 36px;
    --space-1: 4px;
    --space-2: 8px;
    --space-3: 12px;
    --space-4: 16px;
    --space-5: 20px;
    --sans: Heebo, Inter, -apple-system, 'Segoe UI', sans-serif;
    --mono: 'SF Mono', 'JetBrains Mono', ui-monospace, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14.5px;
    line-height: 1.45;
    font-weight: 400;
    margin: 0;
    min-height: 100vh;
  }
  :focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .ls-app { display: flex; min-height: 100vh; background: var(--bg); }
  .ls-sidebar {
    width: 236px;
    flex-shrink: 0;
    position: sticky;
    top: 0;
    height: 100vh;
    background: var(--sidebar);
    color: var(--sidebar-text);
    display: flex;
    flex-direction: column;
    padding: 14px 8px 10px;
    transition: width .2s ease;
    border-inline-end: 1px solid var(--line);
  }
  .ls-app.ls-sidebar-collapsed .ls-sidebar { width: 72px; padding-inline: 10px; }
  .ls-brand {
    display: flex; align-items: center; justify-content: center;
    gap: 8px; text-decoration: none; color: inherit;
    padding: 4px 8px 12px; min-height: 40px;
    border-bottom: 1px solid var(--line);
    margin-bottom: 8px;
  }
  .ls-logo {
    display: block; height: 28px; width: auto; max-width: 100%;
  }
  .ls-mark {
    display: none; width: 32px; height: 32px; object-fit: contain;
  }
  .ls-app.ls-sidebar-collapsed .ls-logo { display: none; }
  .ls-app.ls-sidebar-collapsed .ls-mark { display: block; }
  .ls-app.ls-sidebar-collapsed .ls-brand { border-bottom: 0; margin-bottom: 8px; padding-bottom: 8px; }
  .ls-nav { display: flex; flex-direction: column; gap: 2px; flex: 1; overflow: auto; padding-inline: 2px; }
  .ls-nav-group { margin-top: 8px; padding-top: 2px; }
  .ls-nav-group:first-child { margin-top: 0; }
  .ls-nav-group-label {
    display: block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--text-faint);
    padding: 4px 10px 3px;
  }
  .ls-app.ls-sidebar-collapsed .ls-nav-group-label {
    height: 1px; padding: 0; margin: 8px 10px 6px; color: transparent;
    background: var(--line); overflow: hidden;
  }
  .ls-nav-item {
    position: relative;
    display: flex; align-items: center; gap: 10px;
    color: var(--navy); text-decoration: none;
    padding: 7px 10px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 500;
    transition: background .15s ease, color .15s ease;
  }
  .ls-nav-item .ls-icon { color: var(--blue); }
  .ls-nav-item:hover { background: var(--blue-dim); color: var(--blue); }
  .ls-nav-item.is-active {
    background: var(--blue-dim);
    color: var(--blue);
  }
  .ls-nav-item.is-active::before {
    content: "";
    position: absolute;
    inset-inline-start: 0;
    top: 7px; bottom: 7px;
    width: 3px;
    border-radius: 999px;
    background: var(--lime);
  }
  .ls-nav-item.is-active .ls-icon { color: var(--blue); }
  .ls-icon { width: 18px; height: 18px; flex-shrink: 0; stroke: currentColor; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .ls-app.ls-sidebar-collapsed .ls-nav-label { display: none; }
  .ls-app.ls-sidebar-collapsed .ls-nav-item { justify-content: center; padding: 10px; }
  .ls-sidebar-toggle {
    margin-top: 8px; border: 0; background: var(--panel-muted); color: var(--navy);
    border-radius: var(--radius-sm); padding: 8px; cursor: pointer;
    transition: background .15s ease, color .15s ease;
  }
  .ls-sidebar-toggle:hover { background: var(--blue-dim); color: var(--blue); }
  .ls-app.ls-sidebar-collapsed .ls-sidebar-toggle .ls-icon { transform: rotate(180deg); }
  [dir="rtl"] .ls-sidebar-toggle .ls-icon { transform: scaleX(-1); }
  [dir="rtl"] .ls-app.ls-sidebar-collapsed .ls-sidebar-toggle .ls-icon { transform: scaleX(-1) rotate(180deg); }
  .ls-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .ls-topbar {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 8px 24px; min-height: 56px;
    background: var(--panel);
    border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 20;
  }
  .ls-topbar-end { margin-inline-start: auto; display: flex; align-items: center; gap: 10px; }
  .ls-btn-fill, .ls-btn-outline, .ls-user-btn,
  .btn-console, .btn-console-primary, .btn-console-danger {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    min-height: var(--btn-h); border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 600; letter-spacing: .01em; line-height: 1;
    text-decoration: none; border: 1.5px solid transparent; padding: 7px 14px;
    cursor: pointer; transition: background .15s ease, border-color .15s ease, color .15s ease, box-shadow .15s ease, filter .15s ease;
  }
  .ls-btn-fill { background: var(--navy); color: #fff; border-color: var(--navy); }
  .ls-btn-fill:hover { background: var(--navy-soft); color: #fff; box-shadow: 0 6px 14px rgba(11, 25, 44, .18); }
  .ls-btn-outline { background: #fff; color: var(--blue); border-color: var(--blue); font-weight: 600; }
  .ls-status-live {
    display: inline-flex; align-items: center; gap: 6px;
    min-height: 28px; padding: 4px 10px;
    border: 1px solid var(--line); border-radius: 999px;
    background: var(--panel-muted); color: var(--text-dim);
    font-size: 12px; font-weight: 600;
  }
  .ls-status-live .dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--lime); box-shadow: 0 0 0 3px rgba(132,204,22,.22);
  }
  .ls-user-btn {
    background: #fff; color: var(--text); border-color: var(--line-strong); font-weight: 600;
  }
  .ls-user-btn:hover { border-color: var(--blue); color: var(--blue); }
  .ls-content { flex: 1; padding: 16px 24px 32px; }
  .ls-page { max-width: 1080px; }
  .ls-page-wide { max-width: 1360px; }
  .container-narrow, .container-wide { max-width: none; padding: 0; }
  .ls-page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
  .ls-page-header h1, .ls-page > h1:first-child { font-size: 22px; font-weight: 600; letter-spacing: -.02em; color: var(--navy); margin: 0 0 4px; }
  .ls-page-header .subtitle, .ls-page > h1 + .subtitle { margin: 0; max-width: 680px; }
  .ls-section { margin-bottom: 16px; }
  .ls-section-title, .ls-home-group-title, .block-label {
    display: block;
    font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--text-faint); margin: 0 0 8px;
  }

  h1 { font-size: 22px; font-weight: 600; letter-spacing: -.02em; color: var(--navy); }
  h2 { font-weight: 600; color: var(--navy); font-size: 17px; }
  h3 { font-weight: 600; color: var(--navy); }
  .ls-content:has(.ls-home) { padding: 20px 28px 40px; }
  .ls-home { max-width: 1120px; }
  .ls-home .ls-page-header { margin-bottom: 20px; gap: 16px; }
  .ls-home-title { text-align: start; font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  .ls-home-sub { text-align: start; max-width: 680px; margin: 0; }
  .ls-home-group {
    margin-bottom: 26px; padding: 0;
    background: transparent; border: 0; border-radius: 0; box-shadow: none;
  }
  .ls-home-group-title {
    font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--blue); margin: 0 0 10px;
  }
  .ls-service-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
  .ls-tabs { display: flex; gap: 2px; flex-wrap: wrap; border-bottom: 1px solid var(--line); margin: 0 0 14px; }
  .ls-tab {
    background: transparent; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px;
    padding: 8px 12px; font-size: 13px; font-weight: 600; color: var(--text-dim); cursor: pointer;
    transition: color .15s ease, border-color .15s ease, background .15s ease;
  }
  .ls-tab:hover { color: var(--navy); background: var(--panel-muted); }
  .ls-tab.is-active { color: var(--blue); border-bottom-color: var(--blue); }
  .protocol-layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
  .ls-content:has(.ls-protocols) { padding: 20px 28px 40px; }
  .ls-protocols { max-width: 1400px; }
  .ls-protocols .ls-page-header { margin-bottom: 20px; gap: 16px; }
  .ls-protocols .ls-table-toolbar { margin-bottom: 16px; }
  .ls-protocols .ls-identity-block { margin-bottom: 16px; padding: 16px 18px; }
  .ls-protocols .protocol-layout { grid-template-columns: 240px minmax(0, 1fr); gap: 20px; }
  .ls-protocols .protocol-nav { padding: 12px; }
  .ls-protocols .protocol-nav-item { padding: 10px 12px; }
  .ls-protocols .protocol-section { margin-bottom: 16px; padding-bottom: 16px; }
  .ls-protocols .block-console { padding: 18px 18px 16px; }
  .ls-protocols .api-form-grid { gap: 12px; }
  .ls-protocols textarea.form-control-console { min-height: 88px; }
  .protocol-nav { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 8px; box-shadow: var(--shadow); position: sticky; top: 72px; }
  .protocol-nav-item {
    display: block; width: 100%; text-align: start; border: 0; background: transparent;
    padding: 8px 10px; border-radius: 8px; font-size: 13px; font-weight: 400; color: var(--text); cursor: pointer;
  }
  .protocol-nav-item:hover { background: var(--panel-muted); }
  .protocol-nav-item.is-active { background: var(--blue-dim); color: var(--blue); }
  .protocol-editor-block[hidden], [data-panel][hidden] { display: none !important; }
  .ls-status-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin-bottom: 12px; }
  .ls-status-chip {
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-sm);
    padding: 8px 10px; font-size: 12px; color: var(--text-dim); min-height: 56px;
    display: flex; flex-direction: column; justify-content: center; gap: 2px;
  }
  .ls-status-chip .ls-status-kicker { font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--text-faint); }
  .ls-status-chip strong { color: var(--text); font-weight: 600; }
  .ls-status-chip:first-child { border-inline-start: 3px solid var(--blue); }
  .ls-danger-zone { border-color: #fecaca; }
  .ls-compact-table td .form-control-console, .ls-compact-table td .form-select-console { min-height: 30px; padding: 3px 8px; font-size: 13px; }
  .ls-compact-table tbody td { padding: 5px 8px; }
  .ls-table-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
  .ls-count { font-size: 12px; font-weight: 600; color: var(--text-dim); }
  .ls-actions { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
  .ls-actions .btn { min-height: 28px; padding: 2px 8px; font-size: 12px; font-weight: 600; }
  .ls-empty-state { text-align: center; padding: 28px 18px; color: var(--text-dim); background: var(--panel); border: 1px dashed var(--line-strong); border-radius: var(--radius); }
  .ls-empty-icon { width: 32px; height: 32px; margin: 0 auto 8px; color: var(--blue); }
  .ls-empty-state strong { display: block; color: var(--navy); font-size: 15px; font-weight: 600; margin-bottom: 4px; }
  .ls-empty-state p { margin: 0 auto; max-width: 400px; font-size: 13px; }
  .ls-empty-state .btn { margin-top: 12px; }
  .ls-chip-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .tech, .tag.tech, .form-control-console.tech, .form-select-console.tech {
    direction: ltr; unicode-bidi: isolate; text-align: start; font-family: var(--mono);
  }
  .protocol-section { margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
  .protocol-section:last-child { border-bottom: 0; margin-bottom: 0; padding-bottom: 0; }
  .protocol-nav-create { color: var(--blue); }
  .sim-step.is-primary { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
  @media (max-width: 900px) { .protocol-layout { grid-template-columns: 1fr; } .protocol-nav { position: static; } }
  .ls-service-card {
    display: block; background: var(--panel); border-radius: var(--radius); padding: 18px 18px 16px;
    text-decoration: none; color: inherit; height: 100%;
    box-shadow: var(--shadow);
    border: 1px solid var(--line);
    transition: transform .25s ease, box-shadow .25s ease, border-color .25s ease;
  }
  .ls-service-card:hover, .ls-service-card:focus-visible {
    transform: scale(1.02);
    box-shadow: var(--shadow-lg);
    border-color: rgba(37, 99, 235, .35);
    color: inherit;
  }
  .ls-service-icon {
    width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center;
    background: var(--blue-dim); color: var(--blue); margin-bottom: 12px;
    transition: background .25s ease, color .25s ease;
  }
  .ls-service-card:hover .ls-service-icon, .ls-service-card:focus-visible .ls-service-icon {
    background: var(--blue); color: #fff;
  }
  .ls-service-card h2 { font-size: 15px; font-weight: 600; margin: 0 0 4px; color: var(--navy); }
  .ls-service-card .subtitle { font-size: 13px; display: block; color: var(--text-dim); }
  .ls-service-card.is-featured {
    background: var(--blue); border-color: var(--blue); color: #fff;
  }
  .ls-service-card.is-featured h2, .ls-service-card.is-featured .subtitle { color: #fff; }
  .ls-service-card.is-featured .subtitle { color: rgba(255,255,255,.82); }
  .ls-service-card.is-featured .ls-service-icon { background: rgba(255,255,255,.16); color: #fff; }
  .ls-service-card.is-featured:hover, .ls-service-card.is-featured:focus-visible {
    background: var(--blue-hover); border-color: var(--blue-hover); color: #fff;
  }
  .ls-service-card.is-featured:hover .ls-service-icon, .ls-service-card.is-featured:focus-visible .ls-service-icon {
    background: rgba(255,255,255,.22); color: #fff;
  }
  .ls-service-card.is-featured-navy { background: var(--navy); border-color: var(--navy); }
  .ls-service-card.is-featured-navy:hover, .ls-service-card.is-featured-navy:focus-visible {
    background: var(--navy-soft); border-color: var(--navy-soft);
  }
  @media (max-width: 980px) { .ls-service-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 640px) {
    .ls-service-grid { grid-template-columns: 1fr; }
    .ls-sidebar { position: sticky; top: 0; align-self: flex-start; max-height: 100vh; }
    .ls-content { padding: 12px 14px 28px; }
    .ls-topbar { padding-inline: 16px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .ls-service-card, .ls-nav-item, .ls-btn-fill, .ls-btn-outline, .ls-user-btn, .btn-console, .btn-console-primary, .btn-console-danger {
      transition: none;
    }
    .ls-service-card:hover, .ls-service-card:focus-visible { transform: none; }
  }

  .status-pill { font-size: 13px; color: var(--text-faint); }
  .status-pill .dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--lime); box-shadow: 0 0 0 3px rgba(132,204,22,.28); margin-inline-end: 6px;
  }
  .subtitle { color: var(--text-dim); font-size: 13.5px; line-height: 1.45; }
  .nav-console { font-size: 13px; color: var(--blue); text-decoration: none; font-weight: 600; }
  .nav-console:hover { color: var(--blue-hover); text-decoration: underline; }

  .alert-console {
    background: var(--commander-dim);
    border: 1px solid #bef264;
    border-inline-start: 3px solid var(--lime);
    border-radius: var(--radius-sm);
    color: #3f6212;
    font-size: 14px;
  }
  .alert-console b { font-weight: 600; }
  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #fca5a5;
    border-inline-start: 3px solid var(--danger);
    border-radius: var(--radius-sm);
    color: #7f1d1d;
    font-size: 14px;
  }
  .alert-console-error b { font-weight: 600; }

  .ls-page > table.table-console,
  .ls-table-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 0;
    overflow: auto;
  }
  .table-responsive { overflow: auto; }
  table.table-console {
    --bs-table-bg: transparent;
    border-collapse: collapse;
    font-size: 14px;
    margin-bottom: 0;
  }
  table.table-console thead th {
    font-size: 11px;
    font-weight: 700;
    color: var(--text-faint);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    background: var(--panel-muted);
    border-bottom: 1px solid var(--line) !important;
    border-top: none;
    padding: 8px 12px;
    white-space: nowrap;
  }
  table.table-console tbody td {
    border-color: var(--line);
    vertical-align: middle;
    padding: 8px 12px;
    font-size: 13px;
  }
  table.table-console tbody tr:nth-child(even) { background: #f7f9fc; }
  table.table-console tbody tr:hover { background: #eef4ff; }
  table.table-console tbody td:first-child,
  table.table-console thead th:first-child { padding-inline-start: 16px; }
  table.table-console .ls-empty,
  table.table-console td[colspan] {
    color: var(--text-dim);
    text-align: center;
    padding: 28px 16px;
    background: var(--panel-muted);
  }

  .identity {
    font-family: var(--mono);
    font-size: 12.5px;
    direction: ltr;
    unicode-bidi: isolate;
  }
  .identity-id {
    display: inline-block;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    vertical-align: bottom;
  }
  .identity .tag { font-family: var(--sans); font-size: 11px; color: var(--text-faint); margin-inline-start: 8px; }
  .tag {
    display: inline-block; font-size: 11px; font-weight: 600; color: var(--text-dim);
    background: #f1f5f9; border-radius: 999px; padding: 2px 8px; line-height: 1.4;
  }

  .level-dot {
    display: inline-block;
    width: 5px; height: 5px;
    border-radius: 50%;
    margin-inline-end: 6px;
  }
  .badge-commander { color: var(--commander); }
  .badge-commander .level-dot { background: var(--commander); }
  .badge-viewer { color: var(--viewer); }
  .badge-viewer .level-dot { background: var(--viewer); }
  .level-label { font-family: var(--mono); font-size: 13px; }

  .form-select-console, .form-control-console {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    color: var(--text);
    font-size: 13.5px;
    min-height: var(--control-h);
    padding: 6px 10px;
  }
  textarea.form-control-console { min-height: 76px; padding: 8px 10px; }
  .form-select-console:focus, .form-control-console:focus {
    border-color: var(--blue);
    box-shadow: 0 0 0 0.2rem rgba(37, 99, 235, 0.16);
  }
  .form-select-console:disabled, .form-control-console:disabled,
  .form-control-console[readonly] {
    background: var(--panel-muted);
    color: var(--text-dim);
  }

  .btn-console {
    background: #fff;
    color: var(--blue);
    border-color: var(--blue);
  }
  .btn-console:hover { border-color: var(--blue-hover); color: var(--blue-hover); background: var(--blue-dim); }
  .btn-console-danger { color: var(--danger); border-color: #fca5a5; background: #fff; }
  .btn-console-danger:hover { border-color: var(--danger); color: var(--danger); background: var(--danger-dim); }
  .btn-console-primary {
    background: var(--lime); border-color: var(--lime); color: var(--lime-text); font-weight: 700;
  }
  .btn-console-primary:hover { background: var(--lime-hover); border-color: var(--lime-hover); color: var(--lime-text); filter: brightness(1.02); box-shadow: 0 6px 14px rgba(132, 204, 22, .28); }
  .btn-console:disabled, .btn-console-primary:disabled, .btn-console-danger:disabled,
  .ls-btn-fill:disabled, .ls-user-btn:disabled {
    opacity: .55; cursor: not-allowed; box-shadow: none; filter: none;
  }
  .btn-sm.btn-console, .btn-sm.btn-console-primary, .btn-sm.btn-console-danger {
    min-height: 28px; padding: 3px 9px; font-size: 12px; font-weight: 600;
  }

  .block-console {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 14px 16px 14px;
    position: relative;
    background: var(--panel);
    box-shadow: var(--shadow);
  }
  .ls-section > .ls-section-title + .block-console > .block-label:first-child { display: none; }
  .ls-identity-block { margin-bottom: 12px; }
  .ls-create-panel { background: var(--panel-muted); box-shadow: none; }
  .form-label-console {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-dim);
    letter-spacing: 0.01em;
    margin-bottom: 4px;
  }
  code.console-code {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--viewer);
    background: var(--viewer-dim);
    padding: 1px 5px;
    border-radius: 3px;
    direction: ltr;
    unicode-bidi: isolate;
  }
</style>
"""

_LOGIN_STYLE = """
<style>
  :root {
    --bg: #f8fafc;
    --text: #0B192C;
    --text-dim: #475569;
    --text-faint: #94a3b8;
    --lime: #84cc16;
    --lime-hover: #65a30d;
    --blue: #2563eb;
    --danger: #b91c1c;
    --danger-dim: #fee2e2;
    --sans: Heebo, Inter, -apple-system, 'Segoe UI', sans-serif;
  }
  body.ls-login {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 16px;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    margin: 0;
  }
  .login-brand {
    margin-bottom: 28px;
    text-align: center;
    background: #0B192C;
    border-radius: 16px;
    padding: 18px 28px;
    box-shadow: 0 12px 32px rgba(11, 25, 44, .18);
  }
  .login-brand .ls-logo { display: block; height: 44px; width: auto; max-width: 280px; margin: 0 auto; }
  .login-card {
    width: 100%;
    max-width: 420px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 32px 28px 28px;
    box-shadow: 0 1px 2px rgba(11, 25, 44, .04), 0 8px 24px rgba(11, 25, 44, .06);
  }
  .login-card h1 {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 8px;
    text-align: center;
  }
  .login-card .subtitle {
    font-size: 13px;
    color: var(--text-faint);
    margin-bottom: 24px;
    text-align: center;
  }
  .form-label-console {
    font-size: 12px;
    color: var(--text-faint);
    letter-spacing: 0.02em;
    margin-bottom: 4px;
    display: block;
  }
  .form-control-console, .form-select-console {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    color: var(--text);
    font-size: 15px;
    width: 100%;
    min-height: 44px;
    padding: 12px 14px;
  }
  .form-control-console::placeholder { color: #94a3b8; }
  .form-control-console:focus {
    outline: none;
    border-color: var(--blue);
    box-shadow: 0 0 0 0.2rem rgba(37, 99, 235, 0.18);
    background: #fff;
  }
  .field-group { margin-bottom: 14px; }

  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #fca5a5;
    border-inline-start: 3px solid var(--danger);
    border-radius: 12px;
    color: #7f1d1d;
    font-size: 13px;
    padding: 10px 14px;
    margin-bottom: 20px;
  }
  .lockout-progress-track {
    margin-top: 10px;
    height: 6px;
    border-radius: 3px;
    background: rgba(185, 28, 28, 0.18);
    overflow: hidden;
  }
  .lockout-progress-fill {
    height: 100%;
    border-radius: 3px;
    background: var(--danger);
  }

  .login-actions { text-align: center; margin-top: 8px; }
  .btn-console-primary {
    background: var(--lime);
    border: 0;
    color: #0B192C;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: .02em;
    border-radius: 8px;
    padding: 12px 32px;
    min-width: 180px;
    min-height: 44px;
    transition: background .2s ease, box-shadow .2s ease;
  }
  .btn-console-primary:hover { background: var(--lime-hover); color: #0B192C; box-shadow: 0 8px 18px rgba(132, 204, 22, .28); }

  .status-pill {
    font-size: 12px;
    color: var(--text-faint);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-top: 22px;
  }
  .status-pill .dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--lime);
    box-shadow: 0 0 0 3px rgba(132,204,22,.28);
    margin-inline-end: 6px;
  }
</style>
"""

_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.login_title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _LOGIN_STYLE + """
</head>
<body class="ls-login">

  <div class="login-brand">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="LeadSpotting">
  </div>
  <div class="login-card">
    <h1>{{ t('admin.login_title') }}</h1>
    <p class="subtitle">{{ t('admin.login_subtitle') }}</p>

    {% if lockout %}
      <div class="alert-console-error">
        {{ lockout.message }}
        <div class="lockout-progress-track" role="progressbar"
             aria-valuenow="{{ lockout.percent_elapsed }}" aria-valuemin="0" aria-valuemax="100"
             aria-label="Lockout time elapsed">
          <div class="lockout-progress-fill" style="width: {{ lockout.percent_elapsed }}%;"></div>
        </div>
      </div>
    {% else %}
      {% for category, message in get_flashed_messages(with_categories=true) %}
        <div class="alert-console-error">{{ message }}</div>
      {% endfor %}
    {% endif %}

    <form id="loginForm" method="post">
      <div class="field-group">
        <label class="form-label-console visually-hidden" for="username">{{ t('admin.username') }}</label>
        <input type="text" class="form-control-console" id="username" name="username" placeholder="{{ t('admin.username') }}" autofocus required>
      </div>
      <div class="field-group">
        <label class="form-label-console visually-hidden" for="password">{{ t('admin.password') }}</label>
        <input type="password" class="form-control-console" id="password" name="password" placeholder="{{ t('admin.password') }}" required>
      </div>
      <div class="login-actions">
        <button type="submit" class="btn-console-primary">{{ t('admin.sign_in') }}</button>
      </div>
    </form>

    <div class="status-pill"><span class="dot"></span>{{ t('admin.connected') }}</div>
  </div>

</body>
</html>
"""

_ICON_HOME = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>'
_ICON_PROFILES = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h16"/><path d="M4 12h16"/><path d="M4 16h16"/><path d="M8 6v4"/><path d="M12 10v4"/><path d="M16 14v4"/></svg>'
_ICON_PROTOCOLS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor" stroke="none"/></svg>'
_ICON_EVENTS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/></svg>'
_ICON_USERS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 2.8-4.5 5.5-4.5S13.7 16 14.5 19"/><circle cx="17" cy="9" r="2.4"/><path d="M16.2 14.6c2.2.3 3.8 1.6 4.3 4.4"/></svg>'
_ICON_GROUPS = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="9" r="2.5"/><circle cx="16" cy="9" r="2.5"/><circle cx="12" cy="8" r="2.7"/><path d="M4 19c.7-2.6 2.4-4 5-4"/><path d="M20 19c-.7-2.6-2.4-4-5-4"/><path d="M8.5 19c.7-2.4 2-3.6 3.5-3.6s2.8 1.2 3.5 3.6"/></svg>'
_ICON_SIMULATOR = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z" fill="currentColor" stroke="none"/></svg>'
_ICON_SERVER = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3 3.2 4.5 6.2 4.5 9S15 17.8 12 21"/><path d="M12 3c-3 3.2-4.5 6.2-4.5 9S9 17.8 12 21"/></svg>'
_ICON_TABLE = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M3 14h18"/><path d="M9 9v11"/><path d="M15 9v11"/></svg>'
_ICON_TOGGLE = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>'
_ICON_PERSON = '<svg class="ls-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c1-3.4 3.4-5 7-5s6 1.6 7 5"/></svg>'

_SHELL_SCRIPT = """
<script>
(function () {
  const root = document.querySelector('.ls-app');
  if (!root) return;
  const key = 'admin-sidebar-collapsed';
  if (window.localStorage.getItem(key) === '1') root.classList.add('ls-sidebar-collapsed');
  const toggle = document.getElementById('ls-sidebar-toggle');
  if (toggle) toggle.addEventListener('click', function () {
    root.classList.toggle('ls-sidebar-collapsed');
    window.localStorage.setItem(key, root.classList.contains('ls-sidebar-collapsed') ? '1' : '0');
  });
  document.querySelectorAll('[data-ls-tabs]').forEach(function (group) {
    const buttons = Array.from(group.querySelectorAll('[data-tab]'));
    const scope = group.closest('.ls-page, .ls-page-wide') || document;
    function show(name) {
      buttons.forEach(function (button) { button.classList.toggle('is-active', button.dataset.tab === name); });
      scope.querySelectorAll('[data-panel]').forEach(function (panel) {
        if (panel.closest('[data-ls-tabs]') && panel.closest('[data-ls-tabs]') !== group) return;
        panel.hidden = panel.dataset.panel !== name;
      });
    }
    buttons.forEach(function (button) { button.addEventListener('click', function () { show(button.dataset.tab); }); });
    const initial = group.getAttribute('data-initial') || (buttons[0] && buttons[0].dataset.tab);
    if (initial) show(initial);
  });
  const protocolNav = document.querySelector('[data-protocol-nav]');
  if (protocolNav) {
    const items = Array.from(protocolNav.querySelectorAll('[data-protocol-target]'));
    function showProtocol(id) {
      items.forEach(function (item) { item.classList.toggle('is-active', item.getAttribute('data-protocol-target') === id); });
      document.querySelectorAll('.protocol-editor-block').forEach(function (block) { block.hidden = block.id !== id; });
    }
    items.forEach(function (item) {
      item.addEventListener('click', function () { showProtocol(item.getAttribute('data-protocol-target')); });
    });
    if (items[0]) showProtocol(items[0].getAttribute('data-protocol-target'));
  }
  document.querySelectorAll('[data-tab-goto]').forEach(function (button) {
    button.addEventListener('click', function () {
      const target = document.querySelector('[data-tab="' + button.getAttribute('data-tab-goto') + '"]');
      if (target) target.click();
    });
  });
})();
</script>
"""

_SHELL_OPEN = """
<body class="ls-app"{% if api_identity is defined %} data-api-identity="{{ api_identity }}"{% endif %}>
<aside class="ls-sidebar">
  <a class="ls-brand" href="{{ url_for('admin.dashboard') }}" aria-label="LeadSpotting">
    <img class="ls-mark" src="{{ url_for('static', filename='leadspotting-mark.gif') }}" alt="">
    <img class="ls-logo" src="{{ url_for('static', filename='leadspotting-logo.gif') }}" alt="">
  </a>
    <nav class="ls-nav" aria-label="{{ t('admin.menu_title') }}">
    <div class="ls-nav-group">
      <a class="ls-nav-item{% if request.endpoint == 'admin.dashboard' %} is-active{% endif %}" href="{{ url_for('admin.dashboard') }}">""" + _ICON_HOME + """<span class="ls-nav-label">{{ t('admin.menu_all') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_management') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.users' %} is-active{% endif %}" href="{{ url_for('admin.users') }}">""" + _ICON_USERS + """<span class="ls-nav-label">{{ t('admin.menu_users') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.groups' %} is-active{% endif %}" href="{{ url_for('admin.groups') }}">""" + _ICON_GROUPS + """<span class="ls-nav-label">{{ t('admin.menu_groups') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_operations') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.profiles' %} is-active{% endif %}" href="{{ url_for('admin.profiles') }}">""" + _ICON_PROFILES + """<span class="ls-nav-label">{{ t('admin.menu_profiles') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.protocols' %} is-active{% endif %}" href="{{ url_for('admin.protocols') }}">""" + _ICON_PROTOCOLS + """<span class="ls-nav-label">{{ t('admin.menu_protocols') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.events' %} is-active{% endif %}" href="{{ url_for('admin.events') }}">""" + _ICON_EVENTS + """<span class="ls-nav-label">{{ t('admin.menu_events') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_system') }}</span>
      <a class="ls-nav-item{% if request.endpoint == 'admin.server' %} is-active{% endif %}" href="{{ url_for('admin.server') }}">""" + _ICON_SERVER + """<span class="ls-nav-label">{{ t('admin.menu_server') }}</span></a>
      <a class="ls-nav-item{% if request.endpoint == 'admin.simulator' %} is-active{% endif %}" href="{{ url_for('admin.simulator') }}">""" + _ICON_SIMULATOR + """<span class="ls-nav-label">{{ t('admin.menu_simulator') }}</span></a>
    </div>
    <div class="ls-nav-group">
      <span class="ls-nav-group-label">{{ t('admin.nav_group_data') }}</span>
      {% for table in admin_tables|default([]) %}
      <a class="ls-nav-item{% if request.view_args and request.view_args.get('table_key') == table.key %} is-active{% endif %}" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}">""" + _ICON_TABLE + """<span class="ls-nav-label">{{ table.label }}</span></a>
      {% endfor %}
    </div>
  </nav>
  <button type="button" class="ls-sidebar-toggle" id="ls-sidebar-toggle" aria-label="{{ t('admin.menu_title') }}">""" + _ICON_TOGGLE + """</button>
</aside>
<div class="ls-main">
  <header class="ls-topbar">
    <a class="ls-btn-fill" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a>
    <span class="ls-status-live"><span class="dot"></span>{{ t('admin.connected') }}</span>
    <div class="ls-topbar-end">
      {% if csrf_token %}
      <form method="post" action="{{ url_for('admin.logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="ls-user-btn">""" + _ICON_PERSON + """<span>{{ t('admin.log_out') }}</span></button>
      </form>
      {% endif %}
    </div>
  </header>
  <div class="ls-content">
"""

_SHELL_CLOSE = """
  </div>
</div>
""" + _SHELL_SCRIPT + """
</body></html>
"""

_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.dashboard_title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>
<body>
<div class="container container-narrow">

  <div class="d-flex justify-content-between align-items-baseline mb-1">
    <h1 class="mb-0">{{ t('admin.dashboard_title') }}</h1>
    <div class="d-flex align-items-center gap-3">
      <a class="nav-console" href="{{ url_for('admin.simulator') }}">{{ t('admin.nav_simulator') }}</a>
      <span class="status-pill"><span class="dot"></span>{{ t('admin.connected') }}</span>
      <form method="post" action="{{ url_for('admin.logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="btn btn-console-danger btn-sm">{{ t('admin.log_out') }}</button>
      </form>
    </div>
  </div>
  <p class="subtitle mb-4">{{ t('admin.dashboard_subtitle') }}</p>

  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}

  <table class="table table-console mb-5">
    <thead>
      <tr>
        <th>{{ t('admin.col_identity') }}</th>
        <th>{{ t('admin.col_level') }}</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for user in users %}
      <tr>
        <td class="identity">{{ user.telegram_identity }}{% if user.telegram_identity == bot_service_identity %} <span class="tag">{{ t('admin.tag_bot_service') }}</span>{% endif %}</td>
        <td>
          <form class="d-flex gap-2" method="post" action="{{ url_for('admin.write_user') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <input type="hidden" name="telegram_identity" value="{{ user.telegram_identity }}">
            <select name="permission_level" class="form-select form-select-console form-select-sm w-auto">
              {% for level in levels %}
              <option value="{{ level }}" {% if level == user.permission_level %}selected{% endif %}>{{ level }}</option>
              {% endfor %}
            </select>
            <button type="submit" class="btn btn-console btn-sm">{{ t('admin.save') }}</button>
          </form>
        </td>
        <td class="text-end">
          <form method="post" action="{{ url_for('admin.remove_user', identity=user.telegram_identity) }}"
                onsubmit="return confirm({{ t('admin.confirm_remove_user', identity=user.telegram_identity)|tojson|forceescape }});">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit" class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="block-console mb-4">
    <span class="block-label">{{ t('admin.add_user') }}</span>
    <form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_user') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <div class="col">
        <div class="form-label-console">{{ t('admin.col_identity') }}</div>
        <input type="text" name="telegram_identity" class="form-control form-control-console" placeholder="123456789" required>
      </div>
      <div class="col-auto">
        <div class="form-label-console">{{ t('admin.col_level') }}</div>
        <select name="permission_level" class="form-select form-select-console">
          {% for level in levels %}<option value="{{ level }}">{{ level }}</option>{% endfor %}
        </select>
      </div>
      <div class="col-auto">
        <button type="submit" class="btn btn-console-primary">{{ t('admin.add') }}</button>
      </div>
    </form>
  </div>

  <h2 class="mb-1" style="font-size:18px;">{{ t('admin.groups_title') }}</h2>
  <p class="subtitle mb-3">{{ t('admin.groups_subtitle') }}</p>

  <table class="table table-console mb-4">
    <thead>
      <tr>
        <th>{{ t('admin.col_chat_id') }}</th>
        <th>{{ t('admin.col_label') }}</th>
        <th>{{ t('admin.col_routed_to') }}</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for group in groups %}
      <tr>
        <td class="identity">{{ group.chat_id }}</td>
        <td>{{ group.label }}</td>
        <td>
          <form class="d-flex gap-2" method="post" action="{{ url_for('admin.write_group') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <input type="hidden" name="chat_id" value="{{ group.chat_id }}">
            <input type="hidden" name="label" value="{{ group.label }}">
            <select name="agent_name" class="form-select form-select-console form-select-sm w-auto">
              {% for agent_name in routable_agents %}
              <option value="{{ agent_name }}" {% if agent_name == group.agent_name %}selected{% endif %}>{{ agent_name }}</option>
              {% endfor %}
            </select>
            <button type="submit" class="btn btn-console btn-sm">{{ t('admin.save') }}</button>
          </form>
        </td>
        <td class="text-end">
          <form method="post" action="{{ url_for('admin.remove_group', chat_id=group.chat_id) }}"
                onsubmit="return confirm({{ t('admin.confirm_remove_group', chat_id=group.chat_id)|tojson|forceescape }});">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit" class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" style="color:var(--text-dim);">{{ t('admin.no_groups') }}</td></tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="block-console mb-5">
    <span class="block-label">{{ t('admin.add_group') }}</span>
    <p class="mb-3" style="font-size:13px; color:var(--text-dim); max-width:560px; line-height:1.6;">
      {{ t('admin.add_group_help', main_agent='main_agent') }}
    </p>
    <form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_group') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <div class="col">
        <div class="form-label-console">{{ t('admin.col_chat_id') }}</div>
        <input type="text" name="chat_id" class="form-control form-control-console" placeholder="-1001234567890" required>
      </div>
      <div class="col">
        <div class="form-label-console">{{ t('admin.col_label') }}</div>
        <input type="text" name="label" class="form-control form-control-console" placeholder="{{ t('admin.label_placeholder') }}" maxlength="200">
      </div>
      <div class="col-auto">
        <div class="form-label-console">{{ t('admin.col_routed_to') }}</div>
        <select name="agent_name" class="form-select form-select-console">
          {% for agent_name in routable_agents %}<option value="{{ agent_name }}">{{ agent_name }}</option>{% endfor %}
        </select>
      </div>
      <div class="col-auto">
        <button type="submit" class="btn btn-console-primary">{{ t('admin.add') }}</button>
      </div>
    </form>
  </div>

  <div class="block-console mb-4">
    <span class="block-label">{{ t('admin.bot_service_title') }}</span>
    <p class="mb-3" style="font-size:13px; color:var(--text-dim); max-width:560px; line-height:1.6;">
      {{ t('admin.bot_service_help', identity=bot_service_identity) }}
    </p>
    <form method="post" action="{{ url_for('admin.provision_bot_service') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button type="submit" class="btn btn-console">{{ t('admin.bot_service_button') }}</button>
    </form>
  </div>

</div>
</body>
</html>
"""


_MENU_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.menu_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>""" + _SHELL_OPEN + """
<div class="ls-page ls-home">
  <div class="ls-page-header">
    <div>
      <h1 class="ls-home-title">{{ t('admin.menu_title') }}</h1>
      <p class="subtitle ls-home-sub">{{ t('admin.menu_subtitle') }}</p>
    </div>
  </div>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_configuration') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card" href="{{ url_for('admin.users') }}"><span class="ls-service-icon">""" + _ICON_USERS + """</span><h2>{{ t('admin.menu_users') }}</h2><span class="subtitle">{{ t('admin.users_subtitle') }}</span></a>
      <a class="ls-service-card" href="{{ url_for('admin.groups') }}"><span class="ls-service-icon">""" + _ICON_GROUPS + """</span><h2>{{ t('admin.menu_groups') }}</h2><span class="subtitle">{{ t('admin.groups_page_subtitle') }}</span></a>
    </div>
  </section>
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_operations') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card" href="{{ url_for('admin.profiles') }}"><span class="ls-service-icon">""" + _ICON_PROFILES + """</span><h2>{{ t('admin.menu_profiles') }}</h2><span class="subtitle">{{ t('admin.profiles.subtitle') }}</span></a>
      <a class="ls-service-card" href="{{ url_for('admin.protocols') }}"><span class="ls-service-icon">""" + _ICON_PROTOCOLS + """</span><h2>{{ t('admin.menu_protocols') }}</h2><span class="subtitle">{{ t('admin.protocols.subtitle') }}</span></a>
      <a class="ls-service-card is-featured" href="{{ url_for('admin.events') }}"><span class="ls-service-icon">""" + _ICON_EVENTS + """</span><h2>{{ t('admin.menu_events') }}</h2><span class="subtitle">{{ t('admin.events.subtitle') }}</span></a>
    </div>
  </section>
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_system') }}</h2>
    <div class="ls-service-grid">
      <a class="ls-service-card is-featured is-featured-navy" href="{{ url_for('admin.server') }}"><span class="ls-service-icon">""" + _ICON_SERVER + """</span><h2>{{ t('admin.menu_server') }}</h2><span class="subtitle">{{ t('admin.server_subtitle') }}</span></a>
      <a class="ls-service-card is-featured" href="{{ url_for('admin.simulator') }}"><span class="ls-service-icon">""" + _ICON_SIMULATOR + """</span><h2>{{ t('admin.menu_simulator') }}</h2><span class="subtitle">{{ t('admin.simulator.subtitle') }}</span></a>
    </div>
  </section>
  {% if admin_tables %}
  <section class="ls-home-group">
    <h2 class="ls-home-group-title">{{ t('admin.home_group_data') }}</h2>
    <div class="ls-service-grid">
      {% for table in admin_tables %}
      <a class="ls-service-card" href="{{ url_for('admin.admin_table_list', table_key=table.key) }}"><span class="ls-service-icon">""" + _ICON_TABLE + """</span><h2>{{ table.label }}</h2><span class="subtitle">{{ t('admin.tables.menu_subtitle') }}</span></a>
      {% endfor %}
    </div>
  </section>
  {% endif %}
</div>
""" + _SHELL_CLOSE


_USERS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.users_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>""" + _SHELL_OPEN + """
<div class="ls-page-wide">
  <div class="ls-page-header"><div>
  <h1>{{ t('admin.users_title') }}</h1>
  <p class="subtitle">{{ t('admin.users_subtitle') }}</p>
  </div></div>
  {% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
  <section class="ls-section">
    <div class="ls-table-toolbar"><h2 class="ls-section-title mb-0">{{ t('admin.users.list_heading') }}</h2><span class="ls-count">{{ t('admin.tables.record_count', count=users|length) }}</span></div>
    <div class="table-responsive ls-table-card"><table class="table table-console ls-compact-table mb-0"><thead><tr><th>{{ t('admin.col_identity') }}</th><th>{{ t('admin.col_full_name') }}</th><th>{{ t('admin.col_level') }}</th><th>{{ t('admin.col_status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
    {% for user in users %}
    <tr>
      <td class="identity" title="{{ user.telegram_identity }}"><span class="identity-id">{{ user.telegram_identity }}</span>{% if user.telegram_identity == bot_service_identity %} <span class="tag">{{ t('admin.tag_bot_service') }}</span>{% endif %}</td>
      <td><form id="user-save-{{ loop.index }}" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="telegram_identity" value="{{ user.telegram_identity }}"><input type="text" name="full_name" value="{{ user.full_name }}" class="form-control form-control-console" maxlength="120" placeholder="{{ t('admin.col_full_name') }}"></form></td>
      <td><select name="permission_level" form="user-save-{{ loop.index }}" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}" {% if level == user.permission_level %}selected{% endif %}>{{ level }}</option>{% endfor %}</select></td>
      <td><span class="tag">{% if user.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and user.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></td>
      <td><div class="ls-actions"><button class="btn btn-console btn-sm" form="user-save-{{ loop.index }}">{{ t('admin.save') }}</button>{% if user.auto_register %}<form method="post" action="{{ url_for('admin.approve_user', identity=user.telegram_identity) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}<form method="post" action="{{ url_for('admin.remove_user', identity=user.telegram_identity) }}" onsubmit="return confirm({{ t('admin.confirm_remove_user', identity=user.telegram_identity)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form></div></td>
    </tr>
    {% endfor %}
    </tbody></table></div>
  </section>
  <section class="ls-section"><div class="block-console ls-create-panel"><span class="block-label">{{ t('admin.add_user') }}</span><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="col"><div class="form-label-console">{{ t('admin.col_identity') }}</div><input name="telegram_identity" class="form-control form-control-console" required></div>
    <div class="col"><div class="form-label-console">{{ t('admin.col_full_name') }}</div><input name="full_name" class="form-control form-control-console" maxlength="120"></div>
    <div class="col-auto"><div class="form-label-console">{{ t('admin.col_level') }}</div><select name="permission_level" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}">{{ level }}</option>{% endfor %}</select></div>
    <div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div></section>
  <section class="ls-section"><div class="block-console"><span class="block-label">{{ t('admin.bot_service_title') }}</span><p class="subtitle">{{ t('admin.bot_service_help', identity=bot_service_identity) }}</p><form method="post" action="{{ url_for('admin.provision_bot_service') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console">{{ t('admin.bot_service_button') }}</button></form></div></section>
</div>
""" + _SHELL_CLOSE


_GROUPS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.groups_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>
""" + _SHELL_OPEN + """
<div class="ls-page-wide"><div class="ls-page-header"><div><h1>{{ t('admin.groups_title') }}</h1><p class="subtitle">{{ t('admin.groups_page_subtitle') }}</p></div></div>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
<section class="ls-section">
  <div class="ls-table-toolbar"><h2 class="ls-section-title mb-0">{{ t('admin.groups.list_heading') }}</h2><span class="ls-count">{{ t('admin.tables.record_count', count=groups|length) }}</span></div>
  <div class="table-responsive ls-table-card"><table class="table table-console ls-compact-table mb-0"><thead><tr><th>{{ t('admin.col_chat_id') }}</th><th>{{ t('admin.col_label') }}</th><th>{{ t('admin.col_routed_to') }}</th><th>{{ t('admin.col_status') }}</th><th>{{ t('admin.col_actions') }}</th></tr></thead><tbody>
  {% for group in groups %}
  <tr>
    <td class="identity" title="{{ group.chat_id }}"><span class="identity-id">{{ group.chat_id }}</span></td>
    <td><form id="group-save-{{ loop.index }}" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="chat_id" value="{{ group.chat_id }}"><input name="label" value="{{ group.label }}" maxlength="200" class="form-control form-control-console" placeholder="{{ t('admin.col_label') }}"></form></td>
    <td><select name="agent_name" form="group-save-{{ loop.index }}" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}" {% if agent_name == group.agent_name %}selected{% endif %}>{{ agent_name }}</option>{% endfor %}</select></td>
    <td><span class="tag">{% if group.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and group.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></td>
    <td>
      <div class="ls-actions">
        <button class="btn btn-console btn-sm" form="group-save-{{ loop.index }}">{{ t('admin.save') }}</button>
        {% if group.auto_register %}<form method="post" action="{{ url_for('admin.approve_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}
        <form method="post" action="{{ url_for('admin.remove_group', chat_id=group.chat_id) }}" onsubmit="return confirm({{ t('admin.confirm_remove_group', chat_id=group.chat_id)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form>
      </div>
      <form class="d-flex gap-2 mt-2" method="post" action="{{ url_for('admin.rename_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input name="new_chat_id" class="form-control form-control-console form-control-sm" placeholder="{{ t('admin.new_chat_id_placeholder') }}" title="{{ t('admin.group_rename_help') }}"><button class="btn btn-console btn-sm" title="{{ t('admin.group_rename_help') }}">{{ t('admin.rename_group') }}</button></form>
    </td>
  </tr>
  {% else %}<tr><td class="ls-empty" colspan="5">{{ t('admin.no_groups') }}</td></tr>{% endfor %}
  </tbody></table></div>
</section>
<section class="ls-section"><div class="block-console ls-create-panel"><span class="block-label">{{ t('admin.add_group') }}</span><p class="subtitle">{{ t('admin.add_group_help', main_agent='main_agent') }}</p><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><div class="form-label-console">{{ t('admin.col_chat_id') }}</div><input name="chat_id" class="form-control form-control-console" placeholder="-1001234567890" required></div><div class="col"><div class="form-label-console">{{ t('admin.col_label') }}</div><input name="label" class="form-control form-control-console" maxlength="200"></div><div class="col-auto"><select name="agent_name" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}">{{ agent_name }}</option>{% endfor %}</select></div><div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div></section>
</div>
""" + _SHELL_CLOSE


_PROFILES_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.profiles.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + PROFILES_BODY + _SHELL_CLOSE


_PROTOCOLS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.protocols.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + PROTOCOLS_BODY + _SHELL_CLOSE


_ADMIN_TABLES_LIST_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ table.label }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + ADMIN_TABLES_LIST_BODY + _SHELL_CLOSE


_ADMIN_TABLES_EDIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ table.label }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + ADMIN_TABLES_EDIT_BODY + _SHELL_CLOSE


_EVENTS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.events.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + _SHELL_OPEN + EVENTS_BODY + _SHELL_CLOSE


_SERVER_STYLE = """
<style>
  .ls-content:has(.ls-server) { padding: 20px 28px 40px; }
  .ls-server { max-width: 1080px; }
  .ls-server .ls-page-header { margin-bottom: 20px; }
  .ls-server .ls-section { margin-bottom: 28px; }
  .ls-server .block-console { padding: 22px 24px; }
  .ls-server .ls-identity-block { padding: 16px 18px; margin-bottom: 20px; }
  .mode-panel {
    position: relative;
    overflow: hidden;
    border: 1px solid var(--line);
    border-inline-start: 3px solid var(--blue);
    background: var(--panel);
    padding: 28px 28px 24px;
  }
  .mode-panel.open-active { background: var(--panel); }
  .mode-panel.safe-active { background: var(--panel); border-inline-start-color: var(--blue); }
  .mode-layout { display: grid; grid-template-columns: minmax(150px, .62fr) minmax(280px, 1.38fr); gap: 28px; align-items: center; }
  .mode-visual { display: flex; align-items: center; justify-content: center; min-height: 150px; }
  .mode-orbit {
    position: relative;
    width: 116px;
    height: 116px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 1px solid rgba(37, 99, 235, .28);
    background: var(--panel-muted);
  }
  .open-active .mode-orbit { border-color: rgba(132, 204, 22, .45); }
  .safe-active .mode-orbit { border-color: rgba(37, 99, 235, .38); background: #eff6ff; }
  .mode-orbit::before, .mode-orbit::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    border: 1px solid currentColor;
    opacity: .14;
  }
  .mode-orbit::before { inset: 12px; }
  .mode-orbit::after { inset: 27px; }
  .mode-shield {
    width: 48px;
    height: 56px;
    display: grid;
    place-items: center;
    color: var(--lime-text);
    font-family: var(--mono);
    font-weight: 700;
    font-size: 12px;
    background: var(--lime);
    clip-path: polygon(50% 0, 92% 17%, 84% 70%, 50% 100%, 16% 70%, 8% 17%);
  }
  .safe-active .mode-shield { background: var(--blue); color: #fff; }
  .mode-value { font-family: var(--mono); font-size: 12px; color: var(--text-faint); letter-spacing: .02em; direction: ltr; unicode-bidi: isolate; }
  .mode-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
  .mode-title h2 { font-size: 22px; font-weight: 600; color: var(--navy); }
  .mode-state-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--lime); box-shadow: 0 0 0 5px rgba(132, 204, 22, .16); }
  .safe-active .mode-state-dot { background: var(--blue); box-shadow: 0 0 0 5px rgba(37, 99, 235, .16); }
  .mode-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }
  .mode-choice {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 16px 18px;
    background: #fff;
    color: var(--navy);
    text-align: start;
    min-height: 88px;
    transition: border-color .15s ease, box-shadow .15s ease, background .15s ease, transform .2s ease;
  }
  .mode-choice:hover { transform: translateY(-2px); border-color: var(--blue); box-shadow: var(--shadow); }
  .mode-choice[data-safe-mode="false"].active {
    border-color: var(--blue);
    background: var(--blue-dim);
  }
  .mode-choice[data-safe-mode="true"].active {
    border-color: var(--line-strong);
    background: var(--panel);
  }
  .mode-choice strong { display: block; margin-bottom: 4px; font-weight: 600; color: var(--navy); }
  .mode-choice small { display: block; color: var(--text-dim); line-height: 1.4; }
  .mode-counts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
  .ls-server .ls-action-card { margin-bottom: 16px; }
  .ls-server .ls-action-card:last-child { margin-bottom: 0; }
  @media (max-width: 700px) { .mode-layout { grid-template-columns: 1fr; gap: 12px; } .mode-visual { min-height: 118px; } .mode-actions { grid-template-columns: 1fr; } }
  @media (prefers-reduced-motion: reduce) { .mode-choice { transition: border-color .15s ease, box-shadow .15s ease, background .15s ease; transform: none; } }
</style>
"""


_SERVER_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + _SERVER_STYLE + """</head>
""" + _SHELL_OPEN + """
<div class="ls-page ls-server"><div class="ls-page-header"><div><h1>{{ t('admin.server_title') }}</h1>
<p class="subtitle">{{ t('admin.server_subtitle') }}</p></div></div>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
""" + IDENTITY_BAR + """
{% if status.get('last_error') %}<div class="alert-console-error px-3 py-2 mb-4">{{ status.get('last_error') }}</div>{% endif %}
{% if not supervisor %}<div class="alert-console-error px-3 py-2 mb-4">{{ t('admin.server_unavailable') }}</div>{% endif %}
<section class="ls-section">
  <h2 class="ls-section-title">{{ t('admin.server_status_heading') }}</h2>
  <div class="block-console mode-panel {% if safe_mode %}safe-active{% else %}open-active{% endif %}">
    <div class="mode-layout">
      <div class="mode-visual"><div class="mode-orbit" aria-hidden="true"><div class="mode-shield">{% if safe_mode %}SAFE{% else %}OPEN{% endif %}</div></div></div>
      <div>
        <span class="block-label">SAFE_MODE</span>
        <div class="mode-title"><span class="mode-state-dot"></span><h2 class="mb-0">{% if safe_mode %}{{ t('admin.server_safe_on') }}{% else %}{{ t('admin.server_safe_off') }}{% endif %}</h2><span class="mode-value">SAFE_MODE = {{ safe_mode|string|lower }}</span></div>
        <p class="subtitle mt-2 mb-0">{% if safe_mode %}{{ t('admin.server_safe_on_help') }}{% else %}{{ t('admin.server_safe_off_help') }}{% endif %}</p>
        <div class="mode-counts">
          <span class="tag">{% if supervisor %}{{ t('admin.server.connection_ok') }}{% else %}{{ t('admin.server.connection_limited') }}{% endif %}</span>
          <span class="tag">{{ t('admin.server_pending_users', count=automatic_users) }}</span>
          <span class="tag">{{ t('admin.server_pending_groups', count=automatic_groups) }}</span>
        </div>
        <div class="mode-actions" data-confirm="{{ t('admin.server_safe_confirm', users=automatic_users, groups=automatic_groups) }}">
          <button type="button" data-safe-mode="false" class="mode-choice {% if not safe_mode %}active{% endif %}"><strong>{{ t('admin.server_choose_open') }}</strong><small>{{ t('admin.server_choose_open_help') }}</small></button>
          <button type="button" data-safe-mode="true" class="mode-choice {% if safe_mode %}active{% endif %}"><strong>{{ t('admin.server_choose_safe') }}</strong><small>{{ t('admin.server_choose_safe_help') }}</small></button>
        </div>
        <div id="safe-mode-feedback" class="api-hint mt-3" role="status" aria-live="polite"></div>
      </div>
    </div>
  </div>
</section>
<section class="ls-section">
  <h2 class="ls-section-title">{{ t('admin.server_actions_heading') }}</h2>
  <div class="block-console ls-action-card"><span class="block-label">{{ t('admin.server_profile') }}</span>
  <p class="subtitle">{{ t('admin.server_active_profile', profile=active_profile) }}</p>
  <form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.switch_profile') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><select class="form-select form-select-console" name="profile_module" {% if not supervisor %}disabled{% endif %}>{% for profile in profiles %}<option value="{{ profile.module_path }}" {% if profile.module_path == active_module %}selected{% endif %}>{{ profile.profile_name }} — {{ profile.module_path }} ({{ profile.api_port }})</option>{% endfor %}</select></div><div class="col-auto"><button class="btn btn-console-primary" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_load_profile') }}</button></div></form>
  <p class="subtitle mt-3 mb-0">{{ t('admin.server_restart_required') }}</p></div>
  <div class="block-console ls-action-card ls-danger-zone"><span class="block-label">{{ t('admin.server_reset') }}</span><p class="subtitle">{{ t('admin.server_reset_help') }}</p><form method="post" action="{{ url_for('admin.reset_server') }}" onsubmit="return confirm({{ t('admin.server_reset_confirm')|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="confirm" value="yes"><button class="btn btn-console-danger" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_reset_button') }}</button></form></div>
</section>
</div>""" + API_CLIENT_SCRIPT + """
<script>
(() => {
  const controls = document.querySelector('.mode-actions');
  const feedback = document.getElementById('safe-mode-feedback');
  const currentMode = {{ safe_mode|tojson }};
  const changingText = {{ t('admin.server_safe_changing')|tojson }};
  const changedText = {{ t('admin.server_safe_changed')|tojson }};
  const failedText = {{ t('admin.server_safe_change_failed')|tojson }};
  controls.querySelectorAll('[data-safe-mode]').forEach(button => {
    button.addEventListener('click', async () => {
      const requestedMode = button.dataset.safeMode === 'true';
      if (requestedMode === currentMode) return;
      if (requestedMode && !window.confirm(controls.dataset.confirm)) return;
      controls.querySelectorAll('button').forEach(item => { item.disabled = true; });
      feedback.textContent = changingText;
      const result = await AdminApi.call('PUT', '/SYSTEM', {safe_mode: requestedMode}, 'safe-mode-api-output');
      if (result.ok) {
        feedback.textContent = changedText;
        window.setTimeout(() => window.location.reload(), 450);
      } else {
        feedback.textContent = failedText;
        controls.querySelectorAll('button').forEach(item => { item.disabled = false; });
      }
    });
  });
})();
</script>
<pre id="safe-mode-api-output" class="d-none" hidden></pre>
""" + _SHELL_CLOSE


# docs/Admin_Profile_Switch_Investigation.md §1/§4.1: the wait page must never navigate the
# browser to the OLD profile's port -- fetch() with mode:'no-cors' resolves on *any* HTTP
# response (even the old process, seconds from being killed) and only rejects when a port is
# genuinely down, so racing both candidate URLs and following whichever answers first can strand
# the browser on a port that dies moments later. Fixed sequence, never raced: (1) poll `old_url`
# until it stops answering (confirms the old process actually stopped -- also correct when
# `old_url == target_url`, i.e. a same-port reset, since that still requires observing a real
# down-then-up transition before declaring success), (2) only then poll `target_url` until it
# answers, and navigate there -- never anywhere else. A hard deadline shows both URLs as manual
# links instead of spinning or retrying forever if either phase never completes.
_RESTART_POLL_MS = 1500
_RESTART_TIMEOUT_MS = 60000

_SERVER_WAIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_restarting') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>""" + _SHELL_OPEN + """<div class="ls-page"><div class="block-console">
<div id="wait-status"><h1>{{ t('admin.server_restarting') }}</h1><p class="subtitle">{{ t('admin.server_restarting_help') }}</p></div>
<div id="wait-timeout" hidden><h1>{{ t('admin.server_restart_timeout_title') }}</h1><p class="subtitle">{{ t('admin.server_restart_timeout_help') }}</p><p><a id="target-link" href="{{ target_url }}">{{ t('admin.server_restart_timeout_target_link') }}</a></p><p><a id="old-link" href="{{ old_url }}">{{ t('admin.server_restart_timeout_previous_link') }}</a></p></div>
</div></div><script>
(function(){
  const oldUrl = {{ old_url|tojson }};
  const targetUrl = {{ target_url|tojson }};
  const deadline = Date.now() + {{ timeout_ms }};
  let phase = 'old-down';
  async function reachable(url) {
    try { await fetch(url, {mode: 'no-cors', credentials: 'include', cache: 'no-store'}); return true; }
    catch (error) { return false; }
  }
  async function tick() {
    if (Date.now() >= deadline) {
      document.getElementById('wait-status').hidden = true;
      document.getElementById('wait-timeout').hidden = false;
      return;
    }
    if (phase === 'old-down') {
      if (!(await reachable(oldUrl))) { phase = 'new-up'; }
    } else if (await reachable(targetUrl)) {
      window.location.href = targetUrl;
      return;
    }
    setTimeout(tick, {{ poll_ms }});
  }
  setTimeout(tick, {{ poll_ms }});
})();
</script>
""" + _SHELL_CLOSE


# The simulator page shares the dashboard's chrome (Bootstrap build, palette, header) and adds
# its own style + body from api/admin_simulator.py; assembled here so one module decides how
# admin pages are put together.
_SIMULATOR_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.simulator.title') }}</title>
""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + SIMULATOR_STYLE + """
</head>
""" + _SHELL_OPEN + SIMULATOR_BODY + _SHELL_CLOSE


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

    def _render(template: str, **context) -> str:
        context.setdefault("admin_tables", ctx.loaded_profile.admin_tables)
        return _page_render(template, **context)

    def _api_users() -> list[dict]:
        """Human identities available to the browser API console.

        The service identity is intentionally excluded: the console is meant to
        exercise the same Telegram identities and RBAC rules as real callers,
        not turn the admin password into an API authorization bypass.
        """

        return sorted(
            (
                user
                for user in ctx.deps.persistence.list_users()
                if user["telegram_identity"] != BOT_SERVICE_IDENTITY
            ),
            key=lambda user: (
                user["permission_level"] != "commander",
                user["full_name"].casefold(),
                user["telegram_identity"],
            ),
        )

    def _api_page_context(current_page: str) -> dict:
        users = _api_users()
        available = {user["telegram_identity"] for user in users}
        selected = str(session.get("api_identity") or "")
        if selected not in available:
            selected = users[0]["telegram_identity"] if users else ""
            if selected:
                session["api_identity"] = selected
            else:
                session.pop("api_identity", None)
        return {
            "api_users": users,
            "api_identity": selected,
            "current_page": current_page,
        }

    def _session_expired() -> bool:
        last_activity = session.get("last_activity")
        if last_activity is None:
            return True
        return (time.time() - last_activity) > config.session_timeout_minutes * 60

    def _require_session():
        """None if the caller has a live admin session (and refreshes its inactivity window);
        otherwise a redirect response the route must return immediately."""

        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login"))
        if _session_expired():
            session.clear()
            flash(_t("admin.session_expired"), "error")
            return redirect(url_for("admin.login"))
        session["last_activity"] = time.time()
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
        if not expected or not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
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
                return redirect(url_for("admin.dashboard"))
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
        username_ok = hmac.compare_digest(submitted_username.encode("utf-8"), config.username.encode("utf-8"))
        password_ok = hmac.compare_digest(submitted_password.encode("utf-8"), config.password.encode("utf-8"))

        if username_ok and password_ok:
            rate_limiter.record_success()
            _issue_session(config)
            logger.info(
                "admin login succeeded",
                extra={"event": "admin_login_succeeded", "source_ip": source, "trace_id": get_trace_id()},
            )
            return redirect(url_for("admin.dashboard"))

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
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        logger.info("admin logout", extra={"event": "admin_logout", "trace_id": get_trace_id()})
        session.clear()
        flash(_t("admin.signed_out"), "ok")
        return redirect(url_for("admin.login"))

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

    @blueprint.route("/identity", methods=["POST"])
    def select_api_identity():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response

        identity = request.form.get("api_identity", "").strip()
        if identity not in {user["telegram_identity"] for user in _api_users()}:
            flash(_t("admin.api.identity_invalid"), "error")
        else:
            session["api_identity"] = identity
            flash(_t("admin.api.identity_selected", identity=identity), "ok")

        destinations = {
            "profiles": "admin.profiles",
            "protocols": "admin.protocols",
            "events": "admin.events",
            "users": "admin.users",
            "groups": "admin.groups",
            "server": "admin.server",
            "simulator": "admin.simulator",
        }
        return redirect(url_for(destinations.get(request.form.get("next_page", ""), "admin.dashboard")))

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
            protocols=tuple(
                {
                    "name": protocol.name,
                    "description": protocol.description,
                    "participating_agents": protocol.participating_agents,
                    "approved_tools": protocol.approved_tools,
                    "expected_success_output": protocol.expected_success_output,
                    "criticality": protocol.criticality.name.lower(),
                    "approval_flag": protocol.approval_flag,
                }
                for protocol in ctx.deps.protocol_set.all()
            ),
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
        if not chat_id:
            flash(_t("admin.chat_id_required"), "error")
            return redirect(url_for("admin.groups"))

        existed = ctx.group_routing.get(chat_id) is not None
        try:
            # The same write-through path PUT /Groups and cli.group_admin use — one
            # source of truth for the routing table either way.
            ctx.group_routing.upsert(chat_id, agent_name, label)
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
