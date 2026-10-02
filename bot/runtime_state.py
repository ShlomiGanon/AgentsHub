"""In-memory bot caches shared by dispatch and handlers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

_USER_ROLE_CACHE: dict[tuple[int, str], tuple[object, float]] = {}
_USER_ROLE_CACHE_TTL_SECONDS = 60.0
_PENDING_NAME_ACTION_TTL_SECONDS = 3600.0
_GROUP_BINDINGS_CACHE: dict[int, tuple[dict, float]] = {}
_GROUP_BINDINGS_CACHE_TTL_SECONDS = 60.0
_background_trace_tasks: set[asyncio.Task] = set()
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})
REGISTERED_COMMANDS = ("profile", "settings")
NOTIFICATION_POLL_INTERVAL_SECONDS = 5.0
ATTENDANCE_CHECK_INTERVAL_SECONDS = 60.0


@dataclass
class PendingNameAction:
    """A Telegram update waiting for the caller to supply their full name."""

    handler: Callable[..., Awaitable[None]]
    update: object
    context: object
    created_at: float = field(default_factory=time.monotonic)


PENDING_NAME_ACTIONS: dict[tuple[str, str], PendingNameAction] = {}
_PENDING_UNAVAILABILITY: dict[tuple[str, str], dict] = {}


def clear_caller_cache() -> None:
    """Clear in-memory caller resolution cache (for test isolation or admin resets)."""

    _USER_ROLE_CACHE.clear()
    _GROUP_BINDINGS_CACHE.clear()
    PENDING_NAME_ACTIONS.clear()
    _PENDING_UNAVAILABILITY.clear()
