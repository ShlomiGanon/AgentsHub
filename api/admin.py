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
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Blueprint, flash, get_flashed_messages, jsonify, redirect, render_template_string, request, session, url_for

from api.admin_scenarios import ScenarioMappingError, map_legacy_scenario, scenario_catalog
from api.admin_simulator import SIMULATOR_BODY, SIMULATOR_STYLE, simulator_page_context
from api.admin_api_pages import (
    API_CONSOLE_STYLE,
    EVENTS_BODY,
    PROFILES_BODY,
    PROTOCOLS_BODY,
)
from auth.permissions import InvalidFullNameError, PermissionLevel, normalize_full_name
from config import discover_profiles, read_server_status, submit_server_command, supervisor_available
from messages import get_current_catalog
from orchestrator.flows import InvalidRoutingTargetError
from persistence import EventSearchCriteria, NotFoundError
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

# Duplicated rather than imported from bot.contracts.BOT_SERVICE_IDENTITY: api may not import
# bot (tests/test_architecture.py enforces the package boundary — bot calls api over HTTP, not
# api importing bot's Python code). See api/request_boundary.py's identical duplication and
# comment. Keep this in sync with bot.contracts.BOT_SERVICE_IDENTITY if it ever changes.
BOT_SERVICE_IDENTITY = "bot-service"


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
_BOOTSTRAP_CSS_LINK = (
    '{% if dir == "rtl" %}'
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.rtl.min.css" rel="stylesheet">'
    "{% else %}"
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">'
    "{% endif %}"
)

# Both style blocks below follow the design references (user-admin-bootstrap.html, login.html)
# rather than being merged into one shared stylesheet — the two pages diverge in real ways
# (status-pill's font-size and centering differ between them, the login page has no table/block-console
# rules at all) and keeping each page's CSS as designed avoids introducing any drift. The one
# deliberate departure: physical left/right properties are written as CSS logical properties
# (`border-inline-start`, `inset-inline-start`, `margin-inline-end`) so the same stylesheet lays
# out correctly under both `dir="ltr"` and `dir="rtl"`.
_DASHBOARD_STYLE = """
<style>
  :root {
    --bg: #AFCBE3;
    --panel: #C4DAEC;
    --line: #9BB9D3;
    --line-strong: #7A9CBC;
    --text: #10233A;
    --text-dim: #2E4C6B;
    --text-faint: #55738F;
    --commander: #0A6553;
    --commander-dim: #B9DFD2;
    --viewer: #164C82;
    --viewer-dim: #B9D2E9;
    --danger: #9A302B;
    --danger-dim: #E8C4C0;
    --mono: 'SF Mono', 'JetBrains Mono', ui-monospace, Consolas, monospace;
  }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 16px;
    padding: 56px 0 100px;
  }
  .container-narrow { max-width: 760px; }

  h1 { font-size: 25px; font-weight: 500; letter-spacing: -0.01em; }
  .status-pill {
    font-family: var(--mono);
    font-size: 13px;
    color: var(--text-faint);
  }
  .status-pill .dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--commander);
    box-shadow: 0 0 0 3px var(--commander-dim);
    margin-inline-end: 6px;
  }
  .subtitle { color: var(--text-dim); font-size: 15px; }
  .nav-console { font-size: 14px; color: var(--text-dim); text-decoration: none; }
  .nav-console:hover { color: var(--text); text-decoration: underline; }

  .alert-console {
    background: var(--commander-dim);
    border: 1px solid #9DCFC0;
    border-inline-start: 3px solid var(--commander);
    border-radius: 4px;
    color: #075A47;
    font-family: var(--mono);
    font-size: 14px;
  }
  .alert-console b { font-weight: 600; }
  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #C98782;
    border-inline-start: 3px solid var(--danger);
    border-radius: 4px;
    color: #6B1F1B;
    font-family: var(--mono);
    font-size: 14px;
  }
  .alert-console-error b { font-weight: 600; }

  table.table-console {
    --bs-table-bg: transparent;
    border-collapse: collapse;
    font-size: 15px;
  }
  table.table-console thead th {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-faint);
    letter-spacing: 0.04em;
    border-bottom: 1px solid var(--line-strong) !important;
    border-top: none;
    padding-inline-start: 0;
  }
  table.table-console tbody td {
    border-color: var(--line);
    vertical-align: middle;
    padding-block: 14px;
    padding-inline: 0 0.5rem;
    font-size: 15px;
  }
  table.table-console tbody td:first-child { padding-inline-start: 0; }

  .identity { font-family: var(--mono); font-size: 15px; }
  .identity .tag { font-family: inherit; font-size: 13px; color: var(--text-faint); margin-inline-start: 8px; }

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
    border: 1px solid var(--line-strong);
    color: var(--text);
    font-family: var(--mono);
    font-size: 14px;
  }
  .form-select-console:focus, .form-control-console:focus {
    border-color: var(--text-dim);
    box-shadow: 0 0 0 0.2rem rgba(46, 76, 107, 0.15);
  }

  .btn-console {
    background: #fff;
    color: var(--text-dim);
    border: 1px solid var(--line-strong);
    font-size: 14px;
    font-weight: 500;
    box-shadow: 0 1px 2px rgba(16, 35, 58, 0.15);
  }
  .btn-console:hover { border-color: var(--text-dim); color: var(--text); background: #fff; box-shadow: 0 2px 4px rgba(16, 35, 58, 0.22); }

  .btn-console-danger { color: var(--danger); border-color: #C98782; background: #fff; }
  .btn-console-danger:hover { border-color: var(--danger); color: var(--danger); background: var(--danger-dim); }

  .btn-console-primary { background: var(--commander); border-color: var(--commander); color: #fff; }
  .btn-console-primary:hover { background: #0A5A49; border-color: #0A5A49; color: #fff; }

  .block-console {
    border: 1px solid var(--line-strong);
    border-radius: 6px;
    padding: 20px 24px 24px;
    position: relative;
    background: var(--panel);
  }
  .block-label {
    position: absolute;
    top: -11px;
    inset-inline-start: 18px;
    background: var(--bg);
    padding: 0 8px;
    font-size: 14px;
    font-weight: 500;
    color: var(--text-dim);
  }
  .form-label-console {
    font-size: 12px;
    color: var(--text-faint);
    letter-spacing: 0.02em;
    margin-bottom: 4px;
  }
  code.console-code {
    font-family: var(--mono);
    font-size: 13px;
    color: var(--viewer);
    background: var(--viewer-dim);
    padding: 1px 5px;
    border-radius: 3px;
  }
</style>
"""

_LOGIN_STYLE = """
<style>
  :root {
    --bg: #AFCBE3;
    --panel: #C4DAEC;
    --line: #9BB9D3;
    --line-strong: #7A9CBC;
    --text: #10233A;
    --text-dim: #2E4C6B;
    --text-faint: #55738F;
    --commander: #0A6553;
    --commander-dim: #B9DFD2;
    --viewer: #164C82;
    --viewer-dim: #B9D2E9;
    --danger: #9A302B;
    --danger-dim: #E8C4C0;
    --mono: 'SF Mono', 'JetBrains Mono', ui-monospace, Consolas, monospace;
  }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 16px;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .login-card {
    width: 100%;
    max-width: 380px;
    background: var(--panel);
    border: 1px solid var(--line-strong);
    border-radius: 6px;
    padding: 32px 32px 28px;
  }
  .login-card h1 {
    font-size: 22px;
    font-weight: 500;
    letter-spacing: -0.01em;
    margin-bottom: 4px;
  }
  .login-card .subtitle {
    font-size: 13px;
    color: var(--text-faint);
    font-family: var(--mono);
    margin-bottom: 24px;
  }
  .form-label-console {
    font-size: 12px;
    color: var(--text-faint);
    letter-spacing: 0.02em;
    margin-bottom: 4px;
    display: block;
  }
  .form-control-console {
    background: #fff;
    border: 1px solid var(--line-strong);
    color: var(--text);
    font-family: var(--mono);
    font-size: 14px;
    width: 100%;
  }
  .form-control-console:focus {
    border-color: var(--text-dim);
    box-shadow: 0 0 0 0.2rem rgba(46, 76, 107, 0.15);
  }
  .field-group { margin-bottom: 18px; }

  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #C98782;
    border-inline-start: 3px solid var(--danger);
    border-radius: 4px;
    color: #6B1F1B;
    font-family: var(--mono);
    font-size: 13px;
    padding: 10px 14px;
    margin-bottom: 20px;
  }
  .lockout-progress-track {
    margin-top: 10px;
    height: 6px;
    border-radius: 3px;
    background: rgba(154, 48, 43, 0.18);
    overflow: hidden;
  }
  .lockout-progress-fill {
    height: 100%;
    border-radius: 3px;
    background: var(--danger);
  }

  .btn-console-primary {
    background: var(--commander);
    border-color: var(--commander);
    color: #fff;
    font-size: 14px;
    font-weight: 500;
    width: 100%;
    padding: 8px 0;
    box-shadow: 0 1px 2px rgba(16, 35, 58, 0.15);
  }
  .btn-console-primary:hover { background: #0A5A49; border-color: #0A5A49; color: #fff; }

  .status-pill {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-faint);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-top: 18px;
  }
  .status-pill .dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--commander);
    box-shadow: 0 0 0 3px var(--commander-dim);
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
<body>

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
        <label class="form-label-console" for="username">{{ t('admin.username') }}</label>
        <input type="text" class="form-control-console" id="username" name="username" placeholder="{{ t('admin.username') }}" autofocus required>
      </div>
      <div class="field-group">
        <label class="form-label-console" for="password">{{ t('admin.password') }}</label>
        <input type="password" class="form-control-console" id="password" name="password" placeholder="••••••••" required>
      </div>
      <button type="submit" class="btn-console-primary">{{ t('admin.sign_in') }}</button>
    </form>

    <div class="status-pill"><span class="dot"></span>{{ t('admin.connected') }}</div>
  </div>

</body>
</html>
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
</head><body><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline mb-1">
    <h1>{{ t('admin.menu_title') }}</h1>
    <form method="post" action="{{ url_for('admin.logout') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button class="btn btn-console-danger btn-sm">{{ t('admin.log_out') }}</button></form>
  </div>
  <p class="subtitle mb-4">{{ t('admin.menu_subtitle') }}</p>
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}
  <div class="row g-3">
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.profiles') }}"><h2 class="h5">{{ t('admin.menu_profiles') }}</h2><span class="subtitle">{{ t('admin.profiles.subtitle') }}</span></a></div>
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.protocols') }}"><h2 class="h5">{{ t('admin.menu_protocols') }}</h2><span class="subtitle">{{ t('admin.protocols.subtitle') }}</span></a></div>
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.events') }}"><h2 class="h5">{{ t('admin.menu_events') }}</h2><span class="subtitle">{{ t('admin.events.subtitle') }}</span></a></div>
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.users') }}"><h2 class="h5">{{ t('admin.menu_users') }}</h2><span class="subtitle">{{ t('admin.users_subtitle') }}</span></a></div>
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.groups') }}"><h2 class="h5">{{ t('admin.menu_groups') }}</h2><span class="subtitle">{{ t('admin.groups_page_subtitle') }}</span></a></div>
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.simulator') }}"><h2 class="h5">{{ t('admin.menu_simulator') }}</h2><span class="subtitle">{{ t('admin.simulator.subtitle') }}</span></a></div>
    <div class="col-sm-6"><a class="block-console d-block text-decoration-none h-100" href="{{ url_for('admin.server') }}"><h2 class="h5">{{ t('admin.menu_server') }}</h2><span class="subtitle">{{ t('admin.server_subtitle') }}</span></a></div>
  </div>
</div></body></html>"""


_USERS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ t('admin.users_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head><body><div class="container container-narrow">
  <div class="d-flex justify-content-between align-items-baseline mb-1"><h1>{{ t('admin.users_title') }}</h1>
    <a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
  <p class="subtitle mb-4">{{ t('admin.users_subtitle') }}</p>
  {% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
  <table class="table table-console mb-5"><thead><tr><th>{{ t('admin.col_identity') }}</th><th>{{ t('admin.col_full_name') }}</th><th>{{ t('admin.col_level') }}</th><th></th></tr></thead><tbody>
  {% for user in users %}<tr><td class="identity">{{ user.telegram_identity }}{% if user.telegram_identity == bot_service_identity %} <span class="tag">{{ t('admin.tag_bot_service') }}</span>{% endif %}<div class="mt-2"><span class="tag">{% if user.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and user.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></div></td>
    <td colspan="2"><form class="d-flex gap-2" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="telegram_identity" value="{{ user.telegram_identity }}">
      <input type="text" name="full_name" value="{{ user.full_name }}" class="form-control form-control-console" maxlength="120" placeholder="{{ t('admin.col_full_name') }}">
      <select name="permission_level" class="form-select form-select-console form-select-sm w-auto">{% for level in levels %}<option value="{{ level }}" {% if level == user.permission_level %}selected{% endif %}>{{ level }}</option>{% endfor %}</select>
      <button class="btn btn-console btn-sm">{{ t('admin.save') }}</button></form></td>
    <td><div class="d-flex gap-2">{% if user.auto_register %}<form method="post" action="{{ url_for('admin.approve_user', identity=user.telegram_identity) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary btn-sm">{{ t('admin.approve_registration') }}</button></form>{% endif %}<form method="post" action="{{ url_for('admin.remove_user', identity=user.telegram_identity) }}" onsubmit="return confirm({{ t('admin.confirm_remove_user', identity=user.telegram_identity)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger btn-sm">{{ t('admin.remove') }}</button></form></div></td></tr>{% endfor %}
  </tbody></table>
  <div class="block-console mb-4"><span class="block-label">{{ t('admin.add_user') }}</span><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_user') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="col"><div class="form-label-console">{{ t('admin.col_identity') }}</div><input name="telegram_identity" class="form-control form-control-console" required></div>
    <div class="col"><div class="form-label-console">{{ t('admin.col_full_name') }}</div><input name="full_name" class="form-control form-control-console" maxlength="120"></div>
    <div class="col-auto"><div class="form-label-console">{{ t('admin.col_level') }}</div><select name="permission_level" class="form-select form-select-console">{% for level in levels %}<option value="{{ level }}">{{ level }}</option>{% endfor %}</select></div>
    <div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div>
  <div class="block-console"><span class="block-label">{{ t('admin.bot_service_title') }}</span><p class="subtitle">{{ t('admin.bot_service_help', identity=bot_service_identity) }}</p><form method="post" action="{{ url_for('admin.provision_bot_service') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console">{{ t('admin.bot_service_button') }}</button></form></div>
</div></body></html>"""


_GROUPS_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.groups_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>
<body><div class="container container-narrow"><div class="d-flex justify-content-between align-items-baseline"><h1>{{ t('admin.groups_title') }}</h1><a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div><p class="subtitle mb-4">{{ t('admin.groups_page_subtitle') }}</p>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
<table class="table table-console mb-4"><thead><tr><th>{{ t('admin.col_chat_id') }}</th><th>{{ t('admin.col_label') }}</th><th>{{ t('admin.col_routed_to') }}</th><th></th></tr></thead><tbody>
{% for group in groups %}<tr><td class="identity">{{ group.chat_id }}<div class="mt-2"><span class="tag">{% if group.auto_register %}{{ t('admin.registration_automatic') }}{% else %}{{ t('admin.registration_approved') }}{% endif %}</span> <span class="tag">{% if safe_mode and group.auto_register %}{{ t('admin.registration_blocked') }}{% else %}{{ t('admin.registration_active') }}{% endif %}</span></div></td><td colspan="2"><form class="d-flex gap-2" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="chat_id" value="{{ group.chat_id }}"><input name="label" value="{{ group.label }}" maxlength="200" class="form-control form-control-console" placeholder="{{ t('admin.col_label') }}"><select name="agent_name" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}" {% if agent_name == group.agent_name %}selected{% endif %}>{{ agent_name }}</option>{% endfor %}</select><button class="btn btn-console">{{ t('admin.save') }}</button></form></td><td><div class="d-flex gap-2">{% if group.auto_register %}<form method="post" action="{{ url_for('admin.approve_group', chat_id=group.chat_id) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-primary">{{ t('admin.approve_registration') }}</button></form>{% endif %}<form method="post" action="{{ url_for('admin.remove_group', chat_id=group.chat_id) }}" onsubmit="return confirm({{ t('admin.confirm_remove_group', chat_id=group.chat_id)|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn-console-danger">{{ t('admin.remove') }}</button></form></div></td></tr>{% else %}<tr><td colspan="4">{{ t('admin.no_groups') }}</td></tr>{% endfor %}</tbody></table>
<div class="block-console"><span class="block-label">{{ t('admin.add_group') }}</span><p class="subtitle">{{ t('admin.add_group_help', main_agent='main_agent') }}</p><form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_group') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><div class="form-label-console">{{ t('admin.col_chat_id') }}</div><input name="chat_id" class="form-control form-control-console" placeholder="-1001234567890" required></div><div class="col"><div class="form-label-console">{{ t('admin.col_label') }}</div><input name="label" class="form-control form-control-console" maxlength="200"></div><div class="col-auto"><select name="agent_name" class="form-select form-select-console">{% for agent_name in routable_agents %}<option value="{{ agent_name }}">{{ agent_name }}</option>{% endfor %}</select></div><div class="col-auto"><button class="btn btn-console-primary">{{ t('admin.add') }}</button></div></form></div>
</div></body></html>"""


_PROFILES_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.profiles.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + PROFILES_BODY + """</html>"""


_PROTOCOLS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.protocols.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + PROTOCOLS_BODY + """</html>"""


_EVENTS_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.events.title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + API_CONSOLE_STYLE + """</head>""" + EVENTS_BODY + """</html>"""


_SERVER_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_title') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head>
<body><div class="container container-narrow"><div class="d-flex justify-content-between align-items-baseline"><h1>{{ t('admin.server_title') }}</h1><a class="nav-console" href="{{ url_for('admin.dashboard') }}">{{ t('admin.nav_menu') }}</a></div>
<p class="subtitle mb-4">{{ t('admin.server_subtitle') }}</p>
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>{% endfor %}
{% if status.get('last_error') %}<div class="alert-console-error px-3 py-2 mb-4">{{ status.get('last_error') }}</div>{% endif %}
{% if not supervisor %}<div class="alert-console-error px-3 py-2 mb-4">{{ t('admin.server_unavailable') }}</div>{% endif %}
<div class="block-console mb-4"><span class="block-label">{{ t('admin.server_profile') }}</span><p class="subtitle">{{ t('admin.server_active_profile', profile=active_profile) }}</p>
<form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.switch_profile') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><div class="col"><select class="form-select form-select-console" name="profile_module" {% if not supervisor %}disabled{% endif %}>{% for profile in profiles %}<option value="{{ profile.module_path }}" {% if profile.module_path == active_module %}selected{% endif %}>{{ profile.profile_name }} — {{ profile.module_path }} ({{ profile.api_port }})</option>{% endfor %}</select></div><div class="col-auto"><button class="btn btn-console-primary" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_load_profile') }}</button></div></form>
<p class="subtitle mt-3 mb-0">{{ t('admin.server_restart_required') }}</p></div>
<div class="block-console"><span class="block-label">{{ t('admin.server_reset') }}</span><p class="subtitle">{{ t('admin.server_reset_help') }}</p><form method="post" action="{{ url_for('admin.reset_server') }}" onsubmit="return confirm({{ t('admin.server_reset_confirm')|tojson|forceescape }});"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="confirm" value="yes"><button class="btn btn-console-danger" {% if not supervisor %}disabled{% endif %}>{{ t('admin.server_reset_button') }}</button></form></div>
</div></body></html>"""


_SERVER_WAIT_TEMPLATE = """<!DOCTYPE html><html lang="{{ lang }}" dir="{{ dir }}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ t('admin.server_restarting') }}</title>""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """</head><body><div class="container container-narrow"><div class="block-console"><h1>{{ t('admin.server_restarting') }}</h1><p class="subtitle">{{ t('admin.server_restarting_help') }}</p><p><a id="retry-link" href="{{ target_url }}">{{ t('admin.server_retry_link') }}</a></p></div></div><script>
(function(){ const candidates={{ candidate_urls|tojson }}; async function probe(){ for(const url of candidates){ try{ await fetch(url, {mode:'no-cors', credentials:'include', cache:'no-store'}); window.location.href=url; return; }catch(error){} } setTimeout(probe, 1500); } setTimeout(probe, 3000); })();
</script></body></html>"""


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
<body>
""" + SIMULATOR_BODY + """
</body>
</html>
"""


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

        return _render(_MENU_TEMPLATE, csrf_token=session["csrf_token"])

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
                "safe_mode": ctx.deps.settings_store.get_safe_mode(),
            },
            automatic_users=sum(bool(user.get("auto_register", False)) for user in ctx.deps.persistence.list_users()),
            automatic_groups=sum(bool(group.auto_register) for group in ctx.group_routing.all()),
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
            csrf_token=session["csrf_token"],
        )

    def _restart_page(port: int):
        host = request.host.split(":", 1)[0]
        target_url = f"{request.scheme}://{host}:{port}/admin/server"
        fallback_url = f"{request.scheme}://{host}:{ctx.loaded_profile.api_port}/admin/server"
        candidates = list(dict.fromkeys((target_url, fallback_url)))
        return _render(_SERVER_WAIT_TEMPLATE, target_url=target_url, candidate_urls=candidates)

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

        return _render(
            _SIMULATOR_TEMPLATE,
            page_data=simulator_page_context(ctx, get_current_catalog(), BOT_SERVICE_IDENTITY),
            csrf_token=session["csrf_token"],
        )

    @blueprint.route("/simulator/example", methods=["POST"])
    def load_simulator_example():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response
        csrf_response = _require_csrf()
        if csrf_response is not None:
            return csrf_response
        example_key = request.form.get("example_key", "")
        example = next((item for item in scenario_catalog() if item["key"] == example_key), None)
        if example is None:
            return jsonify({"error": _t("admin.simulator.example_invalid")}), 400
        try:
            persona_ids = json.loads(request.form.get("persona_ids", "{}"))
            group_ids = json.loads(request.form.get("group_ids", "{}"))
            registered = {
                str(user["telegram_identity"]): user for user in ctx.deps.persistence.list_users()
            }
            mapped = map_legacy_scenario(
                example["raw"], persona_ids, group_ids, registered,
                unregistered_label=_t("admin.simulator.unregistered_name"),
                missing_name_label=_t("admin.simulator.missing_name"),
            )
        except (ValueError, TypeError, ScenarioMappingError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(mapped)

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

        # The exact same write cli.user_admin's `add`/`update` commands make — not a
        # separate mechanism, one source of truth for user storage either way.
        ctx.deps.persistence.write_user(BOT_SERVICE_IDENTITY, "commander")
        ctx.deps.persistence.approve_user(BOT_SERVICE_IDENTITY)
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
            + '</head><body><div class="container container-narrow">'
            '<h1 class="mb-2">{{ t(\'admin.error_title\') }}</h1>'
            '<p class="subtitle">{{ t(\'admin.error_subtitle\') }}</p>'
            "</div></body></html>"
        ), 500

    return blueprint
