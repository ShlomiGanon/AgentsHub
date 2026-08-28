"""Public bot and terminal-integration facade."""

import sys

from bot import contracts
from bot.contracts import *

api_client = contracts
deps = contracts
sys.modules[f"{__name__}.api_client"] = contracts
sys.modules[f"{__name__}.deps"] = contracts

from bot import interactions

commands = interactions
formatting = interactions
holds = interactions
users = interactions
presentation = interactions
for legacy_name in ("commands", "formatting", "holds", "users"):
    sys.modules[f"{__name__}.{legacy_name}"] = interactions
sys.modules[f"{__name__}.presentation"] = interactions

from bot import transports
from bot.transports import HttpApiClient, PTBTelegramClient, TelegramClient, _do_request

http_api_client = transports
telegram_client = transports
client = transports
sys.modules[f"{__name__}.client"] = transports
sys.modules[f"{__name__}.http_api_client"] = transports
sys.modules[f"{__name__}.telegram_client"] = transports

from bot import background_services
from bot.background_services import NotificationCursorStore, SingleInstanceLock, dispatch_notification

notifications = background_services
startup = background_services
runtime = background_services
sys.modules[f"{__name__}.runtime"] = background_services
sys.modules[f"{__name__}.notifications"] = background_services
sys.modules[f"{__name__}.startup"] = background_services

from bot import app
from bot.app import handle_incoming_message

interface = sys.modules[__name__]
sys.modules[f"{__name__}.interface"] = sys.modules[__name__]

do_request = _do_request
format_approval_prompt = interactions.format_approval_prompt
handle_approval_answer = interactions.handle_approval_answer
handle_clarification_answer = interactions.handle_clarification_answer
parse_approval_callback_data = interactions.parse_callback_data
split_message = interactions.split_message

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
