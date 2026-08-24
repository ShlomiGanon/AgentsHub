"""Persist the notification feed's read cursor across a bot restart
(work_plan.md §8.12's own forward-looking note).

`GET /Notifications` (§8.12) is a stateless, caller-supplied-cursor feed —
the API remembers nothing per caller, so nothing stops a bot restart from
either replaying everything already delivered (starting over at 0) or,
worse, silently losing track of where it was. A small file beside the
deployment's database — the same "beside the db, not beside the profile"
convention `config.settings_store` and `bot.singleton_lock` both already
use — is the whole mechanism: written after every successful poll, read
once at startup. No new persistence-layer or API surface needed for this;
it is purely local, per-bot-process state, the same way the singleton
lock file is.
"""

from pathlib import Path


class NotificationCursorStore:
    def __init__(self, path: Path):
        self._path = path

    def read(self) -> int:
        try:
            return int(self._path.read_text().strip())
        except (FileNotFoundError, ValueError):
            # No file yet (first-ever run), or a corrupted/partial write —
            # either way, starting over at 0 means "may redeliver a few
            # notifications once," never "silently skip real ones," which
            # is the safer failure direction.
            return 0

    def write(self, cursor: int) -> None:
        self._path.write_text(str(cursor))
