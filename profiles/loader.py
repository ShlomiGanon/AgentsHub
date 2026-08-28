"""Profile loading and selection (work_plan.md §1.5)."""

import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
from types import MappingProxyType, ModuleType

from agents import HistoryAgent
from config import BaseConfig, TierModel, load_base_config
from profiles.contracts import (
    HUMAN_ACTIVATION_TYPE,
    REQUIRED_PROFILE_ATTRS,
    AgentSpec,
    AreaRegistry,
    EventTypeRegistry,
    LoadedProfile,
    ProfileLoadError,
    ProfileValidationError,
    protocol_missing_attrs,
)
from protocols import CriticalityLevel


def build_area_registry(loaded_profile: "LoadedProfile") -> AreaRegistry:
    return AreaRegistry(areas=loaded_profile.areas)


def build_event_type_registry(loaded_profile: "LoadedProfile") -> EventTypeRegistry:
    return EventTypeRegistry(types=loaded_profile.event_types)

def validate_profile(loaded: "LoadedProfile", declared_event_types: list) -> list[str]:
    failures: list[str] = []
    agents_by_name = {agent.name: agent for agent in loaded.agents}

    for protocol in loaded.protocols:
        failures.extend(_validate_protocol(protocol, agents_by_name))

    if not declared_event_types:
        failures.append("profile declares no event types — extraction has nothing to classify into")

    if HUMAN_ACTIVATION_TYPE in declared_event_types:
        failures.append(
            f"profile declares '{HUMAN_ACTIVATION_TYPE}' as an event type — "
            "it is built in and added automatically, declaring it is a duplicate"
        )

    if not loaded.areas:
        failures.append("profile declares no areas — extraction has nothing to resolve a location to")

    return failures


def _validate_protocol(protocol, agents_by_name: dict) -> list[str]:
    failures: list[str] = []
    missing_attrs = protocol_missing_attrs(protocol)
    if missing_attrs:
        failures.append(
            f"protocol object {protocol!r} is missing required attribute(s): "
            f"{', '.join(missing_attrs)}"
        )
        return failures

    exposed_by_participants: set[str] = set()
    for agent_name in protocol.participating_agents:
        agent = agents_by_name.get(agent_name)
        if agent is None:
            failures.append(
                f"protocol '{protocol.name}' names agent '{agent_name}' "
                "which was not constructed by the profile"
            )
            continue

        exposed_by_participants.update(getattr(tool, "name", tool) for tool in agent.exposed_tools())

    for tool_name in protocol.approved_tools:
        if tool_name not in exposed_by_participants:
            failures.append(
                f"protocol '{protocol.name}' approves tool '{tool_name}' "
                "which none of its participating agents expose"
            )

    if not protocol.description:
        failures.append(f"protocol '{protocol.name}' has no description")

    if not protocol.expected_success_output:
        failures.append(f"protocol '{protocol.name}' has no expected success output")

    if not isinstance(protocol.criticality, CriticalityLevel):
        failures.append(
            f"protocol '{protocol.name}' has an invalid criticality level: "
            "expected a real CriticalityLevel enum member (LOW, MEDIUM, or HIGH), "
            f"got {protocol.criticality!r} instead"
        )

    if protocol.approval_flag is not True and protocol.approval_flag is not False:
        failures.append(
            f"protocol '{protocol.name}' has no explicitly-set approval flag "
            "(True/False required — an absent flag is not defaulted)"
        )

    return failures


def hash_profile_file(module_path: str) -> str:
    """SHA-256 of the profile module's source file, hex-encoded."""

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
        secret_value = os.environ.get(var_name)
        if secret_value is None:
            raise ProfileLoadError(
                f"profile '{module_path}' names environment variable "
                f"'{var_name}' but it is not set"
            )
        resolved[var_name] = secret_value

    return resolved


def _construct_core_agents(base_config: BaseConfig) -> dict:
    """Construct core agents whose owning mission has landed."""

    history_agent = HistoryAgent(model=base_config.core_model.model, api_key=base_config.core_model.api_key)

    return {history_agent.name: history_agent}


def _construct_agents_from_specs(module: ModuleType, module_path: str, core_model: TierModel, sub_model: TierModel) -> tuple:
    """Build the real agents a profile's `AGENTS` list only *declares* (`profiles.spec.AgentSpec` — `cls` + `tier`) — the one place any of them actually gets constructed."""

    tier_models = {"core": core_model, "sub": sub_model}
    agents = []

    for index, spec in enumerate(module.AGENTS):
        if not isinstance(spec, AgentSpec):
            raise ProfileLoadError(
                f"profile '{module_path}' AGENTS[{index}] is {spec!r}, not a profiles.spec.AgentSpec "
                "— AGENTS must declare agents (cls, tier), never construct them directly"
            )

        if spec.tier not in tier_models:
            raise ProfileLoadError(
                f"profile '{module_path}' AGENTS[{index}] names tier {spec.tier!r} — must be 'core' or 'sub'"
            )

        tier_model = tier_models[spec.tier]
        agents.append(spec.cls(model=tier_model.model, api_key=tier_model.api_key))

    return tuple(agents)


def validate_single_protocol(protocol, agents_by_name: dict) -> list[str]:
    """Validate one protocol against a set of agents, using exactly the checks startup validation runs (§1.6) — the entry point `protocols.editor` (§4.3) calls before accepting a write..."""

    return _validate_protocol(protocol, agents_by_name)


def load_profile(module_path: str, core_model: TierModel, sub_model: TierModel) -> LoadedProfile:
    """`core_model`/`sub_model` are the two already-resolved `TierModel`s — required, no default, no environment access for model-tier config anywhere in this function (the pre-existin..."""

    profile_module = _import_profile_module(module_path)
    _check_required_attrs(profile_module, module_path)

    resolved_secrets = _resolve_secrets(profile_module, module_path)
    core_agents = _construct_core_agents(load_base_config(core_model=core_model))
    agents = _construct_agents_from_specs(profile_module, module_path, core_model=core_model, sub_model=sub_model)

    event_types = tuple(profile_module.EVENT_TYPES) + (HUMAN_ACTIVATION_TYPE,)

    loaded = LoadedProfile(
        module_path=module_path,
        agents=agents,
        protocols=tuple(profile_module.PROTOCOLS),
        event_types=event_types,
        areas=tuple(profile_module.AREAS),
        db_path=profile_module.DB_PATH,
        api_port=profile_module.API_PORT,
        retry_count=profile_module.RETRY_COUNT,
        risk_threshold=profile_module.RISK_THRESHOLD,
        lookback_window_days=profile_module.LOOKBACK_WINDOW_DAYS,
        profile_file_hash=hash_profile_file(module_path),
        core_agents=MappingProxyType(core_agents),
        resolved_secrets=MappingProxyType(resolved_secrets),
    )

    failures = validate_profile(loaded, declared_event_types=profile_module.EVENT_TYPES)
    if failures:
        raise ProfileValidationError(failures)

    return loaded
