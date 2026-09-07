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

__all__ = [
    "BaseConfig",
    "DEEP_DEBUG",
    "DEBUG_FLAG",
    "LOG_CONSOLE_JSON_ENABLED",
    "ModelTierError",
    "SettingsStore",
    "TierModel",
    "build_tier_model",
    "load_base_config",
    "resolve_tier_model_from_env",
]
