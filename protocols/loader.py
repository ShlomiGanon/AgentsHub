"""Loading protocols from the profile (work_plan.md §4.2).

`profiles.loader` already instantiates every protocol the active profile
declares at startup, into `LoadedProfile.protocols` (Mission 1) — this
module wraps that already-correct data in the read-only query interface
§4.2 describes, the same pattern `registries.event_types` used for event
types in Mission 2. Fixed for the life of the run: no add, edit, or
remove operation is reachable from here. Depends on startup validation
having already rejected anything unusable (§1.6) — this module does no
checking of its own.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from protocols.model import Protocol

if TYPE_CHECKING:
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class ProtocolSet:
    protocols: tuple[Protocol, ...]

    def all(self) -> tuple[Protocol, ...]:
        return self.protocols

    def get(self, name: str) -> Protocol | None:
        for protocol in self.protocols:
            if protocol.name == name:
                return protocol
        return None


def load_protocols(loaded_profile: "LoadedProfile") -> ProtocolSet:
    return ProtocolSet(protocols=loaded_profile.protocols)
