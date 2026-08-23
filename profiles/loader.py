"""Profile loading and selection (work_plan.md §1.5).

Reads the profile module named by a launch argument, reads every
environment variable it names (at load time, not at first use), validates
it (profiles.validate), and freezes the result into an immutable
`LoadedProfile` every subsystem reads from. There is no default profile —
a missing or bad argument fails immediately and clearly.

Core-agent construction seam: work_plan.md §1.5 says the three core agents
are constructed "on every run... always". The Agent Framework (§3) exists
now (agents/base.py etc., Mission 3) — but the Main Agent, History Agent,
and Insights Agent *classes* themselves are §5.3/§6.1/§6.9's job, not
§3's, and none of those sections exist yet. `_construct_core_agents` stays
a documented no-op returning an empty mapping until they do. Once they
land, this is the one place that changes to wire them in — nothing here
needs to change shape.
"""

import importlib
import os
from dataclasses import dataclass, field
from types import MappingProxyType, ModuleType

from config.base import BaseConfig, load_base_config
from profiles import validate as profile_validate
from profiles.spec import HUMAN_ACTIVATION_TYPE, REQUIRED_PROFILE_ATTRS


class ProfileLoadError(Exception):
    """The profile module could not be found or is missing required names."""


class ProfileValidationError(Exception):
    """The profile loaded but failed startup validation (§1.6).

    Carries every failure found, not just the first — see `.failures`.
    """

    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("\n".join(failures))


@dataclass(frozen=True)
class LoadedProfile:
    module_path: str
    agents: tuple
    protocols: tuple
    event_types: tuple[str, ...]
    areas: tuple[str, ...]
    db_path: str
    api_port: int
    retry_count: int
    risk_threshold: float
    lookback_window_days: int
    core_agents: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    resolved_secrets: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))


def _import_profile_module(module_path: str) -> ModuleType:
    if not module_path:
        raise ProfileLoadError(
            "no profile specified — launch with a module path, e.g. "
            "'fixtures.profiles.minimal_profile'; there is no default profile"
        )

    try:
        return importlib.import_module(module_path)
    except ImportError as exc:
        raise ProfileLoadError(
            f"profile module '{module_path}' does not exist or failed to import: {exc}"
        ) from exc


def _check_required_attrs(module: ModuleType, module_path: str) -> None:
    missing = [name for name in REQUIRED_PROFILE_ATTRS if not hasattr(module, name)]

    if missing:
        raise ProfileLoadError(
            f"profile '{module_path}' is missing required attribute(s): {', '.join(missing)}"
        )


def _resolve_secrets(module: ModuleType, module_path: str) -> dict[str, str]:
    var_names = [module.BOT_TOKEN_ENV, *module.MODEL_CREDENTIAL_ENVS]
    resolved: dict[str, str] = {}

    for var_name in var_names:
        value = os.environ.get(var_name)
        if value is None:
            raise ProfileLoadError(
                f"profile '{module_path}' names environment variable "
                f"'{var_name}' but it is not set"
            )
        resolved[var_name] = value

    return resolved


def _construct_core_agents(base_config: BaseConfig) -> dict:
    """Seam for the three core agents (Main, History, Insights).

    Returns an empty mapping until the Agent Framework (§3) exists. See
    module docstring.
    """

    del base_config  # unused until §3 lands
    return {}


def load_profile(module_path: str) -> LoadedProfile:
    module = _import_profile_module(module_path)
    _check_required_attrs(module, module_path)

    resolved_secrets = _resolve_secrets(module, module_path)
    core_agents = _construct_core_agents(load_base_config())

    event_types = tuple(module.EVENT_TYPES) + (HUMAN_ACTIVATION_TYPE,)

    loaded = LoadedProfile(
        module_path=module_path,
        agents=tuple(module.AGENTS),
        protocols=tuple(module.PROTOCOLS),
        event_types=event_types,
        areas=tuple(module.AREAS),
        db_path=module.DB_PATH,
        api_port=module.API_PORT,
        retry_count=module.RETRY_COUNT,
        risk_threshold=module.RISK_THRESHOLD,
        lookback_window_days=module.LOOKBACK_WINDOW_DAYS,
        core_agents=MappingProxyType(core_agents),
        resolved_secrets=MappingProxyType(resolved_secrets),
    )

    failures = profile_validate.validate_profile(loaded, declared_event_types=module.EVENT_TYPES)
    if failures:
        raise ProfileValidationError(failures)

    return loaded
