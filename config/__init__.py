"""Public configuration facade."""

import sys

from config import models, settings
from config.models import (
    BaseConfig,
    DEBUG_FLAG,
    LOG_CONSOLE_JSON_ENABLED,
    ModelTierError,
    TierModel,
    build_tier_model,
    load_base_config,
    resolve_tier_model_from_env,
)
from config.settings import SettingsStore

base = models
settings_store = settings
sys.modules[f"{__name__}.base"] = models
sys.modules[f"{__name__}.settings_store"] = settings

__all__ = [
    "BaseConfig",
    "DEBUG_FLAG",
    "LOG_CONSOLE_JSON_ENABLED",
    "ModelTierError",
    "SettingsStore",
    "TierModel",
    "build_tier_model",
    "load_base_config",
    "resolve_tier_model_from_env",
]
