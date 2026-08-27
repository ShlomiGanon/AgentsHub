"""The shared proactive-push dispatcher (work_plan.md §8.4, reused by
§8.5, §8.6, §8.9, §8.11).

`bot.api_client.BotNotification` is the one shape every unprompted push
travels in, regardless of which subtask produces it — see that module's
docstring for why. This module is the single place that reads a batch of
them (via `BotApiClient.poll_pending_notifications`, now backed for real
by `GET /Notifications`, §8.12) and routes each to the module that knows
how to format and deliver it. Introduced while building §8.4 (the first
proactive push this package needed); every later push-style subtask calls
`dispatch_notification` rather than re-implementing routing.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from bot import holds
from bot.startup import ApiNotImplementedError

if TYPE_CHECKING:
    from bot.api_client import BotNotification
    from bot.deps import BotDeps

logger = logging.getLogger(__name__)


async def dispatch_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    if notification.kind == "clarification_hold":
        await holds.push_clarification_prompt(deps, notification.payload)
        return

    if notification.kind == "approval_hold":
        await holds.push_approval_prompt(deps, notification.payload)
        return

    if notification.kind == "uncertain_verdict":
        await holds.notify_uncertain_verdict(deps, notification.payload)
        return

    if notification.kind == "precedent_closure":
        await notify_precedent_closure(deps, notification.payload)
        return

    if notification.kind == "no_match_notice":
        await holds.notify_no_match(deps, notification.payload)
        return

    if notification.kind == "job_finished":
        await deliver_job_result(deps, notification)
        return

    if notification.kind == "job_failed":
        await deliver_failure_notification(deps, notification)
        return

    raise ValueError(f"unknown notification kind: {notification.kind!r}")


async def run_notification_poll_once(deps: "BotDeps", since: int = 0) -> tuple[int, int]:
    """Fetch and dispatch whatever is pending since `since`. Returns
    `(count_handled, next_cursor)` — the count for tests and logging, the
    cursor for the caller to persist and pass as `since` next time
    (unchanged from `since` when nothing new was found).
    """

    notifications, next_cursor = await deps.api_client.poll_pending_notifications(since)

    for notification in notifications:
        await dispatch_notification(deps, notification)

    return len(notifications), next_cursor


async def run_notification_poll_loop(
    deps: "BotDeps",
    poll_interval_seconds: float = 5.0,
    max_iterations: int | None = None,
    cursor_store: "NotificationCursorStore | None" = None,
) -> None:
    """Repeatedly call `run_notification_poll_once`, forever by default
    (`max_iterations=None`), or a fixed number of times — for tests.

    `cursor_store`, when given, is read once at startup for where to
    resume and written after every successful poll — see
    `bot.notifications`'s own docstring for why a restart needs
    this. Omitted (the default) for every existing test and for any
    caller that doesn't care about surviving a restart; the loop still
    tracks the cursor in memory across its own iterations either way, so
    a single run never redelivers anything to itself.

    A failed poll is logged and never stops the loop, the same
    "a failure here must not stop everything else" stance
    `orchestrator.queue.SerialEventQueue` takes for event processing.
    Against `UnimplementedApiClient`, every iteration fails identically
    with `ApiNotImplementedError` — logged once per iteration rather than
    raised, so running the bot with no real API configured degrades to
    "polling finds nothing yet" instead of crashing the process.
    """

    cursor = cursor_store.read() if cursor_store is not None else 0
    iterations = 0

    while max_iterations is None or iterations < max_iterations:
        try:
            _count, cursor = await run_notification_poll_once(deps, cursor)
            if cursor_store is not None:
                cursor_store.write(cursor)
        except ApiNotImplementedError as exc:
            logger.info("notification poll skipped: %s", exc, extra={"event": "notification_poll_not_implemented"})
        except Exception:
            logger.exception("notification poll failed; continuing", extra={"event": "notification_poll_failed"})

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            await asyncio.sleep(poll_interval_seconds)

"""Deliver failure notifications (work_plan.md §8.11).

Sent to whoever originated the event, naming the step that failed and
why it exhausted retries, and including whatever succeeded before that
step — a run that fails at its last step still produced findings, and
losing them from the notification would understate what happened. Kept
visually and textually distinct from a declined run and from an
uncertain verdict (`bot.formatting`'s headers): all three end without a
clean success, and each calls for a different response from whoever
reads it.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_failure_notice

if TYPE_CHECKING:
    from bot.api_client import BotNotification
    from bot.deps import BotDeps


async def deliver_failure_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    notice = notification.payload
    text = format_failure_notice(notice)

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)

"""Deliver asynchronous results (work_plan.md §8.9).

The acknowledgment half of §8.9 ("acknowledge every submission
immediately") is `bot.app.handle_incoming_message`'s job — it
replies in the same turn `submit_message` returns a job ID, before any
model call runs. This module is the other half: once a job finishes, its
result is pushed back to whoever submitted it, in the chat they submitted
it from, as a reply referencing their original message (minutes may have
passed; they may have sent others meanwhile).
"""

from typing import TYPE_CHECKING

from bot.formatting import format_job_result

if TYPE_CHECKING:
    from bot.api_client import BotNotification
    from bot.deps import BotDeps


async def deliver_job_result(deps: "BotDeps", notification: "BotNotification") -> None:
    result = notification.payload
    text = format_job_result(result)

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)

"""Precedent-closure notifications (work_plan.md §8.6).

Pushed to every commander immediately and individually — never batched
into a digest — whenever an event closes without running. Purely
informational: it carries the event, the precedent it matched, and how
that precedent ended, so the closure can be judged, but it is never
phrased as a question and needs no reply.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_header

if TYPE_CHECKING:
    from bot.api_client import PrecedentClosureNotice
    from bot.deps import BotDeps


def format_precedent_closure_notice(notice: "PrecedentClosureNotice") -> str:
    return (
        f"{format_header('precedent_closure')}\n\n"
        f"Event: {notice.raw_text}\n\n"
        f"Closed against precedent {notice.matched_precedent_event_id}, "
        f"which ended: {notice.precedent_ending}"
    )


async def notify_precedent_closure(deps: "BotDeps", notice: "PrecedentClosureNotice") -> None:
    text = format_precedent_closure_notice(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)

"""Persist the notification feed's read cursor across a bot restart
(work_plan.md §8.12's own forward-looking note).

`GET /Notifications` (§8.12) is a stateless, caller-supplied-cursor feed —
the API remembers nothing per caller, so nothing stops a bot restart from
either replaying everything already delivered (starting over at 0) or,
worse, silently losing track of where it was. A small file beside the
deployment's database — the same "beside the db, not beside the profile"
convention `config.settings_store` and `bot.startup` both already
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
