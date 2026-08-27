"""The event-type registry (work_plan.md §2.1).

Serves the closed set of event types to extraction (which classifies into
it), the clarification prompt (which offers it as choices), and anything
validating a type on an incoming event. Fixed for the life of the run — no
add or remove operation.

The human-activation append and the "a profile may not declare it itself"
rejection already happen in `profiles.loader` / `profiles.loader`
(work_plan.md §1.5/§1.6), before this registry ever sees the list — a
`LoadedProfile.event_types` is already the correct closed set. This module
only wraps it in the query interface §2.1 describes; it does not
re-implement that logic.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class EventTypeRegistry:
    types: tuple[str, ...]

    def is_valid(self, event_type: str) -> bool:
        return event_type in self.types


def build_event_type_registry(loaded_profile: "LoadedProfile") -> EventTypeRegistry:
    return EventTypeRegistry(types=loaded_profile.event_types)
