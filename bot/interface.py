"""Supported bot integration surface for the terminal frontends."""

from bot.api_client import BOT_SERVICE_IDENTITY
from bot.holds import (
    format_approval_prompt,
    handle_approval_answer,
    handle_clarification_answer,
    parse_callback_data as parse_approval_callback_data,
)
from bot.deps import BotDeps
from bot.app import handle_incoming_message
from bot.startup import ApiRequestError, BotError
from bot.formatting import split_message
from bot.http_api_client import HttpApiClient, _do_request as do_request
from bot.notifications import NotificationCursorStore, dispatch_notification
from bot.telegram_client import TelegramClient

__all__ = [
    "ApiRequestError",
    "BOT_SERVICE_IDENTITY",
    "BotDeps",
    "BotError",
    "HttpApiClient",
    "NotificationCursorStore",
    "TelegramClient",
    "dispatch_notification",
    "do_request",
    "format_approval_prompt",
    "handle_approval_answer",
    "handle_clarification_answer",
    "handle_incoming_message",
    "parse_approval_callback_data",
    "split_message",
]
