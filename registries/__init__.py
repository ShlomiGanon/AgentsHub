"""Public facade for immutable deployment registries."""

import sys

from registries import registry
from registries.registry import (
    AreaRegistry,
    EventTypeRegistry,
    build_area_registry,
    build_event_type_registry,
)

areas = registry
event_types = registry
sys.modules[f"{__name__}.areas"] = registry
sys.modules[f"{__name__}.event_types"] = registry

__all__ = [
    "AreaRegistry",
    "EventTypeRegistry",
    "build_area_registry",
    "build_event_type_registry",
]
