"""Bot process wiring: profile load, token, clients, polling entry."""

import argparse
import importlib
import logging
import os
from pathlib import Path

from config import ModelTierError, TierModel, resolve_tier_model_from_env
from profiles.loader import LoadedProfile, ProfileLoadError, ProfileValidationError, load_profile
from tools import configure_logging

from bot.background_services import SingleInstanceLock
from bot.contracts import BotDeps, BotStartupError, resolve_bot_service_key
from bot.handlers import register_handlers
from bot.transports import HttpApiClient, PTBTelegramClient

logger = logging.getLogger(__name__)

_INVALID_TOKEN_MESSAGE = (
    "Telegram rejected the configured bot token — check the value of the "
    "environment variable named by BOT_TOKEN_ENV in the active profile"
)

def _resolve_bot_token(module_path: str, loaded_profile: LoadedProfile) -> str | None:
    """The bot token named by the profile's BOT_TOKEN_ENV, already resolved at profile load."""

    profile_module = importlib.import_module(module_path)
    token_env_name = profile_module.BOT_TOKEN_ENV
    token = loaded_profile.resolved_secrets[token_env_name]

    if not token.strip():
        logger.warning(
            f"Bot token not found: environment variable {token_env_name}, as configured in "
            "BOT_TOKEN_ENV, is not set — Telegram connection skipped",
            extra={"event": "bot_token_missing", "env_var": token_env_name},
        )
        return None

    return token

def build_deps(module_path: str, core_model: TierModel, sub_model: TierModel) -> BotDeps | None:
    """Build BotDeps, or None when the configured bot token is missing."""

    loaded_profile = load_profile(module_path, core_model=core_model, sub_model=sub_model)
    configure_logging(loaded_profile.module_path)

    bot_token = _resolve_bot_token(module_path, loaded_profile)
    if bot_token is None:
        return None

    telegram_client = PTBTelegramClient(bot_token)

    bot_service_key = resolve_bot_service_key()
    if not bot_service_key:
        logger.warning(
            "BOT_SERVICE_KEY is not set — every call this bot makes as its own service "
            "identity (notification delivery, the commander roster, profile-change checks, "
            "resolving a Telegram user) will be rejected by the API",
            extra={"event": "bot_service_key_missing"},
        )
    api_client = HttpApiClient(f"http://localhost:{loaded_profile.api_port}", bot_service_key=bot_service_key)

    return BotDeps(loaded_profile=loaded_profile, telegram_client=telegram_client, api_client=api_client)

async def _validate_bot_token(deps: BotDeps) -> None:
    """True when Telegram accepts the token; unused by main because a second event loop would close the PTB client."""
    if not await deps.telegram_client.validate_token():
        raise BotStartupError(_INVALID_TOKEN_MESSAGE)

def run_bot(deps: BotDeps) -> None:
    """Start Telegram polling with this process's handlers."""

    deps.telegram_client.run_polling(lambda application: register_handlers(application, deps))

def _tier_model_from_environ(prefix: str) -> TierModel:
    """Read one model tier from the process environment."""

    return resolve_tier_model_from_env(prefix, error_type=ModelTierError)

def main(argv: list[str] | None = None) -> None:
    """Load the named profile, take the single-instance lock, and start Telegram polling."""

    parser = argparse.ArgumentParser(description="Run the Telegram bot frontend for one deployment.")
    parser.add_argument("profile_module", help="dotted module path of the profile to run, e.g. profiles.response_team")
    args = parser.parse_args(argv)

    try:
        core_model = _tier_model_from_environ("CORE")
        sub_model = _tier_model_from_environ("SUB")
    except ModelTierError as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

    try:
        bot_dependencies = build_deps(args.profile_module, core_model=core_model, sub_model=sub_model)
    except (ProfileLoadError, ProfileValidationError) as exc:
        raise SystemExit(f"failed to start bot: {exc}") from exc

    if bot_dependencies is None:
        return

    lock = SingleInstanceLock(Path(f"{bot_dependencies.loaded_profile.db_path}.bot.lock"))

    try:
        lock.acquire()
    except BotStartupError as exc:
        raise SystemExit(str(exc)) from exc

    # Token validity is verified by run_polling()'s own bootstrap (Application.initialize()
    # calls Bot.get_me()) rather than by a separate asyncio.run(_validate_bot_token(...))
    # pre-check here — see _validate_bot_token's docstring for why running that in its own
    # event loop before run_polling() breaks the Telegram client's async HTTP client.
    from telegram.error import InvalidToken

    try:
        run_bot(bot_dependencies)
    except InvalidToken as exc:
        raise SystemExit(_INVALID_TOKEN_MESSAGE) from exc
    finally:
        lock.release()
