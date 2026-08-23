from registries.event_types import EventTypeRegistry, build_event_type_registry


def test_is_valid_for_declared_type():
    registry = EventTypeRegistry(types=("fire", "medical", "human_activation"))

    assert registry.is_valid("fire")
    assert registry.is_valid("human_activation")


def test_is_not_valid_for_undeclared_type():
    registry = EventTypeRegistry(types=("fire", "medical", "human_activation"))

    assert not registry.is_valid("earthquake")
    assert not registry.is_valid("")


def test_build_from_loaded_profile_carries_its_event_types(monkeypatch):
    monkeypatch.setenv("AGENTSHUB_FIXTURE_BOT_TOKEN", "token")
    monkeypatch.setenv("AGENTSHUB_FIXTURE_MODEL_KEY", "key")

    from profiles.loader import load_profile

    loaded = load_profile("fixtures.profiles.minimal_profile")
    registry = build_event_type_registry(loaded)

    assert registry.types == loaded.event_types
    assert registry.is_valid("fire")
    assert registry.is_valid("human_activation")
