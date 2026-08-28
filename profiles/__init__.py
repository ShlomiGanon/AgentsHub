"""Public profile contracts and loading facade."""

import sys

from profiles import contracts
from profiles.contracts import (
    AgentSpec,
    HUMAN_ACTIVATION_TYPE,
    LoadedProfile,
    ProfileLoadError,
    ProfileValidationError,
)

spec = contracts
sys.modules[f"{__name__}.spec"] = contracts

from profiles import example, loader
from profiles.loader import hash_profile_file, load_profile, validate_profile, validate_single_protocol

reference = example
sys.modules[f"{__name__}.reference"] = example

__all__ = [
    "AgentSpec",
    "HUMAN_ACTIVATION_TYPE",
    "LoadedProfile",
    "ProfileLoadError",
    "ProfileValidationError",
    "hash_profile_file",
    "load_profile",
    "validate_profile",
    "validate_single_protocol",
]
