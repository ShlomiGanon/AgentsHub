import pytest

from persistence.exceptions import NotFoundError
from persistence.sqlite_backend import SQLitePersistence


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "test.db"))
    yield backend
    backend.close()


def test_fresh_database_has_no_users(store):
    assert store.list_users() == []
    assert store.read_user("12345") is None


def test_write_then_read_user(store):
    store.write_user("12345", "commander")

    assert store.read_user("12345") == {"telegram_identity": "12345", "permission_level": "commander"}


def test_write_user_twice_updates_rather_than_duplicates(store):
    store.write_user("12345", "viewer")
    store.write_user("12345", "commander")

    assert store.read_user("12345")["permission_level"] == "commander"
    assert len(store.list_users()) == 1


def test_delete_user(store):
    store.write_user("12345", "viewer")
    store.delete_user("12345")

    assert store.read_user("12345") is None


def test_delete_unknown_user_raises_not_found(store):
    with pytest.raises(NotFoundError):
        store.delete_user("does-not-exist")


def test_held_event_operations_are_not_yet_implemented(store):
    # Events and summaries were implemented in Mission 2 (§2.9) — see
    # tests/test_persistence_events.py and tests/test_persistence_conformance.py.
    # Held-event storage is still owned by §6.2/§6.7 and stays unimplemented
    # here.
    with pytest.raises(NotImplementedError):
        store.store_held_event("clarification", {})

    with pytest.raises(NotImplementedError):
        store.list_held_events("clarification")

    with pytest.raises(NotImplementedError):
        store.resolve_held_event("clarification", "some-id", {})
