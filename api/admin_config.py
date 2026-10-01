"""Admin panel enablement, credentials, and login lockout."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

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
