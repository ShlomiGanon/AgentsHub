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
    registered_roster_members: tuple[tuple[str, str], ...] = ()
    newly_approved_rosters: tuple[str, ...] = ()


def ensure_simulation_entities(persistence: "PersistenceInterface", loaded_profile: "LoadedProfile") -> ProvisioningResult:
    """Create any of `loaded_profile`'s declared simulation users/groups that don't exist yet,
    then register+approve any of them a persona's `pre_approved_rosters` names.

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

    registered_roster_members, newly_approved_rosters = _ensure_roster_memberships(loaded_profile)

    return ProvisioningResult(
        created_users=tuple(created_users),
        created_groups=tuple(created_groups),
        registered_roster_members=registered_roster_members,
        newly_approved_rosters=newly_approved_rosters,
    )


def _ensure_roster_memberships(
    loaded_profile: "LoadedProfile",
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Register every simulation persona naming a `SimulationRoster` in its
    `pre_approved_rosters` onto that roster's own store, then approve the
    roster the first time it's ever provisioned.

    This never imports or names a specific agent — `SimulationRoster.open` is
    the profile's own declared store factory, and only the three methods
    `SimulationRoster` documents (`register_member`, `roster_is_approved`,
    `approve_roster`) plus `list_members` are called on whatever it returns.

    `register_member` is an idempotent upsert (safe to call every restart, the
    same as `ensure_user_exists`/`ensure_group_exists`), but `approve_roster`
    is not scoped to any particular member — it marks *every* row currently in
    that roster's table approved. Calling it unconditionally on every restart
    would silently re-approve a real, still-pending member an operator hasn't
    approved yet. So this only ever calls it the first time a given roster has
    no approval record at all (`roster_is_approved()` is False) — mirroring the
    "create if missing, never touch again" idiom used for users/groups above,
    applied to "has this roster ever been approved" instead of per-row
    existence. Once any approval exists (from this routine, a real commander,
    or a profile's own seed data), it is never called again automatically.
    """

    personas_by_roster_key: dict[str, list] = {}
    for persona in loaded_profile.simulation_users:
        for roster_key in persona.pre_approved_rosters:
            personas_by_roster_key.setdefault(roster_key, []).append(persona)

    if not personas_by_roster_key:
        return (), ()

    rosters_by_key = {roster.key: roster for roster in loaded_profile.simulation_rosters}

    registered_members: list[tuple[str, str]] = []
    newly_approved: list[str] = []

    for roster_key, personas in personas_by_roster_key.items():
        roster = rosters_by_key[roster_key]  # profiles.loader already validated this resolves
        store = roster.open(roster.db_path)
        already_registered = {member["telegram_identity"] for member in store.list_members(approved_only=False)}
        roster_was_approved = store.roster_is_approved()

        newly_registered_ids: list[str] = []
        for persona in personas:
            telegram_id = simulation_user_telegram_id(persona.offset)
            store.register_member(telegram_id, persona.full_name)
            if telegram_id not in already_registered:
                registered_members.append((roster_key, telegram_id))
                newly_registered_ids.append(telegram_id)

        if not roster_was_approved:
            # First-ever approval: approve all currently-registered members in one commander action.
            store.approve_roster(roster.approved_by)
            newly_approved.append(roster_key)
        elif newly_registered_ids:
            # Roster was already approved by a real commander or a previous provisioning run.
            # approve_roster() marks EVERY member, which could silently approve real users
            # that an operator deliberately left pending.  Instead, approve only the simulation
            # personas that were just registered now, one by one.
            for telegram_id in newly_registered_ids:
                store.approve_member(telegram_id)

    return tuple(registered_members), tuple(newly_approved)
