"""Public profile contracts and loading facade."""

import sys

from profiles import contracts
from profiles.contracts import (
    AgentSpec,
    AreaRegistry,
    EventTypeRegistry,
    HUMAN_ACTIVATION_TYPE,
    LoadedProfile,
    ProfileLoadError,
    ProfileValidationError,
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
    "ProfileLoadError",
    "ProfileValidationError",
    "build_area_registry",
    "build_event_type_registry",
    "hash_profile_file",
    "load_profile",
    "validate_profile",
    "validate_single_protocol",
]
