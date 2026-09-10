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


# Server-control imports are lazy because profile discovery imports the
# profiles package, which constructs agents that in turn import observability
# through tools -> config.  Eagerly importing server_control here would make
# that legitimate startup path circular.  The facade functions preserve the
# package boundary without loading profile code until an admin/supervisor
# operation actually asks for it.
def discover_profiles(*args, **kwargs):
    from config.server_control import discover_profiles as implementation

    return implementation(*args, **kwargs)


def read_server_status(*args, **kwargs):
    from config.server_control import read_status

    return read_status(*args, **kwargs)


def submit_server_command(*args, **kwargs):
    from config.server_control import submit_command

    return submit_command(*args, **kwargs)


def supervisor_available(*args, **kwargs):
    from config.server_control import supervisor_available as implementation

    return implementation(*args, **kwargs)

__all__ = [
    "BaseConfig",
    "DEEP_DEBUG",
    "DEBUG_FLAG",
    "LOG_CONSOLE_JSON_ENABLED",
    "ModelTierError",
    "SettingsStore",
    "TierModel",
    "build_tier_model",
    "discover_profiles",
    "load_base_config",
    "read_server_status",
    "resolve_tier_model_from_env",
    "submit_server_command",
    "supervisor_available",
]
