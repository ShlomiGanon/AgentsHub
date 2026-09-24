"""Environment-backed model and runtime configuration."""

import os
from dataclasses import dataclass
from typing import Mapping


def _parse_debug_flag(raw: str | None) -> bool:
    """Strict, not "any non-empty string is truthy": only these exact (case-insensitive) values turn the flag on."""

    return (raw or "").strip().lower() in ("1", "true")


DEBUG_FLAG = _parse_debug_flag(os.environ.get("DEBUG_VERBOSE_LOGGING"))
DEEP_DEBUG = _parse_debug_flag(os.environ.get("DEEP_DEBUG"))


def _parse_console_json_flag(raw: str | None) -> bool:
    """On by default — strict in the opposite direction from `_parse_debug_flag`: only these exact (case-insensitive) values turn it off."""

    return (raw or "").strip().lower() not in ("0", "false")


LOG_CONSOLE_JSON_ENABLED = _parse_console_json_flag(os.environ.get("LOG_CONSOLE_JSON"))


class ModelTierError(Exception):
    """A model tier's provider/model/API key could not be resolved from the environment."""


class RuntimePortError(Exception):
    """A runtime port override is present but is not a valid TCP port."""


def resolve_runtime_port(
    variable_name: str,
    declared_port: int | None,
    environ: Mapping[str, str] | None = None,
) -> int | None:
    """Resolve one deployment-wide port, with the profile value as fallback.

    ``run_stack.py`` loads ``.env`` before profile discovery, so every profile
    selected by that deployment uses the same runtime ports.
    """

    environment_values = os.environ if environ is None else environ
    raw_value = environment_values.get(variable_name)
    if raw_value is None or not raw_value.strip():
        return declared_port
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise RuntimePortError(
            f"environment variable '{variable_name}' must be an integer between 1 and 65535"
        ) from exc
    if not 1 <= port <= 65535:
        raise RuntimePortError(
            f"environment variable '{variable_name}' must be an integer between 1 and 65535"
        )
    return port


@dataclass(frozen=True)
class TierModel:
    """Resolved model identifier and API key."""

    model: str
    api_key: str


def build_tier_model(provider: str, model_name: str, api_key: str) -> TierModel:
    """Join a tier's already-resolved provider/model name/API key into a `TierModel`."""

    return TierModel(model=f"{provider}/{model_name}", api_key=api_key)


def resolve_tier_model_from_env(
    prefix: str,
    environ: Mapping[str, str] | None = None,
    error_type: type[Exception] = ModelTierError,
) -> TierModel:
    environment_values = os.environ if environ is None else environ

    def required(name: str) -> str:
        secret_value = environment_values.get(name)
        if secret_value is None:
            raise error_type(f"required environment variable '{name}' is not set")
        return secret_value

    provider = required(f"{prefix}_MODEL_PROVIDER")
    model_name = required(f"{prefix}_MODEL_NAME")
    api_key_env_name = required(f"{prefix}_MODEL_API_KEY_ENV")
    return build_tier_model(provider, model_name, required(api_key_env_name))


@dataclass(frozen=True)
class BaseConfig:
    core_model: TierModel
    DEBUG_FLAG: bool = False


def load_base_config(core_model: TierModel) -> BaseConfig:
    """Return the base configuration."""

    return BaseConfig(core_model=core_model, DEBUG_FLAG=DEBUG_FLAG)
