"""Profile loading and selection (work_plan.md §1.5)."""

import hashlib
import importlib
import importlib.util
import os
import math
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, ModuleType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import HistoryAgent
from config import BaseConfig, TierModel, load_base_config
from messages import MessageCatalogError, get_catalog
from profiles.contracts import (
    HUMAN_ACTIVATION_TYPE,
    REQUIRED_PROFILE_ATTRS,
    UNCLASSIFIED_TYPE,
    AgentSpec,
    AreaRegistry,
    EventTypeRegistry,
    LoadedProfile,
    OptimizationPolicy,
    ProfileLoadError,
    ProfileValidationError,
    StageModelPolicy,
    protocol_missing_attrs,
)
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, DirectToolExecution, EVENT_DATA_FIELDS


def build_area_registry(loaded_profile: "LoadedProfile") -> AreaRegistry:
    return AreaRegistry(areas=loaded_profile.areas)


def build_event_type_registry(loaded_profile: "LoadedProfile") -> EventTypeRegistry:
    # UNCLASSIFIED_TYPE's required fields are fixed in EventTypeRegistry
    # itself (item #6) — not read from the profile.
    return EventTypeRegistry(
        types=loaded_profile.event_types, required_fields=MappingProxyType(dict(loaded_profile.event_type_required_fields))
    )

def validate_profile(loaded: "LoadedProfile", declared_event_types: list) -> list[str]:
    failures: list[str] = []
    agents_by_name = {agent.name: agent for agent in loaded.agents}
    agents_by_name.update(dict(getattr(loaded, "core_agents", {})))

    if not isinstance(loaded.profile_name, str) or not loaded.profile_name.strip():
        failures.append("PROFILE_NAME must be a non-empty string")
    elif any(character in loaded.profile_name for character in ("\r", "\n", "\x00")):
        failures.append("PROFILE_NAME must not contain control characters")

    if getattr(loaded, "default_language", "en") not in {"en", "he"}:
        failures.append("DEFAULT_LANGUAGE must be 'en' or 'he'")

    if type(loaded.max_iter) is not int or not 1 <= loaded.max_iter <= 100:
        failures.append("MAX_ITER must be an integer between 1 and 100")
    if (
        type(loaded.model_timeout_seconds) not in {int, float}
        or not math.isfinite(loaded.model_timeout_seconds)
        or loaded.model_timeout_seconds <= 0
        or loaded.model_timeout_seconds > 600
    ):
        failures.append("MODEL_TIMEOUT_SECONDS must be a finite number between 0 and 600")

    protocol_names = [getattr(protocol, "name", None) for protocol in loaded.protocols]
    duplicate_protocol_names = sorted({name for name in protocol_names if name and protocol_names.count(name) > 1})
    if duplicate_protocol_names:
        failures.append(f"profile declares duplicate protocol names: {', '.join(duplicate_protocol_names)}")

    for protocol in loaded.protocols:
        failures.extend(_validate_protocol(protocol, agents_by_name))

    if not declared_event_types:
        failures.append("profile declares no event types — extraction has nothing to classify into")

    if HUMAN_ACTIVATION_TYPE in declared_event_types:
        failures.append(
            f"profile declares '{HUMAN_ACTIVATION_TYPE}' as an event type — "
            "it is built in and added automatically, declaring it is a duplicate"
        )

    required_fields = getattr(loaded, "event_type_required_fields", {})
    if UNCLASSIFIED_TYPE in required_fields:
        failures.append(
            f"profile declares EVENT_TYPE_REQUIRED_FIELDS['{UNCLASSIFIED_TYPE}'] — "
            "its required fields are fixed in core code, declaring it here is not allowed"
        )
    for event_type, fields in required_fields.items():
        if event_type != UNCLASSIFIED_TYPE and event_type not in declared_event_types:
            failures.append(f"EVENT_TYPE_REQUIRED_FIELDS references undeclared event type '{event_type}'")
        unknown_fields = sorted(set(fields) - set(EVENT_DATA_FIELDS))
        if unknown_fields:
            failures.append(
                f"EVENT_TYPE_REQUIRED_FIELDS['{event_type}'] references unknown field(s): {', '.join(unknown_fields)}"
            )

    if not loaded.areas:
        failures.append("profile declares no areas — extraction has nothing to resolve a location to")

    db_path = getattr(loaded, "db_path", None)
    resettable_databases = getattr(loaded, "resettable_databases", (db_path,) if db_path else None)
    if resettable_databases is not None:
        if (
            not isinstance(resettable_databases, tuple)
            or not resettable_databases
            or any(not isinstance(path, str) or not path.strip() for path in resettable_databases)
        ):
            failures.append("RESETTABLE_DATABASES must be a non-empty tuple of database paths")
        elif db_path not in resettable_databases:
            failures.append("RESETTABLE_DATABASES must include DB_PATH")

    timezone_name = getattr(loaded, "timezone_name", "UTC")
    try:
        ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        failures.append(f"profile timezone {timezone_name!r} is not a valid IANA timezone")

    history_turns = getattr(loaded, "conversation_history_turns", 0)
    if type(history_turns) is not int or history_turns < 0:
        failures.append("CONVERSATION_HISTORY_TURNS must be a non-negative integer")

    history_ttl = getattr(loaded, "conversation_history_ttl_hours", 24)
    if type(history_ttl) not in {int, float} or history_ttl <= 0:
        failures.append("CONVERSATION_HISTORY_TTL_HOURS must be positive")

    policy = getattr(loaded, "optimization_policy", OptimizationPolicy())
    if not isinstance(policy, OptimizationPolicy):
        failures.append("OPTIMIZATION_POLICY must be a profiles.OptimizationPolicy")
    else:
        if policy.planner_mode not in {"legacy", "shadow", "merged"}:
            failures.append("OPTIMIZATION_POLICY.planner_mode is invalid")
        if policy.operational_decision_mode not in {"separate", "shadow", "merged"}:
            failures.append("OPTIMIZATION_POLICY.operational_decision_mode is invalid")
        if policy.operational_intake_mode not in {"separate", "single"}:
            failures.append("OPTIMIZATION_POLICY.operational_intake_mode is invalid")
        if policy.deterministic_execution_mode not in {"specialist", "direct"}:
            failures.append("OPTIMIZATION_POLICY.deterministic_execution_mode is invalid")
        if policy.final_assessment_mode not in {"separate", "low_risk_merged"}:
            failures.append("OPTIMIZATION_POLICY.final_assessment_mode is invalid")
        if policy.structured_output_mode not in {"off", "auto", "required"}:
            failures.append("OPTIMIZATION_POLICY.structured_output_mode is invalid")
        if policy.event_queue_mode not in {"serial", "policy"}:
            failures.append("OPTIMIZATION_POLICY.event_queue_mode is invalid")
        if not 1 <= policy.event_workers <= 64:
            failures.append("OPTIMIZATION_POLICY.event_workers must be between 1 and 64")
        if policy.event_queue_size < policy.event_workers:
            failures.append("OPTIMIZATION_POLICY.event_queue_size must be at least event_workers")
        if not 0 <= policy.reserved_continuation_percent <= 80:
            failures.append("OPTIMIZATION_POLICY.reserved_continuation_percent must be between 0 and 80")
        if not 1 <= policy.notification_wait_seconds <= 30:
            failures.append("OPTIMIZATION_POLICY.notification_wait_seconds must be between 1 and 30")
        if not 1 <= policy.specialist_fanout <= 4:
            failures.append("OPTIMIZATION_POLICY.specialist_fanout must be between 1 and 4")
        if not 1 <= policy.provider_concurrency <= 64:
            failures.append("OPTIMIZATION_POLICY.provider_concurrency must be between 1 and 64")
        if policy.direct_deadline_seconds <= 0 or policy.job_deadline_seconds <= 0:
            failures.append("OPTIMIZATION_POLICY deadlines must be positive")
        for stage_name, stage_policy in policy.stage_model_policies.items():
            if not isinstance(stage_name, str) or not stage_name or not isinstance(stage_policy, StageModelPolicy):
                failures.append("OPTIMIZATION_POLICY.stage_model_policies must map stage names to StageModelPolicy")
                continue
            if stage_policy.max_output_tokens <= 0 or stage_policy.timeout_seconds <= 0:
                failures.append(f"stage model policy {stage_name!r} requires positive token and timeout budgets")

    failures.extend(_validate_simulation_declarations(loaded))

    return failures


def _validate_simulation_declarations(loaded: "LoadedProfile") -> list[str]:
    """SIMULATION_USERS/SIMULATION_GROUPS/SIMULATIONS/SIMULATION_ROSTERS
    (docs/profile_simulations_design.md): unique keys/offsets, and every
    persona/group/roster key referenced elsewhere actually resolves to a
    declared one. Defaults are empty tuples, so a profile declaring none of
    this is unaffected (every failure below is vacuous on empty input).

    Read via getattr(..., ()) rather than direct attribute access, like every other
    optional LoadedProfile field validate_profile checks (e.g. event_type_required_fields
    below) — some test doubles (e.g. tests/test_profile_loading.py's SimpleNamespace
    fixture) predate this field and never set it."""

    failures: list[str] = []
    simulation_users = getattr(loaded, "simulation_users", ())
    simulation_groups = getattr(loaded, "simulation_groups", ())
    simulations = getattr(loaded, "simulations", ())
    simulation_rosters = getattr(loaded, "simulation_rosters", ())

    for index, persona in enumerate(simulation_users):
        if not isinstance(persona, SimulationPersona):
            failures.append(f"SIMULATION_USERS[{index}] is {persona!r}, not a profiles.simulation.SimulationPersona")
    for index, group in enumerate(simulation_groups):
        if not isinstance(group, SimulationGroup):
            failures.append(f"SIMULATION_GROUPS[{index}] is {group!r}, not a profiles.simulation.SimulationGroup")
    for index, scenario in enumerate(simulations):
        if not isinstance(scenario, SimulationScenario):
            failures.append(f"SIMULATIONS[{index}] is {scenario!r}, not a profiles.simulation.SimulationScenario")
    for index, roster in enumerate(simulation_rosters):
        if not isinstance(roster, SimulationRoster):
            failures.append(f"SIMULATION_ROSTERS[{index}] is {roster!r}, not a profiles.simulation.SimulationRoster")
    if failures:
        # A wrongly-typed entry can't be introspected further (.key/.offset/.raw may not
        # exist) — report the type errors alone rather than cascading into AttributeErrors.
        return failures

    persona_keys = [persona.key for persona in simulation_users]
    duplicate_persona_keys = sorted({key for key in persona_keys if persona_keys.count(key) > 1})
    if duplicate_persona_keys:
        failures.append(f"SIMULATION_USERS declares duplicate key(s): {', '.join(duplicate_persona_keys)}")
    persona_offsets = [persona.offset for persona in simulation_users]
    duplicate_persona_offsets = sorted({offset for offset in persona_offsets if persona_offsets.count(offset) > 1})
    if duplicate_persona_offsets:
        failures.append(f"SIMULATION_USERS declares duplicate offset(s): {duplicate_persona_offsets}")

    group_keys = [group.key for group in simulation_groups]
    duplicate_group_keys = sorted({key for key in group_keys if group_keys.count(key) > 1})
    if duplicate_group_keys:
        failures.append(f"SIMULATION_GROUPS declares duplicate key(s): {', '.join(duplicate_group_keys)}")
    group_offsets = [group.offset for group in simulation_groups]
    duplicate_group_offsets = sorted({offset for offset in group_offsets if group_offsets.count(offset) > 1})
    if duplicate_group_offsets:
        failures.append(f"SIMULATION_GROUPS declares duplicate offset(s): {duplicate_group_offsets}")

    scenario_keys = [scenario.key for scenario in simulations]
    duplicate_scenario_keys = sorted({key for key in scenario_keys if scenario_keys.count(key) > 1})
    if duplicate_scenario_keys:
        failures.append(f"SIMULATIONS declares duplicate key(s): {', '.join(duplicate_scenario_keys)}")

    roster_keys = [roster.key for roster in simulation_rosters]
    duplicate_roster_keys = sorted({key for key in roster_keys if roster_keys.count(key) > 1})
    if duplicate_roster_keys:
        failures.append(f"SIMULATION_ROSTERS declares duplicate key(s): {', '.join(duplicate_roster_keys)}")

    known_persona_keys = set(persona_keys)
    known_group_keys = set(group_keys)
    known_roster_keys = set(roster_keys)

    for persona in simulation_users:
        for roster_key in persona.pre_approved_rosters:
            if roster_key not in known_roster_keys:
                failures.append(
                    f"SIMULATION_USERS[{persona.key!r}] names pre_approved_rosters "
                    f"{roster_key!r} which is not a key in SIMULATION_ROSTERS"
                )

    for scenario in simulations:
        raw = scenario.raw
        if not isinstance(raw, dict) or not isinstance(raw.get("chats"), list) or not isinstance(raw.get("steps"), list):
            failures.append(
                f"simulation '{scenario.key}' raw must be a dict with 'chats' and 'steps' lists "
                "(the existing admin-simulator scenario JSON shape)"
            )
            continue

        chats_by_key = {chat.get("key"): chat for chat in raw["chats"] if isinstance(chat, dict)}
        source_chat_aliases = {
            "TELEGRAM_GROUP_RESPONSE_TEAM": "response_team",
            "TELEGRAM_GROUP_CAMERAS": "cameras",
            "TELEGRAM_GROUP_EXTERNAL_FORCES": "external_forces",
            "TELEGRAM_DIRECT_COMMANDER": "commander_dm",
        }
        known_source_chats = set(chats_by_key)
        known_source_chats.update(
            str(chat.get("telegram_chat_id"))
            for chat in raw["chats"]
            if isinstance(chat, dict) and chat.get("telegram_chat_id") is not None
        )

        # Official migrated scenarios carry a canonical event stream alongside
        # the legacy admin shape.  Validate it without changing the manual
        # simulator contract; old ad-hoc test scenarios remain valid when no
        # official metadata is declared.
        official = getattr(scenario, "official_metadata", {}) or {}
        event_stream = official.get("event_stream", ())
        if event_stream:
            seen_steps: set[int] = set()
            previous_timestamp: datetime | None = None
            for entry in event_stream:
                if not isinstance(entry, dict):
                    failures.append(f"simulation '{scenario.key}' event_stream entry must be an object")
                    continue
                try:
                    step_number = int(entry["step"])
                except (KeyError, TypeError, ValueError):
                    failures.append(f"simulation '{scenario.key}' event_stream has invalid step")
                    continue
                if step_number in seen_steps:
                    failures.append(f"simulation '{scenario.key}' has duplicate event_stream step {step_number}")
                seen_steps.add(step_number)
                timestamp = entry.get("timestamp")
                try:
                    parsed_timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    failures.append(f"simulation '{scenario.key}' step {step_number} has invalid timestamp")
                    continue
                if previous_timestamp is not None and parsed_timestamp < previous_timestamp:
                    failures.append(f"simulation '{scenario.key}' timestamps must be non-decreasing")
                previous_timestamp = parsed_timestamp
                source_chat = str(entry.get("source_chat") or "")
                alias_matches = False
                if not source_chat:
                    failures.append(f"simulation '{scenario.key}' step {step_number} has no source_chat")
                else:
                    alias_target = source_chat_aliases.get(source_chat)
                    alias_matches = alias_target in known_source_chats or any(
                        str(key).endswith(f"_{alias_target}") for key in known_source_chats
                    )
                if source_chat and source_chat not in known_source_chats and not alias_matches:
                    failures.append(
                        f"simulation '{scenario.key}' step {step_number} has unknown source_chat {source_chat!r}"
                    )
                payload = entry.get("payload")
                if not isinstance(payload, dict) or not str(payload.get("message") or "").strip():
                    failures.append(f"simulation '{scenario.key}' step {step_number} has no event message")

            expected_actions = official.get("expected_agent_actions", ())
            for action in expected_actions:
                if not isinstance(action, dict) or not action.get("action_type") or not action.get("trigger_step"):
                    failures.append(f"simulation '{scenario.key}' has malformed expected_agent_actions metadata")

        group_agents = {group.key: group.agent_name for group in simulation_groups}

        for chat in raw["chats"]:
            if not isinstance(chat, dict):
                continue
            chat_kind = chat.get("kind", "message")
            chat_type = chat.get("telegram_chat_type")
            chat_id_key = chat.get("telegram_chat_id")
            if chat_kind == "message" and chat_type in {"group", "supergroup"}:
                if chat_id_key not in known_group_keys:
                    failures.append(
                        f"simulation '{scenario.key}' chat '{chat.get('key')}' names telegram_chat_id "
                        f"{chat_id_key!r} which is not a key in SIMULATION_GROUPS"
                    )

        raw_step_numbers: list[int] = []
        for step in raw["steps"]:
            if not isinstance(step, dict):
                continue
            try:
                step_number = int(step.get("step"))
                raw_step_numbers.append(step_number)
            except (TypeError, ValueError):
                failures.append(f"simulation '{scenario.key}' has invalid step number")
                continue
            chat = chats_by_key.get(step.get("chat"))
            if chat is None or chat.get("kind", "message") != "message":
                continue  # a sensor ("event") step's sender_identity is not a persona
            sender_key = step.get("sender_identity")
            if sender_key not in known_persona_keys:
                failures.append(
                    f"simulation '{scenario.key}' step {step.get('step')} names sender_identity "
                    f"{sender_key!r} which is not a key in SIMULATION_USERS"
                )

        if len(raw_step_numbers) != len(set(raw_step_numbers)):
            failures.append(f"simulation '{scenario.key}' has duplicate step numbers")
        if raw_step_numbers != sorted(raw_step_numbers):
            failures.append(f"simulation '{scenario.key}' step numbers must be non-decreasing")

        for canonical_step in scenario.canonical_raw().get("steps", ()):
            if not isinstance(canonical_step, dict):
                continue
            target_agent = canonical_step.get("target_agent")
            chat = chats_by_key.get(canonical_step.get("chat"))
            if not target_agent or chat is None or chat.get("telegram_chat_type") == "private":
                continue
            group_key = chat.get("telegram_chat_id")
            current_owner = group_agents.get(group_key)
            legacy_owner = {
                "personnel_agent": "team_status_agent",
                "vision_agent": "surveillance_agent",
                "external_comm_agent": "friendly_forces_agent",
                "main_orchestrator": "main_agent",
            }.get(str(target_agent), str(target_agent))
            if current_owner is not None and legacy_owner != current_owner:
                failures.append(
                    f"simulation '{scenario.key}' step {canonical_step.get('step')} legacy target_agent "
                    f"{target_agent!r} conflicts with trusted group owner {current_owner!r}"
                )

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

    if not isinstance(protocol.name, str) or not protocol.name.strip():
        failures.append("protocol name must be a non-empty string")
    elif any(character in protocol.name for character in ("\r", "\n", "\x00")):
        failures.append(f"protocol name {protocol.name!r} contains control characters")

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

    deterministic_fields = getattr(protocol, "deterministic_required_event_fields", None)
    if deterministic_fields is not None:
        if len(protocol.participating_agents) != 1:
            failures.append(
                f"protocol '{protocol.name}' declares deterministic task formulation but does not have exactly one participating agent"
            )
        if not isinstance(deterministic_fields, tuple) or any(
            field_name not in EVENT_DATA_FIELDS for field_name in deterministic_fields
        ):
            failures.append(
                f"protocol '{protocol.name}' deterministic_required_event_fields must be a tuple containing only supported event fields"
            )

    direct_execution = getattr(protocol, "direct_tool_execution", None)
    if direct_execution is not None:
        if not isinstance(direct_execution, DirectToolExecution):
            failures.append(f"protocol '{protocol.name}' direct_tool_execution has an invalid contract")
            direct_execution = None
    if direct_execution is not None:
        if deterministic_fields is None or len(protocol.participating_agents) != 1:
            failures.append(
                f"protocol '{protocol.name}' direct tool execution requires deterministic single-agent formulation"
            )
        if direct_execution.tool_name not in protocol.approved_tools:
            failures.append(
                f"protocol '{protocol.name}' direct tool must be listed in approved_tools"
            )
        argument_names = [name for name, _source in direct_execution.argument_sources]
        source_paths = [source for _name, source in direct_execution.argument_sources]
        if len(argument_names) != len(set(argument_names)) or any(
            not isinstance(name, str) or not name for name in argument_names
        ):
            failures.append(f"protocol '{protocol.name}' direct tool argument names must be unique non-empty strings")
        if any(not source.startswith("business_fields.") for source in source_paths):
            failures.append(
                f"protocol '{protocol.name}' direct tool sources must use validated business_fields"
            )
        if any(name not in argument_names for name in direct_execution.required_arguments):
            failures.append(f"protocol '{protocol.name}' direct required arguments must be declared mappings")
        if any(
            required_name not in argument_names or controlling_name not in argument_names
            for required_name, controlling_name, _value in direct_execution.required_when
        ):
            failures.append(f"protocol '{protocol.name}' conditional direct arguments must be declared mappings")

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

    try:
        message_catalog = get_catalog(profile_module.DEFAULT_LANGUAGE)
    except MessageCatalogError as exc:
        raise ProfileValidationError([str(exc)]) from exc

    loaded = LoadedProfile(
        module_path=module_path,
        profile_name=(
            profile_module.PROFILE_NAME.strip()
            if isinstance(profile_module.PROFILE_NAME, str)
            else profile_module.PROFILE_NAME
        ),
        agents=agents,
        protocols=tuple(profile_module.PROTOCOLS),
        event_types=event_types,
        areas=tuple(profile_module.AREAS),
        db_path=profile_module.DB_PATH,
        resettable_databases=tuple(getattr(profile_module, "RESETTABLE_DATABASES", (profile_module.DB_PATH,))),
        api_port=profile_module.API_PORT,
        retry_count=profile_module.RETRY_COUNT,
        risk_threshold=profile_module.RISK_THRESHOLD,
        lookback_window_days=profile_module.LOOKBACK_WINDOW_DAYS,
        profile_file_hash=hash_profile_file(module_path),
        default_language=profile_module.DEFAULT_LANGUAGE,
        message_catalog=message_catalog,
        max_iter=profile_module.MAX_ITER,
        model_timeout_seconds=profile_module.MODEL_TIMEOUT_SECONDS,
        core_agents=MappingProxyType(core_agents),
        resolved_secrets=MappingProxyType(resolved_secrets),
        timezone_name=getattr(profile_module, "TIMEZONE", "UTC"),
        conversation_history_turns=getattr(profile_module, "CONVERSATION_HISTORY_TURNS", 0),
        conversation_history_ttl_hours=getattr(profile_module, "CONVERSATION_HISTORY_TTL_HOURS", 24),
        optimization_policy=getattr(profile_module, "OPTIMIZATION_POLICY", OptimizationPolicy()),
        event_type_required_fields=MappingProxyType(
            {
                event_type: tuple(fields)
                for event_type, fields in getattr(profile_module, "EVENT_TYPE_REQUIRED_FIELDS", {}).items()
            }
        ),
        simulation_users=tuple(getattr(profile_module, "SIMULATION_USERS", ())),
        simulation_groups=tuple(getattr(profile_module, "SIMULATION_GROUPS", ())),
        simulations=tuple(getattr(profile_module, "SIMULATIONS", ())),
        simulation_rosters=tuple(getattr(profile_module, "SIMULATION_ROSTERS", ())),
        simulator_port=getattr(profile_module, "SIMULATOR_PORT", None),
    )

    failures = validate_profile(loaded, declared_event_types=profile_module.EVENT_TYPES)
    if failures:
        raise ProfileValidationError(failures)

    return loaded
