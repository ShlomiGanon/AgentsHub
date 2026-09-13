"""Profile-declared simulation users, groups, and scenarios (docs/profile_simulations_design.md).

A profile that wants simulations declares three optional module-level lists —
`SIMULATION_USERS`, `SIMULATION_GROUPS`, `SIMULATIONS` — using the dataclasses
below, the same "declare, don't construct" convention `profiles.spec.AgentSpec`
already uses. `profiles.loader.load_profile` reads them (defaulting to `()`
when a profile declares none, so every existing profile is unaffected) and
`profiles.simulation_provisioning.ensure_simulation_entities` is the one place
they turn into real `users`/`telegram_groups` rows.

Every simulation Telegram ID is a pure function of `(base, offset)` — never
random, never read back from storage — so the same profile always provisions
the same IDs, and the server-side JSON adapter (`api/simulations.py`) never
needs a database round trip to compute them.
"""

from dataclasses import dataclass, field
from typing import Mapping

# Chosen so a simulation ID is always:
#   - well above Telegram's own documented real-ID ceiling (2^52 ~= 4.5e15), and
#   - well below the browser's Number.MAX_SAFE_INTEGER (2^53-1 ~= 9.007e15) —
#     the admin simulator's own client-side validation (api/admin_simulator.py)
#     does `Number(id)` arithmetic on these values, so precision must hold there too.
# so a simulation ID can never collide with a real Telegram user/group ID.
SIMULATION_USER_ID_BASE = 9_000_000_000_000_000
SIMULATION_GROUP_ID_BASE = -9_000_000_000_000_000


def simulation_user_telegram_id(offset: int) -> str:
    """The deterministic reserved Telegram user ID for persona `offset`."""

    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"simulation user offset must be a non-negative int, got {offset!r}")
    return str(SIMULATION_USER_ID_BASE + offset)


def simulation_group_chat_id(offset: int) -> str:
    """The deterministic reserved Telegram group chat ID for group `offset`."""

    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"simulation group offset must be a non-negative int, got {offset!r}")
    return str(SIMULATION_GROUP_ID_BASE - offset)


@dataclass(frozen=True)
class SimulationPersona:
    """One simulation user a profile declares.

    `key` is the placeholder a `SimulationScenario.raw`'s `steps[].sender_identity`
    uses in place of a concrete Telegram ID. `offset` is a stable, author-assigned
    ordinal within the reserved user-ID block — never renumbered or reused once a
    profile has shipped with it, or a previously-provisioned simulation user
    silently becomes a different persona on the next restart.
    """

    key: str
    offset: int
    permission_level: str = "viewer"
    full_name: str = ""


@dataclass(frozen=True)
class SimulationGroup:
    """One simulation Telegram group a profile declares.

    `key` is the placeholder a `SimulationScenario.raw`'s `chats[].telegram_chat_id`
    uses in place of a concrete chat ID. `offset` is a stable, author-assigned
    ordinal within the reserved group-ID block (same stability rule as
    `SimulationPersona.offset`).
    """

    key: str
    offset: int
    agent_name: str = "main_agent"
    label: str = ""


@dataclass(frozen=True)
class SimulationScenario:
    """One simulation a profile exposes, in the existing admin-simulator JSON shape.

    `raw` is exactly the canonical `{"scenario": ..., "chats": [...], "steps": [...]}`
    structure `api/admin_simulator.py` already documents and validates — except
    `chats[].telegram_chat_id` and `steps[].sender_identity` hold a declared
    `SimulationGroup`/`SimulationPersona` *key* (a string), not a concrete
    Telegram ID. `api.simulations.materialize_simulation` is the one place a
    key is resolved to its deterministic reserved ID; the shape itself never
    changes.
    """

    key: str
    title: str
    raw: Mapping
    description: str = ""
    tags: tuple[str, ...] = ()
