"""Provisioning for a profile's declared simulation users/groups (docs/profile_simulations_design.md).

`ensure_simulation_entities` is the one shared, idempotent routine covering
both simulation users and simulation groups — the "unified provisioning
routine" the design calls for. `users` and `telegram_groups` are genuinely
different tables (different columns entirely), so this is unification at the
level the existing architecture actually supports: one shared control-flow
loop applying the same "create if missing, never touch if present" idiom to
both, the same way `persistence.register_telegram_user_if_missing` and
`persistence.register_telegram_group_if_missing` are already two parallel
methods sharing one idiom rather than one merged method.

Called once, from `api.app.build_context`, right after `persistence` opens —
the one place a profile actually becomes a running server (work_plan.md's
"profile load"). Never touches an entity that already exists, so an
operator's later edits (a simulation user's full name, a simulation group's
label or promoted chat ID) survive every subsequent restart.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from profiles.simulation import simulation_group_chat_id, simulation_user_telegram_id

if TYPE_CHECKING:
    from persistence import PersistenceInterface
    from profiles.contracts import LoadedProfile


@dataclass(frozen=True)
class ProvisioningResult:
    created_users: tuple[str, ...]
    created_groups: tuple[str, ...]


def ensure_simulation_entities(persistence: "PersistenceInterface", loaded_profile: "LoadedProfile") -> ProvisioningResult:
    """Create any of `loaded_profile`'s declared simulation users/groups that don't exist yet.

    Every simulation user/group is written with `auto_register=False` (via
    `persistence.ensure_user_exists`/`ensure_group_exists`), so safe mode never
    blocks it the way a real auto-registered Telegram entity would be.
    """

    created_users = []
    for persona in loaded_profile.simulation_users:
        telegram_id = simulation_user_telegram_id(persona.offset)
        if persistence.ensure_user_exists(telegram_id, persona.permission_level, persona.full_name):
            created_users.append(telegram_id)

    created_groups = []
    for group in loaded_profile.simulation_groups:
        chat_id = simulation_group_chat_id(group.offset)
        if persistence.ensure_group_exists(chat_id, group.agent_name, group.label):
            created_groups.append(chat_id)

    return ProvisioningResult(created_users=tuple(created_users), created_groups=tuple(created_groups))
