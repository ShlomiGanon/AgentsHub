"""Profile loading and selection (work_plan.md §1.5).

Reads the profile module named by a launch argument, reads every
environment variable it names (at load time, not at first use), validates
it (profiles.validate), and freezes the result into an immutable
`LoadedProfile` every subsystem reads from. There is no default profile —
a missing or bad argument fails immediately and clearly.

Core agents land with their owning missions. The History Agent is now
constructed on every load from the base configuration; Main and Insights
remain absent until their Mission 6 implementations land.
Core-agent construction seam: work_plan.md §1.5 says the three core agents
are constructed "on every run... always". `_construct_core_agents` stays a
documented no-op returning an empty mapping — permanently, not just until
the Agent Framework existed. **This module is not where core agents get
wired in.** Constructing a `MainAgent` means importing
`orchestrator.main_agent`, and `profiles` is a low-level package that may
never call upward into `orchestrator` (docs/allowed_calls.md's own
layering rule — `profiles` is called by anything, calls nothing above
itself). Mission 1's original docstring here claimed otherwise; that was
wrong, caught while building §6.1 (Mission 6). The real, correctly-layered
replacement is `orchestrator.main_agent.construct_core_agents(base_config)`
— called by whichever future startup code assembles the running system
(§7/§9, not yet built), never by this function.
"""

import hashlib
import importlib
import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType

from agents.history import HistoryAgent
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
    profile_file_hash: str
    core_agents: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    resolved_secrets: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))


def hash_profile_file(module_path: str) -> str:
    """SHA-256 of the profile module's source file, hex-encoded.

    Added for §7.7: computed once at load time and stored on
    `LoadedProfile`, then recomputed against the file on disk at request
    time so an operator can see a pending edit is awaiting a restart. The
    one function both call, so the two hashes can never be computed two
    different ways. Uses `importlib.util.find_spec` to locate the file —
    the same technique `protocols.editor._resolve_profile_file` already
    uses to find the exact file a profile write must edit.
    """

    spec = importlib.util.find_spec(module_path)
    if spec is None or spec.origin is None:
        raise ProfileLoadError(f"cannot locate source file for profile module '{module_path}'")

    return hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()


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
    """Construct core agents whose owning mission has landed."""

    history_agent = HistoryAgent(base_config.history_agent_model)

    return {history_agent.name: history_agent}


def validate_single_protocol(protocol, agents_by_name: dict) -> list[str]:
    """Validate one protocol against a set of agents, using exactly the
    checks startup validation runs (§1.6) — the entry point
    `protocols.editor` (§4.3) calls before accepting a write, so a
    written protocol can never fail validation differently than the
    protocols already loaded did. `profiles.validate` itself stays
    internal to this package; this is the one sanctioned way another
    package reaches its logic.
    """

    return profile_validate._validate_protocol(protocol, agents_by_name)


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
        profile_file_hash=hash_profile_file(module_path),
        core_agents=MappingProxyType(core_agents),
        resolved_secrets=MappingProxyType(resolved_secrets),
    )

    failures = profile_validate.validate_profile(loaded, declared_event_types=module.EVENT_TYPES)
    if failures:
        raise ProfileValidationError(failures)

    return loaded
