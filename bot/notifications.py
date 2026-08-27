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

from bot import approval, clarification, precedent_notify
from bot.errors import ApiNotImplementedError
from bot.failures import deliver_failure_notification
from bot.results import deliver_job_result

if TYPE_CHECKING:
    from bot.api_client import BotNotification
    from bot.deps import BotDeps
    from bot.notification_cursor import NotificationCursorStore

logger = logging.getLogger(__name__)


async def dispatch_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    if notification.kind == "clarification_hold":
        await clarification.push_clarification_prompt(deps, notification.payload)
        return

    if notification.kind == "approval_hold":
        await approval.push_approval_prompt(deps, notification.payload)
        return

    if notification.kind == "uncertain_verdict":
        await approval.notify_uncertain_verdict(deps, notification.payload)
        return

    if notification.kind == "precedent_closure":
        await precedent_notify.notify_precedent_closure(deps, notification.payload)
        return

    if notification.kind == "no_match_notice":
        await approval.notify_no_match(deps, notification.payload)
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
    `bot.notification_cursor`'s own docstring for why a restart needs
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
