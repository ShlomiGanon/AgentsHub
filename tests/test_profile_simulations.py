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


# -- profiles/simulation_provisioning.py --------------------------------------


def _fake_loaded_profile(simulation_users=(), simulation_groups=()):
    return SimpleNamespace(simulation_users=simulation_users, simulation_groups=simulation_groups)


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
