"""Public profile contracts and loading facade."""

import sys

from profiles import contracts
from profiles.contracts import (
    AgentSpec,
    AreaRegistry,
    EventTypeRegistry,
    HUMAN_ACTIVATION_TYPE,
    LoadedProfile,
    OptimizationPolicy,
    REQUIRED_PROFILE_ATTRS,
    ProfileLoadError,
    ProfileValidationError,
    StageModelPolicy,
    UNCLASSIFIED_REQUIRED_FIELDS,
    UNCLASSIFIED_TYPE,
)

spec = contracts
sys.modules[f"{__name__}.spec"] = contracts

from profiles import loader, template
from profiles.loader import (
    build_area_registry,
    build_event_type_registry,
    hash_profile_file,
    load_profile,
    validate_profile,
    validate_single_protocol,
)

from profiles.simulation import (
    SIMULATION_GROUP_ID_BASE,
    SIMULATION_USER_ID_BASE,
    SimulationGroup,
    SimulationPersona,
    SimulationRoster,
    SimulationScenario,
    SimulationStepContext,
    SimulationEntityResolution,
    resolve_simulation_entity,
    resolve_simulation_step,
    simulation_group_chat_id,
    simulation_user_telegram_id,
)
from profiles.operational_profile import (
    FIRE_STATION,
    FIXTURE_DOMAIN_PROFILES,
    RESPONSE_TEAM,
    OperationalProfile,
    OperationalProfileError,
    ResourceDefinition,
    current_operational_profile,
    declared_operational_profiles,
    operational_profile,
    operational_profile_context,
    profile_for_scope,
    profile_id_for_scenario,
)
from profiles.simulation_provisioning import (
    ProvisioningResult,
    RunProvisioningResult,
    ensure_simulation_entities,
    provision_operational_world,
    initialize_operational_scope,
    operational_baseline_for_scenario,
)

example = template
reference = template
sys.modules[f"{__name__}.example"] = template
sys.modules[f"{__name__}.reference"] = template

__all__ = [
    "AgentSpec",
    "AreaRegistry",
    "EventTypeRegistry",
    "HUMAN_ACTIVATION_TYPE",
    "LoadedProfile",
    "OptimizationPolicy",
    "REQUIRED_PROFILE_ATTRS",
    "ProfileLoadError",
    "ProfileValidationError",
    "StageModelPolicy",
    "UNCLASSIFIED_REQUIRED_FIELDS",
    "UNCLASSIFIED_TYPE",
    "build_area_registry",
    "build_event_type_registry",
    "hash_profile_file",
    "load_profile",
    "validate_profile",
    "validate_single_protocol",
    "SIMULATION_GROUP_ID_BASE",
    "SIMULATION_USER_ID_BASE",
    "SimulationGroup",
    "SimulationPersona",
    "SimulationRoster",
    "SimulationScenario",
    "SimulationStepContext",
    "SimulationEntityResolution",
    "resolve_simulation_entity",
    "resolve_simulation_step",
    "simulation_group_chat_id",
    "simulation_user_telegram_id",
    "ProvisioningResult",
    "ensure_simulation_entities",
    "FIRE_STATION",
    "FIXTURE_DOMAIN_PROFILES",
    "RESPONSE_TEAM",
    "OperationalProfile",
    "OperationalProfileError",
    "ResourceDefinition",
    "current_operational_profile",
    "declared_operational_profiles",
    "operational_profile",
    "operational_profile_context",
    "profile_for_scope",
    "profile_id_for_scenario",
    "RunProvisioningResult",
    "provision_operational_world",
    "initialize_operational_scope",
    "operational_baseline_for_scenario",
]
