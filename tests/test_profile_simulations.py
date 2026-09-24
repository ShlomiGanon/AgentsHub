"""profiles/simulation.py, profiles/loader.py's simulation validation,
profiles/simulation_provisioning.py, and api/simulations.py
(docs/profile_simulations_design.md)."""

from types import SimpleNamespace

import pytest

from api.simulations import find_simulation_scenario, materialize_simulation, simulation_catalog_payload
from persistence.sqlite_store import SQLitePersistence
from profiles.loader import validate_profile
from profiles.simulation import (
    SIMULATION_GROUP_ID_BASE,
    SIMULATION_USER_ID_BASE,
    SimulationGroup,
    SimulationPersona,
    SimulationRoster,
    SimulationScenario,
    simulation_group_chat_id,
    simulation_user_telegram_id,
)
from profiles.simulation_provisioning import ensure_simulation_entities


# -- profiles/simulation.py: the reserved ID scheme --------------------------


def test_simulation_ids_are_deterministic_and_offset_based():
    assert simulation_user_telegram_id(0) == str(SIMULATION_USER_ID_BASE)
    assert simulation_user_telegram_id(5) == str(SIMULATION_USER_ID_BASE + 5)
    assert simulation_group_chat_id(0) == str(SIMULATION_GROUP_ID_BASE)
    assert simulation_group_chat_id(5) == str(SIMULATION_GROUP_ID_BASE - 5)


def test_simulation_user_ids_are_positive_digit_strings():
    """/Telegram/Admission and the admin simulator's own JS both require
    isdigit() and > 0 for a Telegram user identity — a simulation ID must
    satisfy the exact same shape as a real one."""

    identity = simulation_user_telegram_id(0)
    assert identity.isdigit() and int(identity) > 0


def test_simulation_group_ids_are_negative_digit_strings():
    chat_id = simulation_group_chat_id(0)
    assert chat_id.startswith("-") and chat_id[1:].isdigit() and int(chat_id) < 0


def test_simulation_ids_cannot_collide_with_a_real_telegram_id():
    """Telegram documents a real ID as at most 52 significant bits; a
    simulation ID must sit strictly above that (users) or below its negation
    (groups), and still inside the browser's Number.MAX_SAFE_INTEGER
    (2**53 - 1) since the admin simulator's own JS does Number() arithmetic
    on these values."""

    telegram_real_id_ceiling = 2**52
    js_max_safe_integer = 2**53 - 1

    assert int(simulation_user_telegram_id(0)) > telegram_real_id_ceiling
    assert int(simulation_user_telegram_id(999_999)) <= js_max_safe_integer
    assert abs(int(simulation_group_chat_id(0))) > telegram_real_id_ceiling
    assert abs(int(simulation_group_chat_id(999_999))) <= js_max_safe_integer


def test_negative_offset_is_rejected():
    with pytest.raises(ValueError):
        simulation_user_telegram_id(-1)
    with pytest.raises(ValueError):
        simulation_group_chat_id(-1)


# -- profiles/loader.py: SIMULATION_USERS/SIMULATION_GROUPS/SIMULATIONS validation --


def _loaded(**overrides):
    base = dict(
        profile_name="For Tests", default_language="en", max_iter=8, model_timeout_seconds=30.0,
        agents=(), protocols=(), areas=("x",), simulation_users=(), simulation_groups=(), simulations=(),
        simulation_rosters=(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_profile_declaring_no_simulations_is_unaffected():
    assert validate_profile(_loaded(), declared_event_types=["fire"]) == []


def test_missing_simulation_fields_default_to_empty_like_every_other_optional_field():
    """Some test doubles (e.g. this file's own _loaded(), and other suites') predate
    this field entirely — validate_profile must not require it to be set."""

    loaded = SimpleNamespace(
        profile_name="For Tests", default_language="en", max_iter=8, model_timeout_seconds=30.0,
        agents=(), protocols=(), areas=("x",),
    )
    assert validate_profile(loaded, declared_event_types=["fire"]) == []


def test_wrong_type_in_simulation_users_is_rejected():
    failures = validate_profile(_loaded(simulation_users=("not-a-persona",)), declared_event_types=["fire"])
    assert any("SIMULATION_USERS[0]" in f for f in failures)


def test_wrong_type_in_simulation_groups_is_rejected():
    failures = validate_profile(_loaded(simulation_groups=("not-a-group",)), declared_event_types=["fire"])
    assert any("SIMULATION_GROUPS[0]" in f for f in failures)


def test_wrong_type_in_simulations_is_rejected():
    failures = validate_profile(_loaded(simulations=("not-a-scenario",)), declared_event_types=["fire"])
    assert any("SIMULATIONS[0]" in f for f in failures)


def test_duplicate_persona_key_is_rejected():
    users = (
        SimulationPersona(key="a", offset=0),
        SimulationPersona(key="a", offset=1),
    )
    failures = validate_profile(_loaded(simulation_users=users), declared_event_types=["fire"])
    assert any("duplicate key" in f and "SIMULATION_USERS" in f for f in failures)


def test_duplicate_persona_offset_is_rejected():
    users = (
        SimulationPersona(key="a", offset=0),
        SimulationPersona(key="b", offset=0),
    )
    failures = validate_profile(_loaded(simulation_users=users), declared_event_types=["fire"])
    assert any("duplicate offset" in f and "SIMULATION_USERS" in f for f in failures)


def test_duplicate_group_key_and_offset_are_rejected():
    groups = (
        SimulationGroup(key="g", offset=0),
        SimulationGroup(key="g", offset=1),
    )
    failures = validate_profile(_loaded(simulation_groups=groups), declared_event_types=["fire"])
    assert any("duplicate key" in f and "SIMULATION_GROUPS" in f for f in failures)


def test_duplicate_scenario_key_is_rejected():
    raw = {"scenario": {}, "chats": [], "steps": []}
    scenarios = (
        SimulationScenario(key="s", title="t1", raw=raw),
        SimulationScenario(key="s", title="t2", raw=raw),
    )
    failures = validate_profile(_loaded(simulations=scenarios), declared_event_types=["fire"])
    assert any("duplicate key" in f and "SIMULATIONS" in f for f in failures)


def test_scenario_raw_must_have_chats_and_steps_lists():
    scenario = SimulationScenario(key="s", title="t", raw={"scenario": {}})
    failures = validate_profile(_loaded(simulations=(scenario,)), declared_event_types=["fire"])
    assert any("raw must be a dict with 'chats' and 'steps' lists" in f for f in failures)


def test_message_step_sender_identity_must_resolve_to_a_declared_persona():
    raw = {
        "scenario": {},
        "chats": [{"key": "dm", "kind": "message", "telegram_chat_type": "private"}],
        "steps": [{"step": 1, "chat": "dm", "sender_identity": "unknown_persona", "text": "hi"}],
    }
    scenario = SimulationScenario(key="s", title="t", raw=raw)
    failures = validate_profile(_loaded(simulations=(scenario,)), declared_event_types=["fire"])
    assert any("unknown_persona" in f and "SIMULATION_USERS" in f for f in failures)


def test_event_step_sender_identity_is_exempt_a_sensor_is_not_a_persona():
    raw = {
        "scenario": {},
        "chats": [{"key": "sensors", "kind": "event"}],
        "steps": [{"step": 1, "chat": "sensors", "sender_identity": "sensor-north-1", "text": "smoke"}],
    }
    scenario = SimulationScenario(key="s", title="t", raw=raw)
    assert validate_profile(_loaded(simulations=(scenario,)), declared_event_types=["fire"]) == []


def test_group_chat_telegram_chat_id_must_resolve_to_a_declared_group():
    raw = {
        "scenario": {},
        "chats": [
            {"key": "team", "kind": "message", "telegram_chat_type": "supergroup", "telegram_chat_id": "unknown_group"}
        ],
        "steps": [],
    }
    scenario = SimulationScenario(key="s", title="t", raw=raw)
    failures = validate_profile(_loaded(simulations=(scenario,)), declared_event_types=["fire"])
    assert any("unknown_group" in f and "SIMULATION_GROUPS" in f for f in failures)


def test_wrong_type_in_simulation_rosters_is_rejected():
    failures = validate_profile(_loaded(simulation_rosters=("not-a-roster",)), declared_event_types=["fire"])
    assert any("SIMULATION_ROSTERS[0]" in f for f in failures)


def test_duplicate_roster_key_is_rejected():
    rosters = (
        SimulationRoster(key="r", open=lambda path: None, db_path="a"),
        SimulationRoster(key="r", open=lambda path: None, db_path="b"),
    )
    failures = validate_profile(_loaded(simulation_rosters=rosters), declared_event_types=["fire"])
    assert any("duplicate key" in f and "SIMULATION_ROSTERS" in f for f in failures)


def test_persona_pre_approved_roster_must_resolve_to_a_declared_roster():
    users = (SimulationPersona(key="a", offset=0, pre_approved_rosters=("unknown_roster",)),)
    failures = validate_profile(_loaded(simulation_users=users), declared_event_types=["fire"])
    assert any("unknown_roster" in f and "SIMULATION_ROSTERS" in f for f in failures)


def test_a_fully_valid_simulation_declaration_reports_no_failures():
    users = (SimulationPersona(key="viewer", offset=0, permission_level="viewer"),)
    groups = (SimulationGroup(key="team", offset=0, agent_name="reference_agent"),)
    raw = {
        "scenario": {"id": "s", "title": "t"},
        "chats": [
            {"key": "dm", "kind": "message", "telegram_chat_type": "private"},
            {"key": "team", "kind": "message", "telegram_chat_type": "supergroup", "telegram_chat_id": "team"},
        ],
        "steps": [
            {"step": 1, "chat": "dm", "sender_identity": "viewer", "text": "hi"},
        ],
    }
    scenario = SimulationScenario(key="s", title="t", raw=raw)
    failures = validate_profile(
        _loaded(simulation_users=users, simulation_groups=groups, simulations=(scenario,)),
        declared_event_types=["fire"],
    )
    assert failures == []


def test_a_persona_pre_approved_on_a_declared_roster_reports_no_failures():
    roster = SimulationRoster(key="team_status", open=lambda path: None, db_path="db")
    users = (SimulationPersona(key="a", offset=0, pre_approved_rosters=("team_status",)),)
    failures = validate_profile(
        _loaded(simulation_users=users, simulation_rosters=(roster,)), declared_event_types=["fire"]
    )
    assert failures == []


# -- profiles/simulation_provisioning.py --------------------------------------


def _fake_loaded_profile(simulation_users=(), simulation_groups=(), simulation_rosters=()):
    return SimpleNamespace(
        simulation_users=simulation_users,
        simulation_groups=simulation_groups,
        simulation_rosters=simulation_rosters,
    )


class _FakeRosterStore:
    """Minimal object matching only the shape `SimulationRoster.open` documents
    (`register_member`/`approve_roster`/`roster_is_approved`/`list_members`) — used
    to prove `ensure_simulation_entities` never assumes anything beyond that shape,
    i.e. never imports or names a specific agent (docs/profile_simulations_design.md)."""

    def __init__(self, already_approved=False):
        self.members: dict[str, str] = {}
        self._approved = already_approved
        self.approve_calls: list[str] = []

    def register_member(self, telegram_identity, full_name, registered_at=None):
        self.members[telegram_identity] = full_name

    def approve_roster(self, approved_by, approved_at=None):
        self.approve_calls.append(approved_by)
        self._approved = True
        return len(self.members)

    def roster_is_approved(self):
        return self._approved

    def list_members(self, *, approved_only=True):
        return [{"telegram_identity": telegram_id} for telegram_id in self.members]


def test_ensure_simulation_entities_creates_declared_users_and_groups(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        loaded = _fake_loaded_profile(
            simulation_users=(
                SimulationPersona(key="commander", offset=0, permission_level="commander", full_name="C"),
                SimulationPersona(key="viewer", offset=1, permission_level="viewer", full_name="V"),
            ),
            simulation_groups=(SimulationGroup(key="team", offset=0, agent_name="reference_agent", label="T"),),
        )

        result = ensure_simulation_entities(persistence, loaded)

        assert set(result.created_users) == {simulation_user_telegram_id(0), simulation_user_telegram_id(1)}
        assert result.created_groups == (simulation_group_chat_id(0),)
        commander = persistence.read_user(simulation_user_telegram_id(0))
        assert commander == {
            "telegram_identity": simulation_user_telegram_id(0), "permission_level": "commander",
            "full_name": "C", "auto_register": False,
        }
        group = persistence.read_group(simulation_group_chat_id(0))
        assert group["agent_name"] == "reference_agent"
        assert group["auto_register"] is False
    finally:
        persistence.close()


def test_ensure_simulation_entities_is_idempotent_and_never_overwrites(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        loaded = _fake_loaded_profile(
            simulation_users=(SimulationPersona(key="viewer", offset=0, permission_level="viewer", full_name="V"),),
        )

        first = ensure_simulation_entities(persistence, loaded)
        assert first.created_users == (simulation_user_telegram_id(0),)

        persistence.write_user(simulation_user_telegram_id(0), "viewer", "Edited by an operator")
        second = ensure_simulation_entities(persistence, loaded)

        assert second.created_users == ()
        assert persistence.read_user(simulation_user_telegram_id(0))["full_name"] == "Edited by an operator"
    finally:
        persistence.close()


def test_ensure_simulation_entities_defaults_to_nothing():
    """A profile declaring neither (every profile before this feature) provisions nothing."""

    result = ensure_simulation_entities(persistence=None, loaded_profile=_fake_loaded_profile())
    assert result.created_users == ()
    assert result.created_groups == ()
    assert result.registered_roster_members == ()
    assert result.newly_approved_rosters == ()


def test_ensure_simulation_entities_registers_and_approves_a_fresh_roster(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        store = _FakeRosterStore(already_approved=False)
        roster = SimulationRoster(key="team_status", open=lambda path: store, db_path="ignored", approved_by="cmdr")
        personas = (
            SimulationPersona(key="a", offset=0, full_name="A", pre_approved_rosters=("team_status",)),
            SimulationPersona(key="b", offset=1, full_name="B", pre_approved_rosters=("team_status",)),
            SimulationPersona(key="c", offset=2, full_name="C"),  # not on this roster
        )
        loaded = _fake_loaded_profile(simulation_users=personas, simulation_rosters=(roster,))

        result = ensure_simulation_entities(persistence, loaded)

        assert store.members == {simulation_user_telegram_id(0): "A", simulation_user_telegram_id(1): "B"}
        assert store.approve_calls == ["cmdr"]
        assert store.roster_is_approved()
        assert set(result.registered_roster_members) == {
            ("team_status", simulation_user_telegram_id(0)),
            ("team_status", simulation_user_telegram_id(1)),
        }
        assert result.newly_approved_rosters == ("team_status",)
    finally:
        persistence.close()


def test_ensure_simulation_entities_never_re_approves_an_already_approved_roster(tmp_path):
    """approve_roster() (the existing, reused method) marks EVERY row in that roster's
    table approved, with no per-member scoping — calling it again on every restart would
    risk silently auto-approving an unrelated real member an operator deliberately left
    pending. So it is only ever called the first time a roster has no approval at all."""

    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        store = _FakeRosterStore(already_approved=True)
        roster = SimulationRoster(key="team_status", open=lambda path: store, db_path="ignored")
        persona = SimulationPersona(key="a", offset=0, full_name="A", pre_approved_rosters=("team_status",))
        loaded = _fake_loaded_profile(simulation_users=(persona,), simulation_rosters=(roster,))

        result = ensure_simulation_entities(persistence, loaded)

        assert store.approve_calls == []
        assert result.newly_approved_rosters == ()
        # registration itself is a plain idempotent upsert, unaffected by approval state
        assert result.registered_roster_members == (("team_status", simulation_user_telegram_id(0)),)
    finally:
        persistence.close()


def test_ensure_simulation_entities_roster_registration_reports_only_newly_registered_members(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        store = _FakeRosterStore(already_approved=False)
        roster = SimulationRoster(key="team_status", open=lambda path: store, db_path="ignored")
        persona = SimulationPersona(key="a", offset=0, full_name="A", pre_approved_rosters=("team_status",))
        loaded = _fake_loaded_profile(simulation_users=(persona,), simulation_rosters=(roster,))

        first = ensure_simulation_entities(persistence, loaded)
        assert first.registered_roster_members == (("team_status", simulation_user_telegram_id(0)),)
        assert first.newly_approved_rosters == ("team_status",)

        second = ensure_simulation_entities(persistence, loaded)
        assert second.registered_roster_members == ()  # already registered — nothing "new" to report
        assert second.newly_approved_rosters == ()  # already approved after the first call
    finally:
        persistence.close()


def test_ensure_simulation_entities_never_opens_a_roster_nothing_references(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        open_calls = []
        roster = SimulationRoster(
            key="unused", open=lambda path: open_calls.append(path) or _FakeRosterStore(), db_path="ignored"
        )
        loaded = _fake_loaded_profile(simulation_rosters=(roster,))

        result = ensure_simulation_entities(persistence, loaded)

        assert result.registered_roster_members == ()
        assert result.newly_approved_rosters == ()
        assert open_calls == []
    finally:
        persistence.close()


# -- api/simulations.py: the JSON adapter -------------------------------------


_RAW_SCENARIO = {
    "scenario": {"id": "demo", "title": "Demo"},
    "chats": [
        {"key": "dm", "kind": "message", "telegram_chat_type": "private"},
        {"key": "team", "kind": "message", "telegram_chat_type": "supergroup", "telegram_chat_id": "team"},
        {"key": "sensors", "kind": "event"},
    ],
    "steps": [
        {"step": 1, "chat": "dm", "sender_identity": "viewer", "text": "hi", "timestamp": "2026-01-01T00:00:00Z"},
        {"step": 2, "chat": "sensors", "sender_identity": "sensor-north-1", "text": "smoke"},
    ],
}


def _scenario():
    return SimulationScenario(key="demo", title="Demo", description="d", tags=("t",), raw=_RAW_SCENARIO)


def test_materialize_simulation_substitutes_only_the_declared_ids():
    users = (SimulationPersona(key="viewer", offset=3),)
    groups = (SimulationGroup(key="team", offset=7),)

    materialized = materialize_simulation(_scenario(), users, groups)

    assert materialized["scenario"] == _RAW_SCENARIO["scenario"]
    assert materialized["chats"][0] == _RAW_SCENARIO["chats"][0]  # private: untouched
    assert materialized["chats"][1]["telegram_chat_id"] == simulation_group_chat_id(7)
    assert materialized["chats"][2] == _RAW_SCENARIO["chats"][2]  # event chat: untouched
    assert materialized["steps"][0]["sender_identity"] == simulation_user_telegram_id(3)
    assert materialized["steps"][0]["timestamp"] == "2026-01-01T00:00:00Z"  # display-only field untouched
    assert materialized["steps"][1]["sender_identity"] == "sensor-north-1"  # not a persona key: untouched


def test_materialize_simulation_does_not_mutate_the_declared_raw_template():
    users = (SimulationPersona(key="viewer", offset=0),)
    materialize_simulation(_scenario(), users, ())
    assert _RAW_SCENARIO["steps"][0]["sender_identity"] == "viewer"


def test_simulation_catalog_payload_is_metadata_only():
    loaded = SimpleNamespace(simulations=(_scenario(),))
    assert simulation_catalog_payload(loaded) == [
        {"key": "demo", "title": "Demo", "description": "d", "tags": ["t"]}
    ]


def test_find_simulation_scenario_returns_none_for_an_unknown_key():
    loaded = SimpleNamespace(simulations=(_scenario(),))
    assert find_simulation_scenario(loaded, "does-not-exist") is None
    assert find_simulation_scenario(loaded, "demo") is not None


# -- the SEC_001 migration (profiles/standby_squad.py), against the real profile ------------
#
# Profile Split Plan (docs/Profile_Split_Plan.md), Step 1: repointed from profiles.unified_test
# (which declared this alongside the pilot scenario and the FIRE_002 series) to
# profiles.standby_squad (which declares SEC_001 only, offsets renumbered from 0). The
# FIRE_002-series test below this one is repointed separately, in Step 2, when
# profiles/firefighting.py is created.


def test_standby_squad_declares_the_migrated_sec001_series(test_core_model, test_sub_model, monkeypatch):
    """docs/profile_simulations_design.md: the SEC_001 series
    (fixtures/admin_scenarios/'כיתת כוננת - חלק 1/2/3.json') was migrated into real
    SIMULATIONS declarations, with recurring characters sharing one reserved ID
    across the phases they appear in — not re-declared per phase."""

    monkeypatch.setenv("BOT_TOKEN", "fake-token")
    from profiles.loader import load_profile

    loaded = load_profile("profiles.standby_squad", core_model=test_core_model, sub_model=test_sub_model)

    sec001_keys = {"sec001_phase1", "sec001_phase2", "sec001_phase3"}
    assert sec001_keys <= {s.key for s in loaded.simulations}

    for key in sec001_keys:
        scenario = find_simulation_scenario(loaded, key)
        materialized = materialize_simulation(scenario, loaded.simulation_users, loaded.simulation_groups)
        for step in materialized["steps"]:
            assert step["sender_identity"].isdigit() and int(step["sender_identity"]) > 0
        for chat in materialized["chats"]:
            if chat.get("telegram_chat_type") in ("group", "supergroup"):
                assert chat["telegram_chat_id"].startswith("-") and chat["telegram_chat_id"][1:].isdigit()

    # The recurring technician and security-officer characters resolve to the exact same
    # reserved ID in every phase they appear in.
    def _sender_ids_for(key, persona_key):
        scenario = find_simulation_scenario(loaded, key)
        materialized = materialize_simulation(scenario, loaded.simulation_users, loaded.simulation_groups)
        persona = next(p for p in loaded.simulation_users if p.key == persona_key)
        from profiles.simulation import simulation_user_telegram_id
        expected = simulation_user_telegram_id(persona.offset)
        return {step["sender_identity"] for step in materialized["steps"]} & {expected}

    for persona_key in ("yossi_technician", "site_security_officer"):
        for key in sec001_keys:
            assert _sender_ids_for(key, persona_key), f"{persona_key} missing its reserved ID in {key}"

    group_keys = [g.key for g in loaded.simulation_groups]
    assert group_keys.count("response_team") == 1
    assert {"cameras", "external_forces"}.issubset(group_keys)


def test_firefighting_declares_the_migrated_fire002_series(test_core_model, test_sub_model, monkeypatch):
    """Same migration pattern as SEC_001 (profiles/standby_squad.py), applied to the firefighting
    series. Profile Split Plan (docs/Profile_Split_Plan.md), Step 2: repointed from
    profiles.unified_test to profiles.firefighting (its own dedicated file/process, offsets
    renumbered from 0) -- despite the raw fixture reusing identical channel names
    ('TELEGRAM_GROUP_RESPONSE_TEAM' etc.) across both series."""

    monkeypatch.setenv("BOT_TOKEN", "fake-token")
    from profiles.loader import load_profile

    loaded = load_profile("profiles.firefighting", core_model=test_core_model, sub_model=test_sub_model)

    fire002_keys = {"fire002_phase1", "fire002_phase2", "fire002_phase3"}
    assert fire002_keys <= {s.key for s in loaded.simulations}

    for key in fire002_keys:
        scenario = find_simulation_scenario(loaded, key)
        materialized = materialize_simulation(scenario, loaded.simulation_users, loaded.simulation_groups)
        for step in materialized["steps"]:
            assert step["sender_identity"].isdigit() and int(step["sender_identity"]) > 0
        for chat in materialized["chats"]:
            if chat.get("telegram_chat_type") in ("group", "supergroup"):
                assert chat["telegram_chat_id"].startswith("-") and chat["telegram_chat_id"][1:].isdigit()

    def _sender_ids_for(key, persona_key):
        scenario = find_simulation_scenario(loaded, key)
        materialized = materialize_simulation(scenario, loaded.simulation_users, loaded.simulation_groups)
        persona = next(p for p in loaded.simulation_users if p.key == persona_key)
        expected = simulation_user_telegram_id(persona.offset)
        return {step["sender_identity"] for step in materialized["steps"]} & {expected}

    # Recurring characters across all three FIRE_002 phases.
    for persona_key in ("roni_surveillance_operator", "station_commander"):
        for key in fire002_keys:
            assert _sender_ids_for(key, persona_key), f"{persona_key} missing its reserved ID in {key}"

    # FIRE_002's roster and groups are the only ones profiles.firefighting declares --
    # independence from SEC_001 (profiles.standby_squad) is now structural (a separate
    # profile file/process/DB), not just a same-profile key/offset separation.
    fire_persona_keys = {
        "lahav_avi_shift_commander", "omri_firefighter", "roni_surveillance_operator", "kkl_mountains_sector",
        "police_hub_agam", "station_commander", "yuval_ashed3_commander", "citizen_reports_group",
        "fire_police_patrol", "district_fire_commander", "firefighter_team_a_4", "firefighter_team_a_5",
        "firefighter_team_a_6",
    }
    all_persona_keys = [p.key for p in loaded.simulation_users]
    assert set(all_persona_keys) == fire_persona_keys
    assert len(all_persona_keys) == len(set(all_persona_keys))  # no key or offset collisions
    fire_roster_keys = {p.key for p in loaded.simulation_users if "team_status" in p.pre_approved_rosters}
    assert fire_roster_keys == {
        "lahav_avi_shift_commander",
        "omri_firefighter",
        "yuval_ashed3_commander",
        "firefighter_team_a_4",
        "firefighter_team_a_5",
        "firefighter_team_a_6",
    }

    fire_group_keys = {"fire_response_team", "fire_cameras", "fire_external_forces"}
    group_keys = {g.key for g in loaded.simulation_groups}
    assert group_keys == fire_group_keys


def test_standby_squad_response_team_personas_become_approved_team_status_members(
    test_core_model, test_sub_model, monkeypatch, tmp_path
):
    """Closes the gap the SEC_001-attendance-step investigation found: a response-team
    persona could authenticate and post into a team_status_agent-owned group, yet
    TeamStatusAgent's record_attendance_response tool still refused it because its
    separate approved-roster store never knew about simulation personas. Runs against
    an isolated copy of the profile's own declared roster, not its real on-disk DB."""

    from dataclasses import replace

    from persistence.team_status_contracts import open_team_status_persistence
    from persistence.sqlite_store import SQLitePersistence
    from profiles.loader import load_profile

    monkeypatch.setenv("BOT_TOKEN", "fake-token")
    loaded = load_profile("profiles.standby_squad", core_model=test_core_model, sub_model=test_sub_model)

    isolated_db_path = str(tmp_path / "team_status.db")
    real_roster = next(r for r in loaded.simulation_rosters if r.key == "team_status")
    isolated_profile = SimpleNamespace(
        simulation_users=loaded.simulation_users,
        simulation_groups=(),  # not exercised here — this test is only about the roster
        simulation_rosters=(replace(real_roster, db_path=isolated_db_path),),
    )

    persistence = SQLitePersistence(str(tmp_path / "prov.db"))
    try:
        ensure_simulation_entities(persistence, isolated_profile)
    finally:
        persistence.close()

    store = open_team_status_persistence(isolated_db_path)
    approved_identities = {m["telegram_identity"] for m in store.list_members(approved_only=True)}

    pre_approved_personas = [p for p in loaded.simulation_users if p.pre_approved_rosters]
    assert pre_approved_personas, "expected at least one persona to declare pre_approved_rosters"
    for persona in pre_approved_personas:
        assert simulation_user_telegram_id(persona.offset) in approved_identities

    # A persona that merely posts into the same channel without being a team member
    # (e.g. a resident reporting in as a bystander) correctly stays off the roster.
    bystander = next(p for p in loaded.simulation_users if p.key == "resident_avraham")
    assert simulation_user_telegram_id(bystander.offset) not in approved_identities
