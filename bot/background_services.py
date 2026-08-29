"""Notification delivery, cursor persistence, and process locking."""

import asyncio

import logging

from typing import TYPE_CHECKING

from bot import interactions

from bot.contracts import (
    AlreadyRunningError,
    ApiNotImplementedError,
    ApiRequestError,
    BotError,
    BotStartupError,
)

from bot.interactions import format_failure_notice, format_header, format_job_result

from pathlib import Path

import os

if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification, PrecedentClosureNotice
    from bot.contracts import BotDeps

logger = logging.getLogger(__name__)


async def dispatch_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    if notification.kind == "clarification_hold":
        await interactions.push_clarification_prompt(deps, notification.payload)
        return

    if notification.kind == "approval_hold":
        await interactions.push_approval_prompt(deps, notification.payload)
        return

    if notification.kind == "event_data_hold":
        text = interactions.format_event_data_needed(notification.payload)
        for chat_id in notification.target_chat_ids:
            await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)
        return

    if notification.kind == "uncertain_verdict":
        await interactions.notify_uncertain_verdict(deps, notification.payload)
        return

    if notification.kind == "precedent_closure":
        await notify_precedent_closure(deps, notification.payload)
        return

    if notification.kind == "no_match_notice":
        await interactions.notify_no_match(deps, notification.payload)
        return

    if notification.kind == "job_finished":
        await deliver_job_result(deps, notification)
        return

    if notification.kind == "job_failed":
        await deliver_failure_notification(deps, notification)
        return

    raise ValueError(f"unknown notification kind: {notification.kind!r}")


async def run_notification_poll_once(deps: "BotDeps", since: int = 0, wait_seconds: int = 0) -> tuple[int, int]:
    """Fetch and dispatch whatever is pending since `since`."""

    if wait_seconds:
        notifications, next_cursor = await deps.api_client.poll_pending_notifications(since, wait_seconds)
    else:
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
    """Repeatedly call `run_notification_poll_once`, forever by default (`max_iterations=None`), or a fixed number of times — for tests."""

    cursor = cursor_store.read() if cursor_store is not None else 0
    iterations = 0
    policy = getattr(deps.loaded_profile, "optimization_policy", None)
    wait_seconds = getattr(policy, "notification_wait_seconds", 0)
    transport_backoff = 0.5

    while max_iterations is None or iterations < max_iterations:
        failed_transport = False
        try:
            _count, cursor = await run_notification_poll_once(deps, cursor, wait_seconds)
            transport_backoff = 0.5
            if cursor_store is not None:
                cursor_store.write(cursor)
        except ApiNotImplementedError as exc:
            logger.info("notification poll skipped: %s", exc, extra={"event": "notification_poll_not_implemented"})
        except ApiRequestError:
            failed_transport = True
            logger.exception("notification transport failed; reconnecting", extra={"event": "notification_transport_failed"})
        except Exception:
            logger.exception("notification poll failed; continuing", extra={"event": "notification_poll_failed"})

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            if failed_transport:
                await asyncio.sleep(transport_backoff)
                transport_backoff = min(30.0, transport_backoff * 2)
            elif wait_seconds == 0:
                await asyncio.sleep(poll_interval_seconds)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification


async def deliver_failure_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    notice = notification.payload
    text = format_failure_notice(notice)

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification


async def deliver_job_result(deps: "BotDeps", notification: "BotNotification") -> None:
    job_result = notification.payload
    text = format_job_result(job_result)

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, PrecedentClosureNotice


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


