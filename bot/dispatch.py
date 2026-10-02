"""Message dispatch: caller cache, /Msg submission, and live-trace polling."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from auth.permissions import PermissionLevel
from bot import interactions
from bot.contracts import ApiNotImplementedError, ApiRequestError, BotDeps, GroupBindingView, MessageSubmissionResult
from bot.interactions import resolve_caller
from bot.presentation import replace_status
from bot.runtime_state import (
    GROUP_CHAT_TYPES,
    _GROUP_BINDINGS_CACHE,
    _GROUP_BINDINGS_CACHE_TTL_SECONDS,
    _USER_ROLE_CACHE,
    _USER_ROLE_CACHE_TTL_SECONDS,
    _background_trace_tasks,
)
from tools import deep_debug_enabled, get_trace_id, new_trace_id, set_trace_id

if TYPE_CHECKING:
    from bot.contracts import GroupBindingView

logger = logging.getLogger(__name__)

async def _group_binding_cached(api_client, chat_id: str) -> GroupBindingView | None:
    """The binding for `chat_id`, from a TTL-cached copy of the server's routing table."""

    now = time.monotonic()
    key = id(api_client)
    cached = _GROUP_BINDINGS_CACHE.get(key)
    if cached is None or now >= cached[1]:
        bindings = {binding.chat_id: binding for binding in await api_client.list_groups()}
        _GROUP_BINDINGS_CACHE[key] = (bindings, now + _GROUP_BINDINGS_CACHE_TTL_SECONDS)
    else:
        bindings = cached[0]
    return bindings.get(str(chat_id))

def _chat_type(update) -> str:
    """Telegram chat.type for this update, defaulting to private."""

    chat = getattr(update, "effective_chat", None)
    return str(getattr(chat, "type", None) or "private")

async def _group_is_handled(deps: BotDeps, update) -> bool:
    """False when the update comes from a group the server has no binding for — the bot stays silent there (and logs), by design."""

    chat_type = _chat_type(update)
    if chat_type not in GROUP_CHAT_TYPES:
        return True
    chat_id = str(update.effective_chat.id)
    binding = await _group_binding_cached(deps.api_client, chat_id)
    if binding is None:
        logger.info(
            "ignoring update from unregistered telegram group",
            extra={"event": "bot_group_unregistered", "chat_id": chat_id, "chat_type": chat_type},
        )
        return False
    return True

async def _resolve_caller_cached(
    api_client, telegram_identity: str, messages
) -> interactions.UserResolutionResult:
    """Resolve caller role with in-memory TTL caching to eliminate redundant GET /User round-trips."""
    now = time.monotonic()
    key = (id(api_client), telegram_identity)
    cached = _USER_ROLE_CACHE.get(key)
    if cached is not None:
        resolution, expires_at = cached
        if now < expires_at:
            return resolution

    resolution = await resolve_caller(api_client, telegram_identity, messages)
    ttl = _USER_ROLE_CACHE_TTL_SECONDS if resolution.status == "ok" else 5.0
    _USER_ROLE_CACHE[key] = (resolution, now + ttl)
    return resolution

async def handle_incoming_message(
    deps: BotDeps,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None = None,
    event_data_event_id: str | None = None,
) -> str:
    """Route free-form Telegram text through the single message endpoint."""

    reply, _submission = await _submit_and_format_message(
        deps,
        telegram_identity,
        text,
        message_id,
        conversation_id,
        event_data_event_id=event_data_event_id,
    )
    return reply

async def _submit_and_format_message(
    deps: BotDeps,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None,
    trace_id: str | None = None,
    event_data_event_id: str | None = None,
    protocol_hint: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_chat_type: str | None = None,
    ack_message_id: str | None = None,
) -> tuple[str, MessageSubmissionResult | None]:
    """Submit one message and return both presentation text and semantic result."""

    try:
        submission_result = await deps.api_client.submit_message(
            text,
            telegram_identity,
            message_id,
            conversation_id,
            trace_id,
            event_data_event_id,
            protocol_hint,
            telegram_chat_id=telegram_chat_id,
            telegram_chat_type=telegram_chat_type,
            ack_message_id=ack_message_id,
        )
    except ApiRequestError as exc:
        messages = interactions.message_catalog_for(deps)
        if event_data_event_id is not None:
            interactions.unregister_event_data_reply_target(event_data_event_id)
        if exc.status_code == 401:
            return interactions._unregistered_message(telegram_identity, messages), None
        if exc.status_code == 403:
            return messages.text("bot.refused", message=exc.message), None
        raise
    messages = interactions.message_catalog_for(deps)
    if event_data_event_id is not None:
        interactions.unregister_event_data_reply_target(event_data_event_id)

    # Pure relay for every kind (docs/work_process.md §17): /Msg now sends a
    # ready-to-display `answer` for every kind, including a queued report/request
    # (server-side `api.queued_report`/`api.queued_request`, DEEP_DEBUG-gated
    # there — api/routes.py's `_queued_answer_text`) — the same shape
    # question/conversational/clarification/event_update's `answer` already had.
    # `bot.no_answer` is only a safety net for the (never expected) case of a
    # missing answer, not a real formatting branch. There used to be an
    # `awaiting_approval`-gated append + a `register_open_approval_hold` call
    # here — removed (docs/work_process.md §18): /Msg's synchronous response can
    # never actually know a queued report/request will later be held for
    # approval (that's discovered asynchronously, well after this reply is
    # sent), so both were dead code, never reachable via the real
    # `HttpApiClient`. The real, working path is the `approval_hold`
    # notification (`bot/interactions.py`'s `push_approval_prompt`, which
    # already calls `register_open_approval_hold` correctly, on its own,
    # untouched by this).
    return submission_result.answer_text or messages.text("bot.no_answer"), submission_result

async def _poll_live_trace(
    deps: BotDeps,
    chat_id: str,
    telegram_identity: str,
    trace_id: str,
    stop_when_idle: asyncio.Event,
) -> None:
    """Stream Deep Debug messages for one trace until it ends or the stop event is set."""

    cursor = 0
    while True:
        stopping = stop_when_idle.is_set()
        result = await deps.api_client.poll_trace(
            trace_id,
            cursor,
            0 if stopping else 5,
            telegram_identity,
        )
        cursor = result.next_cursor
        for debug_message in result.messages:
            await deps.telegram_client.send_text(chat_id, debug_message)
        if result.terminal or stopping:
            return

def _keep_trace_task(task: asyncio.Task) -> None:
    """Retain a live-trace task until it finishes so it is not garbage-collected."""

    _background_trace_tasks.add(task)

    def _finished(completed: asyncio.Task) -> None:
        """Drop the finished task and log if it failed."""

        _background_trace_tasks.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            logger.warning(
                "live Deep Debug polling stopped after an error: %s",
                completed.exception(),
                extra={"event": "deep_debug_poll_failed"},
            )

    task.add_done_callback(_finished)

async def present_incoming_message(
    deps: BotDeps,
    chat_id: str,
    telegram_identity: str,
    text: str,
    message_id: str,
    conversation_id: str | None = None,
    event_data_event_id: str | None = None,
    protocol_hint: str | None = None,
    telegram_chat_type: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    """Present one free-form message with the shared status/edit lifecycle.

    `telegram_chat_type` (Telegram's `chat.type`) is forwarded with `chat_id` so
    the server can scope a bound group's message; None means "not known", which
    the server treats like a private chat."""

    messages = interactions.message_catalog_for(deps)
    status_message_id = await deps.telegram_client.send_status(
        chat_id,
        messages.text("status.thinking"),
        reply_to_message_id=message_id,
    )
    resolved_trace_id = trace_id or get_trace_id() or new_trace_id()
    set_trace_id(resolved_trace_id)
    trace_id = resolved_trace_id
    trace_stop = asyncio.Event()
    trace_task: asyncio.Task | None = None
    submission: MessageSubmissionResult | None = None
    try:
        if deep_debug_enabled():
            caller_res = await _resolve_caller_cached(deps.api_client, telegram_identity, messages)
            if caller_res.caller and caller_res.caller.level == PermissionLevel.COMMANDER:
                trace_task = asyncio.create_task(
                    _poll_live_trace(deps, chat_id, telegram_identity, trace_id, trace_stop)
                )

        reply, submission = await _submit_and_format_message(
            deps,
            telegram_identity,
            text,
            message_id,
            conversation_id,
            trace_id,
            event_data_event_id,
            protocol_hint,
            telegram_chat_id=chat_id if telegram_chat_type is not None else None,
            telegram_chat_type=telegram_chat_type,
            ack_message_id=status_message_id,
        )
    except ApiNotImplementedError as exc:
        logger.info(
            "message request blocked on unimplemented API: %s",
            exc,
            extra={"event": "bot_api_not_implemented"},
        )
        reply = messages.text("bot.not_available", reason=exc)
    except ApiRequestError as exc:
        # "run_failure" (RunFailureError, 422) is the one API error class this codebase always
        # raises from a raw internal/model-produced string (api/routes.py wraps
        # OrchestrationParseError verbatim) rather than a deliberately-crafted, already-localized
        # catalog message — the only class genuinely unsafe to show a caller directly. Every other
        # ApiError subclass (InvalidInputError, NotFoundError, ConflictError,
        # ServiceUnavailableError, ...) is raised with real catalog text throughout this codebase
        # and stays exactly as informative as before (docs/IMPROVES/CRITICAL_FIXES_PLAN.MD item 4).
        if exc.error_class == "run_failure":
            reply = messages.text("error.run_failure_generic")
        else:
            reply = messages.text("error.request_failed", reason=exc.message)
    except Exception:
        logger.exception("unhandled error in message request", extra={"event": "bot_handler_failed"})
        reply = messages.text("bot.handler_error")
        submission = None

    await replace_status(deps.telegram_client, chat_id, status_message_id, reply)
    if trace_task is not None:
        if submission is not None and submission.job_id:
            _keep_trace_task(trace_task)
        else:
            trace_stop.set()
            try:
                await trace_task
            except Exception:
                logger.warning(
                    "live Deep Debug polling stopped after an error",
                    exc_info=True,
                    extra={"event": "deep_debug_poll_failed"},
                )
    return reply
