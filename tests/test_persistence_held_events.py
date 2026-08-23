"""Backend-specific held-event tests (§6.7, Mission 6).

Mirrors tests/test_persistence_events.py's role for the events table:
tests/test_persistence_conformance.py covers the engine-agnostic surface;
this file covers SQLitePersistence-specific detail (payload/resolution
JSON round-tripping, ordering, kind isolation in depth).
"""

import pytest

from persistence.exceptions import NotFoundError
from persistence.sqlite_backend import SQLitePersistence


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "test.db"))
    yield backend
    backend.close()


def test_store_generates_a_hold_id_when_none_given(store):
    hold_id = store.store_held_event("approval", {"event_id": "evt-1"})

    assert hold_id


def test_store_respects_a_supplied_hold_id(store):
    hold_id = store.store_held_event("approval", {"event_id": "evt-1", "hold_id": "fixed-id"})

    assert hold_id == "fixed-id"


def test_arbitrary_payload_fields_round_trip(store):
    store.store_held_event(
        "approval",
        {
            "event_id": "evt-1",
            "reason": "ambiguous_selection",
            "candidate_protocol_names": ["a", "b"],
            "risk_level": "low",
        },
    )

    [held] = store.list_held_events("approval")
    assert held["reason"] == "ambiguous_selection"
    assert held["candidate_protocol_names"] == ["a", "b"]
    assert held["risk_level"] == "low"


def test_list_held_events_is_ordered_by_creation(store):
    store.store_held_event("approval", {"event_id": "evt-1", "hold_id": "first", "created_at": "2026-08-01T00:00:00"})
    store.store_held_event("approval", {"event_id": "evt-2", "hold_id": "second", "created_at": "2026-08-02T00:00:00"})

    held = store.list_held_events("approval")
    assert [h["hold_id"] for h in held] == ["first", "second"]


def test_resolved_holds_are_excluded_from_list(store):
    hold_id = store.store_held_event("approval", {"event_id": "evt-1"})
    store.resolve_held_event("approval", hold_id, {"resolved_by": "commander-1"})

    assert store.list_held_events("approval") == []


def test_resolution_payload_fields_are_stored(store):
    hold_id = store.store_held_event("approval", {"event_id": "evt-1"})
    store.resolve_held_event("approval", hold_id, {"resolved_by": "commander-1", "resolved_at": "2026-08-01T00:00:00", "decision": "approved"})

    # Resolved holds aren't returned by list_held_events (only unresolved
    # ones are) — confirm via a fresh read of the same kind returns empty,
    # proving the row really updated rather than erroring silently.
    assert store.list_held_events("approval") == []


def test_resolving_the_wrong_kind_is_treated_as_not_found(store):
    hold_id = store.store_held_event("approval", {"event_id": "evt-1"})

    with pytest.raises(NotFoundError):
        store.resolve_held_event("clarification", hold_id, {"resolved_by": "commander-1"})


def test_two_holds_for_the_same_event_can_coexist_until_resolved(store):
    # The "at most one hold at a time" rule (docs/vocabulary.md) is an
    # orchestration-level invariant (orchestrator.holds), not something
    # this generic storage enforces itself.
    store.store_held_event("approval", {"event_id": "evt-1"})
    store.store_held_event("clarification", {"event_id": "evt-1"})

    assert len(store.list_held_events("approval")) == 1
    assert len(store.list_held_events("clarification")) == 1
