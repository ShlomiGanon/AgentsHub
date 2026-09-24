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

import importlib
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

    _run_operational_seed(loaded_profile)

    return ProvisioningResult(
        created_users=tuple(created_users),
        created_groups=tuple(created_groups),
        registered_roster_members=registered_roster_members,
        newly_approved_rosters=newly_approved_rosters,
    )


def _run_operational_seed(loaded_profile: "LoadedProfile") -> None:
    """Optional extension point (docs/responce_improve.md's provisioning
    stage): a profile module may declare a module-level `OPERATIONAL_SEED`
    callable (no arguments) doing its own "create if missing, never touch if
    present" seeding of profile-owned operational state that isn't a
    simulation user/group/roster -- e.g. `profiles.response_team`'s
    cameras/drones and today's attendance cycle. Absent for every profile
    that doesn't declare it (every existing profile/fixture), so this is a
    no-op for all of them.

    Resolved via `importlib.import_module(loaded_profile.module_path)`
    (a dynamic, string-keyed import already used identically by
    `profiles.loader._import_profile_module`) rather than a static import of
    any specific profile module, so this shared routine never names or
    imports a specific profile -- keeping the "new persistence module(s) ...
    imported only by Response Team agents" architecture rule intact even
    though the *call* happens from here.

    `module_path` is read via `getattr` (default `None`) and a missing/
    unimportable module is treated as "nothing to seed", not an error --
    several tests exercise this routine against a hand-built `SimpleNamespace`
    standing in for a real `LoadedProfile` (predating this field, same as
    every other optional `LoadedProfile` attribute read via `getattr`
    elsewhere in this package), and a real `LoadedProfile` always carries a
    module that already imported successfully during `profiles.loader.
    load_profile` itself.
    """

    module_path = getattr(loaded_profile, "module_path", None)
    if not module_path:
        return
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return
    seed = getattr(module, "OPERATIONAL_SEED", None)
    if seed is not None:
        seed()


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

        for persona in personas:
            telegram_id = simulation_user_telegram_id(persona.offset)
            store.register_member(telegram_id, persona.full_name)
            if telegram_id not in already_registered:
                registered_members.append((roster_key, telegram_id))

        if not store.roster_is_approved():
            store.approve_roster(roster.approved_by)
            newly_approved.append(roster_key)

    return tuple(registered_members), tuple(newly_approved)
