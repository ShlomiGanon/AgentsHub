from registries.areas import AreaRegistry, build_area_registry


def test_is_valid_for_declared_area():
    registry = AreaRegistry(areas=("north_sector", "south_sector"))

    assert registry.is_valid("north_sector")


def test_is_not_valid_for_undeclared_area():
    registry = AreaRegistry(areas=("north_sector", "south_sector"))

    assert not registry.is_valid("east_sector")
    assert not registry.is_valid("")


def test_build_from_loaded_profile_carries_its_areas(monkeypatch):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key")

    from profiles.loader import load_profile

    loaded = load_profile("fixtures.profiles.minimal_profile")
    registry = build_area_registry(loaded)

    assert registry.areas == loaded.areas
    assert registry.is_valid("north_sector")
