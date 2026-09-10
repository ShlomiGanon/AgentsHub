"""Public configuration facade."""

import sys

from config import environment, live_settings
from config.environment import (
    BaseConfig,
    DEEP_DEBUG,
    DEBUG_FLAG,
    LOG_CONSOLE_JSON_ENABLED,
    ModelTierError,
    TierModel,
    build_tier_model,
    load_base_config,
    resolve_tier_model_from_env,
)
from config.live_settings import SettingsStore

base = environment
models = environment
settings = live_settings
settings_store = live_settings
sys.modules[f"{__name__}.base"] = environment
sys.modules[f"{__name__}.models"] = environment
sys.modules[f"{__name__}.settings"] = live_settings
sys.modules[f"{__name__}.settings_store"] = live_settings

from config.server_control import (
    ProfileInfo,
    discover_profiles,
    read_status as read_server_status,
    submit_command as submit_server_command,
    supervisor_available,
)

__all__ = [
    "BaseConfig",
    "DEEP_DEBUG",
    "DEBUG_FLAG",
    "LOG_CONSOLE_JSON_ENABLED",
    "ModelTierError",
    "ProfileInfo",
    "SettingsStore",
    "TierModel",
    "build_tier_model",
    "discover_profiles",
    "load_base_config",
    "resolve_tier_model_from_env",
    "read_server_status",
    "submit_server_command",
    "supervisor_available",
]
