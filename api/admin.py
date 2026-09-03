"""Admin web panel for user management (login-gated, server-rendered HTML).

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

from flask import Blueprint, flash, get_flashed_messages, redirect, render_template_string, request, session, url_for

from auth.permissions import PermissionLevel
from persistence import NotFoundError
from tools import get_trace_id

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
    """In-memory, per-source-IP login lockout — reset on process restart, no shared store, which
    is fine for the single-process deployment this whole panel already assumes (SingleInstanceLock
    elsewhere in this codebase makes the same assumption).

    Tracked per source IP, not per username: ADMIN_USERNAME is one shared credential for every
    commander using this panel (there's no per-person admin account), so a per-username lockout
    would let a single hostile — or even accidental — bad login from anywhere lock out every
    legitimate commander at once. Per-IP contains a lockout to whoever is actually failing,
    at the cost of not stopping a distributed attempt from many source IPs — an accepted
    trade-off for a small deployment's admin panel, not a public-internet-scale defense.
    """

    def __init__(self, max_attempts: int, lockout_minutes: int):
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_minutes * 60
        self._lock = threading.Lock()
        self._failure_counts: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def locked_out_for(self, source: str) -> float:
        """Seconds remaining locked out, or 0.0 if not currently locked out."""

        with self._lock:
            locked_until = self._locked_until.get(source)
            if locked_until is None:
                return 0.0
            remaining = locked_until - time.monotonic()
            if remaining <= 0:
                self._locked_until.pop(source, None)
                self._failure_counts.pop(source, None)
                return 0.0
            return remaining

    def record_failure(self, source: str) -> None:
        with self._lock:
            count = self._failure_counts.get(source, 0) + 1
            if count >= self._max_attempts:
                self._locked_until[source] = time.monotonic() + self._lockout_seconds
                self._failure_counts.pop(source, None)
            else:
                self._failure_counts[source] = count

    def record_success(self, source: str) -> None:
        with self._lock:
            self._failure_counts.pop(source, None)
            self._locked_until.pop(source, None)


_BOOTSTRAP_CSS_LINK = (
    '<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">'
)

# Both style blocks below are copied verbatim from the design references (user-admin-bootstrap.html,
# login.html) rather than merged into one shared stylesheet — the two pages diverge in real ways
# (status-pill's font-size and centering differ between them, the login page has no table/block-console
# rules at all) and keeping each page's CSS exactly as designed avoids introducing any drift.
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
    margin-right: 6px;
  }
  .subtitle { color: var(--text-dim); font-size: 15px; }

  .alert-console {
    background: var(--commander-dim);
    border: 1px solid #9DCFC0;
    border-left: 3px solid var(--commander);
    border-radius: 4px;
    color: #075A47;
    font-family: var(--mono);
    font-size: 14px;
  }
  .alert-console b { font-weight: 600; }
  .alert-console-error {
    background: var(--danger-dim);
    border: 1px solid #C98782;
    border-left: 3px solid var(--danger);
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
    padding-left: 0;
  }
  table.table-console tbody td {
    border-color: var(--line);
    vertical-align: middle;
    padding: 14px 0.5rem 14px 0;
    font-size: 15px;
  }
  table.table-console tbody td:first-child { padding-left: 0; }

  .identity { font-family: var(--mono); font-size: 15px; }
  .identity .tag { font-family: inherit; font-size: 13px; color: var(--text-faint); margin-left: 8px; }

  .level-dot {
    display: inline-block;
    width: 5px; height: 5px;
    border-radius: 50%;
    margin-right: 6px;
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
    left: 18px;
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
    border-left: 3px solid var(--danger);
    border-radius: 4px;
    color: #6B1F1B;
    font-family: var(--mono);
    font-size: 13px;
    padding: 10px 14px;
    margin-bottom: 20px;
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
    margin-right: 6px;
  }
</style>
"""

_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin sign in</title>
""" + _BOOTSTRAP_CSS_LINK + _LOGIN_STYLE + """
</head>
<body>

  <div class="login-card">
    <h1>Admin sign in</h1>
    <p class="subtitle">Bot control panel</p>

    {% for category, message in get_flashed_messages(with_categories=true) %}
      <div class="alert-console-error">{{ message }}</div>
    {% endfor %}

    <form id="loginForm" method="post">
      <div class="field-group">
        <label class="form-label-console" for="username">Username</label>
        <input type="text" class="form-control-console" id="username" name="username" placeholder="username" autofocus required>
      </div>
      <div class="field-group">
        <label class="form-label-console" for="password">Password</label>
        <input type="password" class="form-control-console" id="password" name="password" placeholder="••••••••" required>
      </div>
      <button type="submit" class="btn-console-primary">Sign in</button>
    </form>

    <div class="status-pill"><span class="dot"></span>connected</div>
  </div>

</body>
</html>
"""

_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>User administration</title>
""" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE + """
</head>
<body>
<div class="container container-narrow">

  <div class="d-flex justify-content-between align-items-baseline mb-1">
    <h1 class="mb-0">User administration</h1>
    <div class="d-flex align-items-center gap-3">
      <span class="status-pill"><span class="dot"></span>connected</span>
      <form method="post" action="{{ url_for('admin.logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="btn btn-console-danger btn-sm">Log out</button>
      </form>
    </div>
  </div>
  <p class="subtitle mb-4">Manage who can talk to the bot and what they're allowed to do.</p>

  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="alert-console{% if category == 'error' %}-error{% endif %} px-3 py-2 mb-4">{{ message }}</div>
  {% endfor %}

  <table class="table table-console mb-5">
    <thead>
      <tr>
        <th>Telegram identity</th>
        <th>Level</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for user in users %}
      <tr>
        <td class="identity">{{ user.telegram_identity }}{% if user.telegram_identity == bot_service_identity %} <span class="tag">bot's own service identity</span>{% endif %}</td>
        <td>
          <form class="d-flex gap-2" method="post" action="{{ url_for('admin.write_user') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <input type="hidden" name="telegram_identity" value="{{ user.telegram_identity }}">
            <select name="permission_level" class="form-select form-select-console form-select-sm w-auto">
              {% for level in levels %}
              <option value="{{ level }}" {% if level == user.permission_level %}selected{% endif %}>{{ level }}</option>
              {% endfor %}
            </select>
            <button type="submit" class="btn btn-console btn-sm">Save</button>
          </form>
        </td>
        <td class="text-end">
          <form method="post" action="{{ url_for('admin.remove_user', identity=user.telegram_identity) }}"
                onsubmit="return confirm('Remove {{ user.telegram_identity }}?');">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit" class="btn btn-console-danger btn-sm">Remove</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="block-console mb-4">
    <span class="block-label">Add a user</span>
    <form class="row g-3 align-items-end" method="post" action="{{ url_for('admin.write_user') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <div class="col">
        <div class="form-label-console">Telegram identity</div>
        <input type="text" name="telegram_identity" class="form-control form-control-console" placeholder="123456789" required>
      </div>
      <div class="col-auto">
        <div class="form-label-console">Level</div>
        <select name="permission_level" class="form-select form-select-console">
          {% for level in levels %}<option value="{{ level }}">{{ level }}</option>{% endfor %}
        </select>
      </div>
      <div class="col-auto">
        <button type="submit" class="btn btn-console-primary">Add</button>
      </div>
    </form>
  </div>

  <div class="block-console mb-4">
    <span class="block-label">Bot's own service identity</span>
    <p class="mb-3" style="font-size:13px; color:var(--text-dim); max-width:560px; line-height:1.6;">
      Registers or re-registers <code class="console-code">{{ bot_service_identity }}</code> at commander level.
      Required before the bot can poll notifications, read the commander roster, or check for profile changes.
    </p>
    <form method="post" action="{{ url_for('admin.provision_bot_service') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button type="submit" class="btn btn-console">Register bot-service</button>
    </form>
  </div>

</div>
</body>
</html>
"""


def _client_source() -> str:
    return request.remote_addr or "unknown"


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
            flash("Your session expired from inactivity — please sign in again.", "error")
            return redirect(url_for("admin.login"))
        session["last_activity"] = time.time()
        return None

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
            flash("That action could not be verified — please try again.", "error")
            return redirect(url_for("admin.dashboard"))
        return None

    @blueprint.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("admin_authenticated") and not _session_expired():
                return redirect(url_for("admin.dashboard"))
            return render_template_string(_LOGIN_TEMPLATE)

        source = _client_source()
        remaining = rate_limiter.locked_out_for(source)
        if remaining > 0:
            logger.info(
                "admin login attempt while locked out",
                extra={"event": "admin_login_locked_out", "source_ip": source, "trace_id": get_trace_id()},
            )
            flash(f"Too many failed attempts — try again in {int(remaining // 60) + 1} minute(s).", "error")
            return render_template_string(_LOGIN_TEMPLATE)

        submitted_username = request.form.get("username", "")
        submitted_password = request.form.get("password", "")
        username_ok = hmac.compare_digest(submitted_username.encode("utf-8"), config.username.encode("utf-8"))
        password_ok = hmac.compare_digest(submitted_password.encode("utf-8"), config.password.encode("utf-8"))

        if username_ok and password_ok:
            rate_limiter.record_success(source)
            _issue_session(config)
            logger.info(
                "admin login succeeded",
                extra={"event": "admin_login_succeeded", "source_ip": source, "trace_id": get_trace_id()},
            )
            return redirect(url_for("admin.dashboard"))

        rate_limiter.record_failure(source)
        logger.warning(
            "admin login failed",
            extra={
                "event": "admin_login_failed", "source_ip": source,
                "attempted_username": submitted_username, "trace_id": get_trace_id(),
            },
        )
        # Deliberately generic — never says which of username/password was wrong.
        flash("Wrong username or password.", "error")
        return render_template_string(_LOGIN_TEMPLATE)

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
        flash("Signed out.", "ok")
        return redirect(url_for("admin.login"))

    @blueprint.route("/", methods=["GET"])
    def dashboard():
        redirect_response = _require_session()
        if redirect_response is not None:
            return redirect_response

        users = sorted(ctx.deps.persistence.list_users(), key=lambda user: user["telegram_identity"])
        return render_template_string(
            _DASHBOARD_TEMPLATE,
            users=users,
            levels=levels,
            csrf_token=session["csrf_token"],
            bot_service_identity=BOT_SERVICE_IDENTITY,
        )

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
        if not identity:
            flash("A Telegram identity is required.", "error")
            return redirect(url_for("admin.dashboard"))
        if level not in levels:
            flash(f"'{level}' is not a valid permission level.", "error")
            return redirect(url_for("admin.dashboard"))

        existed = ctx.deps.persistence.read_user(identity) is not None
        ctx.deps.persistence.write_user(identity, level)
        logger.info(
            "admin wrote a user",
            extra={
                "event": "admin_user_updated" if existed else "admin_user_added",
                "telegram_identity": identity, "permission_level": level, "trace_id": get_trace_id(),
            },
        )
        flash(f"'{identity}' is now '{level}'.", "ok")
        return redirect(url_for("admin.dashboard"))

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
            flash(f"No such user: '{identity}'.", "error")
            return redirect(url_for("admin.dashboard"))

        logger.info(
            "admin removed a user",
            extra={"event": "admin_user_removed", "telegram_identity": identity, "trace_id": get_trace_id()},
        )
        flash(f"'{identity}' removed.", "ok")
        return redirect(url_for("admin.dashboard"))

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
        logger.info(
            "admin (re-)provisioned the bot-service identity",
            extra={"event": "admin_bot_service_provisioned", "trace_id": get_trace_id()},
        )
        flash(f"'{BOT_SERVICE_IDENTITY}' is registered at commander level.", "ok")
        return redirect(url_for("admin.dashboard"))

    @blueprint.errorhandler(Exception)
    def _admin_unexpected_error(error: Exception):
        logger.exception(
            "unhandled exception in an admin request", extra={"event": "admin_unexpected_error", "trace_id": get_trace_id()}
        )
        return render_template_string(
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
            "<title>Something went wrong</title>" + _BOOTSTRAP_CSS_LINK + _DASHBOARD_STYLE
            + '</head><body><div class="container container-narrow">'
            '<h1 class="mb-2">Something went wrong</h1>'
            '<p class="subtitle">Try again, or check the server log.</p>'
            "</div></body></html>"
        ), 500

    return blueprint
