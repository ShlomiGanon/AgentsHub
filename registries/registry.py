"""The area registry (work_plan.md §2.2)."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class AreaRegistry:
    areas: tuple[str, ...]

    def is_valid(self, area: str) -> bool:
        return area in self.areas


def build_area_registry(loaded_profile: "LoadedProfile") -> AreaRegistry:
    return AreaRegistry(areas=loaded_profile.areas)


@dataclass(frozen=True)
class EventTypeRegistry:
    types: tuple[str, ...]

    def is_valid(self, event_type: str) -> bool:
        return event_type in self.types


def build_event_type_registry(loaded_profile: "LoadedProfile") -> EventTypeRegistry:
    return EventTypeRegistry(types=loaded_profile.event_types)
