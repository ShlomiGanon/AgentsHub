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

from bot.errors import BotStartupError


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
