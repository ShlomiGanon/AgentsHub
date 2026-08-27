"""Bot-specific error types (work_plan.md §8).

`ApiNotImplementedError` is the one error type this mission introduces
that is not really "a bug" — it is the deliberate, explicit marker for
every capability that is real on the bot side but cannot complete because
the API Layer (work_plan.md §7) has not been built yet. Every raise site
names the exact work_plan.md subtask it is blocked on, so grepping for
this class finds the complete, current list of Mission-7 gaps. See
`bot/api_client.py`.
"""


class BotError(Exception):
    """Base class for every error this package raises."""


class BotStartupError(BotError):
    """The bot could not start — a missing/rejected token, or a second
    instance already running for this deployment (work_plan.md §8.1).
    """


class ApiNotImplementedError(BotError, NotImplementedError):
    """The API Layer (work_plan.md §7) does not implement this operation
    yet. Raised by `bot.api_client.UnimplementedApiClient` — the seam a
    real HTTP-backed client will replace once §7 lands (see that module's
    docstring). Deliberately a subclass of `NotImplementedError` too, so
    existing "not implemented" handling still catches it.
    """

    def __init__(self, operation: str, blocked_on: str):
        self.operation = operation
        self.blocked_on = blocked_on
        super().__init__(
            f"'{operation}' is not available: it depends on {blocked_on} "
            f"(work_plan.md §7 — API Layer), which has not been built yet."
        )


class ApiRequestError(BotError):
    """A real API call, made by `bot.http_api_client.HttpApiClient`,
    failed in a way its own DTO has no slot for — an HTTP 401/403/500, or
    the request never reaching the API at all (connection refused, DNS,
    timeout). `docs/api_spec.md`'s "Mapping to BotApiClient" section
    names exactly which of `HttpApiClient`'s methods raise this versus
    folding the failure into their own DTO — this is the one error type
    every "must raise" case in that mapping raises.
    """

    def __init__(self, status_code: int | None, message: str, error_class: str | None = None, field: str | None = None):
        self.status_code = status_code
        self.message = message
        self.error_class = error_class
        self.field = field
        super().__init__(f"API request failed ({status_code if status_code is not None else 'no response'}): {message}")

"""Run one bot per deployment (work_plan.md §8.1's last bullet).

Two processes polling the same Telegram token compete for the same
messages and each sees half of them — a fresh exclusive-create lock file
beside the deployment's database (the same "beside the db, not beside
the profile" convention `config.settings_store` uses for live settings)
stops a second bot for the *same* deployment from starting while the
first is still running.

This catches the ordinary case — starting a second process while the
first is healthy — by construction: `os.O_EXCL` either creates the file
or fails atomically, with no race window. It does not detect a stale lock
left behind by a process that crashed without releasing it; that is a
deliberate, documented limitation, not a Mission 7 dependency — resolving
it requires either a PID-liveness check (platform-specific) or an
operator manually removing the file, either of which is a separate,
larger decision than this subtask calls for.
"""

import os
from pathlib import Path


class AlreadyRunningError(BotStartupError):
    pass


class SingleInstanceLock:
    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        try:
            self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AlreadyRunningError(
                f"a bot process for this deployment appears to already be running "
                f"(lock file exists: {self._lock_path}); if the previous process crashed "
                f"without cleaning up, remove that file manually before restarting"
            ) from exc

        os.write(self._fd, str(os.getpid()).encode("utf-8"))

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

        self._lock_path.unlink(missing_ok=True)

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

