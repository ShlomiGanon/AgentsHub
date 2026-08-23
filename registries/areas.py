"""The area registry (work_plan.md §2.2).

Serves the closed set of areas to extraction, which must resolve a
reported location to one of these names, and to history queries that
filter by area. Fixed for the life of the run — no add or remove
operation. Unlike the event-type registry, an unresolvable area does not
hold the event; it only narrows what precedent search can find — that
behavior belongs to extraction (§5.2), not to this registry.
"""

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
