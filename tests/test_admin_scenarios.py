import sys
from types import ModuleType

import pytest

from api.admin_scenarios import GROUP_SOURCES, ScenarioMappingError, map_legacy_scenario, scenario_catalog
from run_stack import reset_profile_databases


def test_all_six_bundled_scenarios_load_with_expected_step_counts():
    catalog = scenario_catalog()
    assert len(catalog) == 6
    assert [item["step_count"] for item in catalog] == [9, 9, 10, 7, 7, 9]


def test_mapping_writes_real_ids_and_server_names_without_routing_override():
    example = scenario_catalog()[0]
    persona_ids = {persona: str(1000 + index) for index, persona in enumerate(example["personas"], 1)}
    group_ids = {source: str(-2000 - index) for index, source in enumerate(example["group_sources"], 1)}
    first_identity = next(iter(persona_ids.values()))

    mapped = map_legacy_scenario(
        example["raw"], persona_ids, group_ids,
        {first_identity: {"permission_level": "viewer", "full_name": "Dana Levi"}},
    )

    assert len(mapped["steps"]) == 9
    assert mapped["steps"][0]["sender_name"] == "Dana Levi"
    assert any(step["sender_name"] == "לא רשום" for step in mapped["steps"])
    assert all("protocol_hint" not in step for step in mapped["steps"])
    assert all(chat.get("telegram_chat_id", "-").startswith("-") for chat in mapped["chats"] if chat["telegram_chat_type"] != "private")
    assert {step["semantic_agent"] for step in mapped["steps"] if step["semantic_agent"]} == {
        "team_status_agent", "surveillance_agent", "friendly_forces_agent"
    }


def test_mapping_rejects_non_telegram_shaped_ids():
    example = scenario_catalog()[0]
    people = {persona: "123" for persona in example["personas"]}
    groups = {source: "-456" for source in example["group_sources"]}
    people[example["personas"][0]] = "not-an-id"
    with pytest.raises(ScenarioMappingError):
        map_legacy_scenario(example["raw"], people, groups, {})


def test_reset_removes_only_declared_databases_and_known_sidecars(tmp_path):
    database = tmp_path / "main.db"
    settings = tmp_path / "main.db.settings.json"
    unrelated = tmp_path / "keep.txt"
    for path in (database, settings, unrelated):
        path.write_text("x", encoding="utf-8")
    module = ModuleType("profiles.test_reset_profile")
    module.DB_PATH = str(database)
    module.RESETTABLE_DATABASES = (str(database),)
    sys.modules[module.__name__] = module
    try:
        removed = reset_profile_databases(module.__name__)
    finally:
        sys.modules.pop(module.__name__, None)
    assert set(removed) == {database.resolve(), settings.resolve()}
    assert unrelated.exists()


def test_reset_refuses_a_declared_non_database_path(tmp_path):
    module = ModuleType("profiles.test_unsafe_reset_profile")
    module.DB_PATH = str(tmp_path)
    module.RESETTABLE_DATABASES = (str(tmp_path),)
    sys.modules[module.__name__] = module
    try:
        with pytest.raises(ValueError, match="non-database"):
            reset_profile_databases(module.__name__)
    finally:
        sys.modules.pop(module.__name__, None)
