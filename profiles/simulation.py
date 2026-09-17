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
from datetime import datetime
from typing import Any, Callable, Mapping

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

    `pre_approved_rosters` names zero or more `SimulationRoster.key` values this
    persona should also be registered on (and have that roster approved for, the
    first time it's ever provisioned) — for agents that keep their own separate
    approved-roster store outside the main `users` table (e.g. `TeamStatusAgent`).
    Most personas need none of this and leave it `()`.
    """

    key: str
    offset: int
    permission_level: str = "viewer"
    full_name: str = ""
    pre_approved_rosters: tuple[str, ...] = ()


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
class SimulationRoster:
    """One agent-owned approved-roster store a profile wants simulation personas
    pre-registered and pre-approved on, declared once and referenced by key from
    any `SimulationPersona.pre_approved_rosters` that needs it.

    Some agents (e.g. `TeamStatusAgent`) keep their own separate persistence for
    an approved membership roster, entirely outside the main `users` table
    `ensure_user_exists` provisions — so a simulation persona can authenticate
    and send messages yet still be refused by that agent's own tools until it's
    also registered and approved there. `SimulationRoster` lets a profile close
    that gap declaratively, the same "declare, don't construct" way it declares
    everything else here, without `profiles.simulation_provisioning` needing to
    import or name any specific agent class.

    `open` is the store's own `open_*_persistence(db_path)` factory — exactly
    the one the owning agent's class already uses (e.g.
    `persistence.open_team_status_persistence`) — returning any object exposing
    `register_member(telegram_identity, full_name, registered_at=None)`,
    `approve_roster(approved_by, approved_at=None)`, `roster_is_approved()`, and
    `list_members(approved_only=True)`. Any current or future agent whose roster
    store has this same shape can be targeted this way, not just `TeamStatusAgent`.

    `approved_by` is the identity recorded as having approved the roster the one
    time `ensure_simulation_entities` triggers that approval (see there for why
    it only ever does this once per roster).
    """

    key: str
    open: Callable[[str], Any]
    db_path: str
    approved_by: str = "simulation-provisioning"


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
    # Optional metadata imported from an official fixture.  Keeping this
    # separate from ``raw`` lets profile declarations remain compatible with
    # the original admin-simulator shape while exposing one canonical contract
    # to API/UI/runtime consumers.
    official_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def scenario_id(self) -> str:
        metadata = self.official_metadata
        scenario = self.raw.get("scenario", {}) if isinstance(self.raw, Mapping) else {}
        return str(metadata.get("scenario_id") or scenario.get("id") or self.key)

    @property
    def domain(self) -> str | None:
        value = self.official_metadata.get("domain")
        return str(value) if value is not None else None

    @property
    def phase(self) -> str | None:
        value = self.official_metadata.get("phase")
        return str(value) if value is not None else None

    def canonical_raw(self) -> dict:
        """Return the admin-simulator contract enriched with official metadata.

        ``raw`` remains the backwards-compatible declaration.  Canonical
        scenario metadata is merged at the one server-side materialization
        boundary so expected actions and event-stream fields cannot disappear
        between fixtures, profiles and the browser.
        """

        import copy

        result = copy.deepcopy(dict(self.raw))
        scenario = result.setdefault("scenario", {})
        if not isinstance(scenario, dict):
            scenario = {}
            result["scenario"] = scenario
        if self.scenario_id:
            scenario.setdefault("id", self.scenario_id)
        if self.domain is not None:
            scenario["domain"] = self.domain
        if self.phase is not None:
            scenario["phase"] = self.phase
        for key in ("title", "description"):
            if self.official_metadata.get(key) is not None:
                scenario.setdefault(key, self.official_metadata[key])
        expected = self.official_metadata.get("expected_agent_actions")
        if expected is not None:
            scenario["expected_agent_actions"] = copy.deepcopy(list(expected))

        official_stream = self.official_metadata.get("event_stream")
        if official_stream is not None:
            result["event_stream"] = copy.deepcopy(list(official_stream))

        stream_by_step = {
            int(entry["step"]): entry
            for entry in self.official_metadata.get("event_stream", ())
            if isinstance(entry, Mapping) and str(entry.get("step", "")).isdigit()
        }
        for step in result.get("steps", ()):
            if not isinstance(step, dict):
                continue
            entry = stream_by_step.get(int(step.get("step", -1)))
            if entry is None:
                continue
            payload = entry.get("payload") if isinstance(entry.get("payload"), Mapping) else {}
            step.setdefault("timestamp", entry.get("timestamp"))
            step.setdefault("source_chat", entry.get("source_chat"))
            step.setdefault("target_agent", entry.get("target_agent"))
            step.setdefault("message", payload.get("message", step.get("text")))

        return result


@dataclass(frozen=True)
class SimulationStepContext:
    """Trusted metadata attached to one manually released simulation step."""

    scenario_id: str
    scenario_step: int
    scenario_time: str

    def __post_init__(self) -> None:
        if not self.scenario_id or not isinstance(self.scenario_id, str):
            raise ValueError("scenario_id must be a non-empty string")
        if type(self.scenario_step) is not int or self.scenario_step < 1:
            raise ValueError("scenario_step must be a positive integer")
        if not isinstance(self.scenario_time, str) or not self.scenario_time:
            raise ValueError("scenario_time must be a non-empty ISO-8601 string")
        try:
            datetime.fromisoformat(self.scenario_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("scenario_time must be ISO-8601") from exc


@dataclass(frozen=True)
class SimulationEntityResolution:
    reference: str
    canonical_id: str | None
    status: str
    domain: str


def resolve_simulation_entity(
    reference: str,
    *,
    domain: str,
    canonical_ids: tuple[str, ...] | list[str],
    aliases: Mapping[str, str] | None = None,
) -> SimulationEntityResolution:
    """Resolve an exact canonical ID or declared alias; never fuzzy-match."""

    if not isinstance(reference, str) or not reference.strip():
        return SimulationEntityResolution(str(reference), None, "unresolved", domain)
    canonical = {str(item).casefold(): str(item) for item in canonical_ids}
    normalized = " ".join(reference.strip().split()).casefold()
    resolved = canonical.get(normalized)
    if resolved is None and aliases:
        normalized_aliases = {
            " ".join(str(key).strip().split()).casefold(): str(value)
            for key, value in aliases.items()
        }
        resolved_alias = normalized_aliases.get(normalized)
        if resolved_alias is not None:
            resolved = canonical.get(str(resolved_alias).casefold())
    if resolved is None:
        return SimulationEntityResolution(reference, None, "unresolved", domain)
    return SimulationEntityResolution(reference, resolved, "resolved", domain)


def resolve_simulation_step(
    scenarios: tuple[SimulationScenario, ...] | list[SimulationScenario],
    groups: tuple[SimulationGroup, ...] | list[SimulationGroup],
    *,
    users: tuple[SimulationPersona, ...] | list[SimulationPersona] = (),
    scenario_id: str,
    scenario_step: int,
    sender_identity: str,
    chat_id: str,
    chat_type: str,
) -> SimulationStepContext:
    """Resolve and authenticate a step against profile-declared simulation data.

    The caller supplies only the identifiers carried by the trusted simulator
    process.  The timestamp is accepted only when it is read from the matching
    declared step, never from an HTTP/body value.
    """

    for scenario in scenarios:
        if scenario.scenario_id != scenario_id:
            continue
        canonical = scenario.canonical_raw()
        chats = {str(chat.get("key")): chat for chat in canonical.get("chats", ()) if isinstance(chat, Mapping)}
        steps = {
            int(step.get("step")): step
            for step in canonical.get("steps", ())
            if isinstance(step, Mapping) and str(step.get("step", "")).isdigit()
        }
        step = steps.get(scenario_step)
        if step is None:
            break
        chat = chats.get(str(step.get("chat")))
        if chat is None:
            break
        declared_sender = str(step.get("sender_identity") or "")
        if users and declared_sender:
            persona = next((item for item in users if item.key == declared_sender), None)
            if persona is None or simulation_user_telegram_id(persona.offset) != sender_identity:
                break
        expected_type = str(chat.get("telegram_chat_type") or "private")
        expected_chat = str(chat.get("telegram_chat_id") or "")
        if expected_type == "private":
            expected_chat = sender_identity
        else:
            group_key = chat.get("telegram_chat_id")
            group = next((item for item in groups if item.key == group_key), None)
            if group is None:
                break
            expected_chat = simulation_group_chat_id(group.offset)
        if expected_type != chat_type or (expected_type == "private" and expected_chat != sender_identity):
            break
        if expected_type != "private" and expected_chat != chat_id:
            break
        timestamp = step.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"simulation step {scenario_id}/{scenario_step} has no timestamp")
        return SimulationStepContext(scenario_id, scenario_step, timestamp)

    raise ValueError("simulation step does not match a declared profile scenario")
