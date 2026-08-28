"""Public bot and terminal-integration facade."""

import sys

from bot import contracts
from bot.contracts import *

api_client = contracts
deps = contracts
sys.modules[f"{__name__}.api_client"] = contracts
sys.modules[f"{__name__}.deps"] = contracts

from bot import presentation

commands = presentation
formatting = presentation
holds = presentation
users = presentation
for legacy_name in ("commands", "formatting", "holds", "users"):
    sys.modules[f"{__name__}.{legacy_name}"] = presentation

from bot import client
from bot.client import HttpApiClient, PTBTelegramClient, TelegramClient, _do_request

http_api_client = client
telegram_client = client
sys.modules[f"{__name__}.http_api_client"] = client
sys.modules[f"{__name__}.telegram_client"] = client

from bot import runtime
from bot.runtime import NotificationCursorStore, SingleInstanceLock, dispatch_notification

notifications = runtime
startup = runtime
sys.modules[f"{__name__}.notifications"] = runtime
sys.modules[f"{__name__}.startup"] = runtime

from bot import app
from bot.app import handle_incoming_message

interface = sys.modules[__name__]
sys.modules[f"{__name__}.interface"] = sys.modules[__name__]

do_request = _do_request
format_approval_prompt = presentation.format_approval_prompt
handle_approval_answer = presentation.handle_approval_answer
handle_clarification_answer = presentation.handle_clarification_answer
parse_approval_callback_data = presentation.parse_callback_data
split_message = presentation.split_message

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
