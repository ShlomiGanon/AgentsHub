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

from bot.interactions import (
    format_failure_notice,
    format_header,
    format_job_result,
    message_catalog_for,
)

from pathlib import Path
import random
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
        text = interactions.format_event_data_needed(notification.payload, message_catalog_for(deps))
        for chat_id in notification.target_chat_ids:
            prompt_message_id = await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)
            if prompt_message_id is not None:
                interactions.register_event_data_reply_target(
                    chat_id, prompt_message_id, notification.payload.event_id
                )
        return

    if notification.kind == "uncertain_verdict":
        await interactions.notify_uncertain_verdict(deps, notification.payload)
        return

    if notification.kind == "uncertain_verdict_reporter":
        text = interactions.format_uncertain_verdict_reporter_notice(message_catalog_for(deps))
        for chat_id in notification.target_chat_ids:
            await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)
        return

    if notification.kind == "precedent_closure":
        await notify_precedent_closure(deps, notification.payload)
        return

    if notification.kind == "no_match_notice":
        await interactions.notify_no_match(deps, notification.payload)
        return

    if notification.kind == "job_finished":
        interactions.unregister_event_data_reply_target(notification.payload.job_id)
        await deliver_job_result(deps, notification)
        return

    if notification.kind == "job_failed":
        interactions.unregister_event_data_reply_target(notification.payload.event_id)
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
        try:
            await dispatch_notification(deps, notification)
        except Exception:
            logger.exception(
                "notification dispatch failed; continuing to next",
                extra={"event": "notification_dispatch_failed", "kind": notification.kind},
            )

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
    current_backoff = 1.0

    while max_iterations is None or iterations < max_iterations:
        had_error = False
        try:
            _count, cursor = await run_notification_poll_once(deps, cursor, wait_seconds)
            current_backoff = 1.0
            if cursor_store is not None:
                cursor_store.write(cursor)
        except asyncio.CancelledError:
            logger.info("notification poll loop cancelled; stopping gracefully", extra={"event": "notification_poll_cancelled"})
            break
        except ApiNotImplementedError as exc:
            logger.info("notification poll skipped: %s", exc, extra={"event": "notification_poll_not_implemented"})
        except ApiRequestError:
            had_error = True
            logger.exception("notification transport failed; reconnecting with backoff", extra={"event": "notification_transport_failed"})
        except Exception:
            had_error = True
            logger.exception("notification poll failed; continuing with backoff", extra={"event": "notification_poll_failed"})

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            if had_error:
                jitter = random.uniform(0.8, 1.2)
                sleep_duration = min(30.0, current_backoff * jitter)
                await asyncio.sleep(sleep_duration)
                current_backoff = min(30.0, current_backoff * 2.0)
            elif wait_seconds == 0:
                await asyncio.sleep(poll_interval_seconds)


# -- group attendance checks ---------------------------------------------------

ATTENDANCE_CALLBACK_PREFIX = "attend"


def attendance_buttons(messages) -> tuple[tuple[str, str], ...]:
    """The two inline buttons under a group attendance prompt: (label, callback_data)."""

    return (
        (messages.text("attendance.button_available"), f"{ATTENDANCE_CALLBACK_PREFIX}:available"),
        (messages.text("attendance.button_unavailable"), f"{ATTENDANCE_CALLBACK_PREFIX}:unavailable"),
    )


def _local_clock_time(deadline_iso: str | None, timezone_name: str | None) -> str:
    """`HH:MM` in the deployment's timezone, or the raw value when it cannot be parsed."""

    if not deadline_iso:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        parsed = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
        if timezone_name:
            parsed = parsed.astimezone(ZoneInfo(timezone_name))
        return parsed.strftime("%H:%M")
    except (ValueError, KeyError):
        return deadline_iso


def format_attendance_prompt(deps: "BotDeps", result) -> str:
    messages = message_catalog_for(deps)
    if not result.members_required:
        return messages.text("attendance.group_prompt_nobody")
    deadline = _local_clock_time(result.deadline_at, getattr(deps.loaded_profile, "timezone_name", None))
    members = "\n".join(f"- {name}" for name in result.members_required)
    return messages.text("attendance.group_prompt", deadline=deadline, members=members)


async def run_attendance_check_once(deps: "BotDeps") -> int:
    """Ask the server to open today's cycle if due; when it did, post the prompt with buttons to every group bound to the attendance agent. Returns how many groups were prompted."""

    result = await deps.api_client.run_attendance_check()
    if not result.opened:
        return 0
    if not result.target_chat_ids:
        logger.warning(
            "attendance cycle opened but no telegram group is bound to the attendance agent",
            extra={"event": "attendance_no_target_group", "cycle_key": result.cycle_key, "agent": result.agent_name},
        )
        return 0
    prompt = format_attendance_prompt(deps, result)
    buttons = attendance_buttons(message_catalog_for(deps))
    prompted = 0
    for chat_id in result.target_chat_ids:
        try:
            await deps.telegram_client.send_with_buttons(chat_id, prompt, buttons)
            prompted += 1
        except Exception:
            logger.exception(
                "could not post attendance prompt to group; continuing",
                extra={"event": "attendance_prompt_failed", "chat_id": chat_id, "cycle_key": result.cycle_key},
            )
    logger.info(
        "attendance prompt posted",
        extra={"event": "attendance_prompt_posted", "cycle_key": result.cycle_key, "groups": prompted},
    )
    return prompted


async def run_attendance_check_loop(
    deps: "BotDeps",
    poll_interval_seconds: float = 60.0,
    max_iterations: int | None = None,
) -> None:
    """Repeatedly call `run_attendance_check_once`, forever by default, or a fixed number of times — for tests.

    The server decides whether a cycle is due (once per local day, after the
    profile's attendance hour); this loop only asks and delivers, so restarting
    the bot never opens a second cycle."""

    iterations = 0
    current_backoff = 1.0

    while max_iterations is None or iterations < max_iterations:
        had_error = False
        try:
            await run_attendance_check_once(deps)
            current_backoff = 1.0
        except asyncio.CancelledError:
            logger.info("attendance check loop cancelled; stopping gracefully", extra={"event": "attendance_loop_cancelled"})
            break
        except ApiNotImplementedError as exc:
            logger.info("attendance check skipped: %s", exc, extra={"event": "attendance_check_not_implemented"})
        except ApiRequestError as exc:
            if exc.status_code == 404:
                # This deployment has no attendance specialist: nothing to do, ever. Stay quiet.
                logger.info("attendance check unavailable in this deployment; loop stopping", extra={"event": "attendance_check_unavailable"})
                break
            had_error = True
            logger.exception("attendance check transport failed; retrying with backoff", extra={"event": "attendance_check_transport_failed"})
        except Exception:
            had_error = True
            logger.exception("attendance check failed; continuing with backoff", extra={"event": "attendance_check_failed"})

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            if had_error:
                jitter = random.uniform(0.8, 1.2)
                await asyncio.sleep(min(60.0, current_backoff * jitter))
                current_backoff = min(60.0, current_backoff * 2.0)
            else:
                await asyncio.sleep(poll_interval_seconds)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification


async def deliver_failure_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    notice = notification.payload
    text = format_failure_notice(notice, message_catalog_for(deps))

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification


async def deliver_job_result(deps: "BotDeps", notification: "BotNotification") -> None:
    job_result = notification.payload
    text = format_job_result(job_result, message_catalog_for(deps))

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, PrecedentClosureNotice


def format_precedent_closure_notice(notice: "PrecedentClosureNotice", catalog=None) -> str:
    messages = catalog or interactions._catalog()
    return messages.text(
        "notice.precedent",
        header=format_header("precedent_closure", messages),
        raw_text=notice.raw_text,
        precedent_id=notice.matched_precedent_event_id,
        ending=interactions._outcome_word(notice.precedent_ending, messages),
    )


async def notify_precedent_closure(deps: "BotDeps", notice: "PrecedentClosureNotice") -> None:
    text = format_precedent_closure_notice(notice, message_catalog_for(deps))

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


