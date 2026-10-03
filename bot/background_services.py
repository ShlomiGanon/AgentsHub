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
    TELEGRAM_MESSAGE_LIMIT,
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


async def _sleep_backoff_or_interval(
    *,
    had_error: bool,
    current_backoff: float,
    cap: float,
    interval: float,
    skip_interval: bool = False,
) -> float:
    """Sleep the error-backoff (capped) or the steady poll interval. Returns the next backoff."""

    if had_error:
        jitter = random.uniform(0.8, 1.2)
        await asyncio.sleep(min(cap, current_backoff * jitter))
        return min(cap, current_backoff * 2.0)
    if not skip_interval:
        await asyncio.sleep(interval)
    return current_backoff


async def _for_each_target_chat(chat_ids: list[str], send) -> None:
    """Run `send(chat_id)` for each target chat, in the existing iteration order."""

    for chat_id in chat_ids:
        await send(chat_id)


# --- notifications ---

async def dispatch_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    """Route one notification kind to the matching Telegram delivery helper."""

    if notification.kind == "clarification_hold":
        await interactions.push_clarification_prompt(deps, notification.payload)
        return

    if notification.kind == "approval_hold":
        await interactions.push_approval_prompt(deps, notification.payload)
        return

    if notification.kind == "event_data_hold":
        text = interactions.format_event_data_needed(notification.payload, message_catalog_for(deps))

        async def _send_event_data(chat_id: str) -> None:
            """Send the extra-data prompt and remember its message id for a later reply."""

            prompt_message_id = await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)
            if prompt_message_id is not None:
                interactions.register_event_data_reply_target(
                    chat_id, prompt_message_id, notification.payload.event_id
                )

        await _for_each_target_chat(notification.target_chat_ids, _send_event_data)
        return

    if notification.kind == "uncertain_verdict":
        await interactions.notify_uncertain_verdict(deps, notification.payload)
        return

    if notification.kind == "resource_unavailable_alert":
        await interactions.notify_resource_unavailable_alert(deps, notification.payload)
        return

    if notification.kind == "hold_escalation":
        await interactions.notify_hold_escalation(deps, notification.payload)
        return

    if notification.kind == "uncertain_verdict_reporter":
        text = interactions.format_uncertain_verdict_reporter_notice(message_catalog_for(deps))
        await _for_each_target_chat(
            notification.target_chat_ids,
            lambda chat_id: deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id),
        )
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

    try:
        await run_attendance_check_once(deps)
    except ApiNotImplementedError:
        pass
    except ApiRequestError as exc:
        if exc.status_code != 404:
            logger.exception(
                "attendance claim after notification poll failed",
                extra={"event": "attendance_claim_failed", "status": exc.status_code},
            )
    except Exception:
        logger.exception(
            "attendance claim after notification poll failed",
            extra={"event": "attendance_claim_failed"},
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
            current_backoff = await _sleep_backoff_or_interval(
                had_error=had_error,
                current_backoff=current_backoff,
                cap=30.0,
                interval=poll_interval_seconds,
                skip_interval=wait_seconds != 0,
            )


# --- attendance ---

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
    """Group-chat text asking named members to mark today's attendance."""

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
    hour on the enabled attendance group); this loop only asks and delivers, so
    restarting the bot never opens a second cycle. Force-opens are also claimed
    from the notification long-poll so they do not wait for this timer."""

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
            current_backoff = await _sleep_backoff_or_interval(
                had_error=had_error,
                current_backoff=current_backoff,
                cap=60.0,
                interval=poll_interval_seconds,
            )


# --- delivery ---

if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification


async def _deliver_editing_the_ack_first(deps: "BotDeps", chat_id: str, text: str, notification: "BotNotification") -> None:
    """Edit the ack in place when it still fits; otherwise send a new reply to the original."""

    if notification.ack_message_id and len(text) <= TELEGRAM_MESSAGE_LIMIT:
        try:
            await deps.telegram_client.edit_status(chat_id, notification.ack_message_id, text)
            return
        except Exception as exc:
            logger.warning(
                "editing the ack message failed; sending a new reply instead",
                extra={"event": "ack_edit_failed", "reason": str(exc)},
            )

    await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)


async def deliver_failure_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    """Send a failure as a new reply so the user is pinged; best-effort edit the ack to a neutral line."""

    notice = notification.payload
    text = notice.report_text or format_failure_notice(notice, message_catalog_for(deps))
    messages = message_catalog_for(deps)

    async def _send_failure(chat_id: str) -> None:
        """Edit the ack if possible, then send the failure as a new reply."""

        if notification.ack_message_id:
            try:
                await deps.telegram_client.edit_status(chat_id, notification.ack_message_id, messages.text("failure.ack_not_completed"))
            except Exception as exc:
                logger.warning(
                    "editing the ack message to the neutral failure line failed; sending the real reply regardless",
                    extra={"event": "ack_edit_failed", "reason": str(exc)},
                )
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)

    await _for_each_target_chat(notification.target_chat_ids, _send_failure)


if TYPE_CHECKING:
    from bot.contracts import BotDeps, BotNotification


async def deliver_job_result(deps: "BotDeps", notification: "BotNotification") -> None:
    """Deliver a finished job by editing the ack first, then sending the result."""

    job_result = notification.payload
    text = job_result.report_text or format_job_result(job_result, message_catalog_for(deps))

    await _for_each_target_chat(
        notification.target_chat_ids,
        lambda chat_id: _deliver_editing_the_ack_first(deps, chat_id, text, notification),
    )


if TYPE_CHECKING:
    from bot.contracts import BotDeps, PrecedentClosureNotice


def format_precedent_closure_notice(notice: "PrecedentClosureNotice", catalog=None) -> str:
    """Commander notice that this report closed on a matching precedent."""

    messages = catalog or interactions._catalog()
    return messages.text(
        "notice.precedent",
        header=format_header("precedent_closure", messages),
        raw_text=notice.raw_text,
        precedent_id=notice.matched_precedent_event_id,
        ending=interactions._outcome_word(notice.precedent_ending, messages),
    )


async def notify_precedent_closure(deps: "BotDeps", notice: "PrecedentClosureNotice") -> None:
    """Send the precedent-closure notice to every commander private chat."""

    text = format_precedent_closure_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


class NotificationCursorStore:
    """Persists the notification poll cursor across bot process restarts."""

    def __init__(self, path: Path):
        """Remember the file that holds the integer cursor."""

        self._path = path

    def read(self) -> int:
        """The stored cursor, or 0 when the file is missing or unreadable."""

        try:
            return int(self._path.read_text().strip())
        except (FileNotFoundError, ValueError):
            # No file yet (first-ever run), or a corrupted/partial write —
            # either way, starting over at 0 means "may redeliver a few
            # notifications once," never "silently skip real ones," which
            # is the safer failure direction.
            return 0

    def write(self, cursor: int) -> None:
        """Replace the stored cursor with this integer."""

        self._path.write_text(str(cursor))











# --- process lock ---

def _pid_is_running(pid: int) -> bool:
    """True when `pid` still names a live process. Used to tell a crashed bot's leftover
    lock file from a second live instance of the same deployment."""

    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        process_query_limited_information = 0x1000
        still_active = 259
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) == 0:
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_lock_pid(lock_path: Path) -> int | None:
    """PID recorded in the lock file, or None if it cannot be read."""

    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


class SingleInstanceLock:
    """File lock so only one bot process runs against this profile at a time."""

    def __init__(self, lock_path: Path):
        """Remember the lock-file path for this process."""

        self._lock_path = lock_path
        self._fd: int | None = None

    def _reclaim_stale(self) -> bool:
        """True after removing a lock whose recorded PID is no longer running."""

        holder = _read_lock_pid(self._lock_path)
        if holder is not None and _pid_is_running(holder):
            return False
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def acquire(self) -> None:
        """Take the lock, reclaiming a stale file, or raise if another live process holds it."""

        try:
            self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if not self._reclaim_stale():
                raise AlreadyRunningError(
                    f"a bot process for this deployment appears to already be running "
                    f"(lock file exists: {self._lock_path}); if the previous process crashed "
                    f"without cleaning up, remove that file manually before restarting"
                ) from exc
            try:
                self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as retry_exc:
                raise AlreadyRunningError(
                    f"a bot process for this deployment appears to already be running "
                    f"(lock file exists: {self._lock_path}); if the previous process crashed "
                    f"without cleaning up, remove that file manually before restarting"
                ) from retry_exc

        os.write(self._fd, str(os.getpid()).encode("utf-8"))

    def release(self) -> None:
        """Remove the lock file if this process created it."""

        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

        self._lock_path.unlink(missing_ok=True)

    def __enter__(self) -> "SingleInstanceLock":
        """Acquire the lock for a with-block."""

        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        """Release the lock when the with-block ends."""

        self.release()


