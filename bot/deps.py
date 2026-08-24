"""Shared bot dependencies (work_plan.md §8).

One bundle passed to every handler module, mirroring
`orchestrator.flows.FlowDeps`'s pattern — a single object to construct
once at startup (`bot.app`) and thread through, rather than each module
reaching for a global.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.api_client import BotApiClient
    from bot.telegram_client import TelegramClient
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class BotDeps:
    loaded_profile: "LoadedProfile"
    telegram_client: "TelegramClient"
    api_client: "BotApiClient"
