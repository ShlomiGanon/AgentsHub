"""Backend-swap conformance suite (work_plan.md §2.11).

Written against persistence.interface only. The `persistence` fixture
below is the *only* place a concrete engine is named — adding a second
backend means adding its class to IMPLEMENTATIONS and nothing else in this
file changes. No test body may reference SQLite, a SQL string, or a file
layout; passing this suite unchanged is the definition of a valid
replacement engine.
"""

import pytest

from persistence.exceptions import NotFoundError
from persistence.sqlite_backend import SQLitePersistence

IMPLEMENTATIONS = [SQLitePersistence]


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda cls: cls.__name__)
def persistence(request, tmp_path):
    backend = request.param(str(tmp_path / "conformance.db"))
    yield backend
    backend.close()


def _minimal_event(**overrides):
    event = {
        "received_at": "2026-08-01T10:00:00",
        "source": "sensor",
        "sender_identity": "sensor-1",
        "occurred_at": "2026-08-01T10:00:00",
        "raw_text": "text",
    }
    event.update(overrides)
    return event


# -- Events ---------------------------------------------------------------


def test_append_and_fetch_events_range(persistence):
    event_id = persistence.append_event(_minimal_event())

    events = persistence.fetch_events_range("2026-01-01", "2026-12-31")
    assert event_id in [e["event_id"] for e in events]


def test_fetch_events_range_with_no_rows_returns_empty_list(persistence):
    assert persistence.fetch_events_range("2020-01-01", "2020-12-31") == []


def test_fetch_events_by_type_area_window_with_no_match_returns_empty_list(persistence):
    persistence.append_event(_minimal_event(classification="fire", area="north"))

    assert persistence.fetch_events_by_type_area_window("medical", "south", "2000-01-01", "2100-01-01") == []


def test_update_event_merges_fields(persistence):
    event_id = persistence.append_event(_minimal_event())

    persistence.update_event(event_id, {"risk_level": "low"})

    [event] = persistence.fetch_events_range("2026-01-01", "2026-12-31")
    assert event["risk_level"] == "low"


def test_update_unknown_event_raises_not_found(persistence):
    with pytest.raises(NotFoundError):
        persistence.update_event("nonexistent", {"risk_level": "low"})


# -- Summaries --------------------------------------------------------------


def test_write_and_fetch_summary(persistence):
    persistence.write_summary("daily", {
        "summary_text": "x",
        "period_start": "2026-08-01",
        "period_end": "2026-08-02",
        "generated_at": "2026-08-02",
        "event_index": [{"event_id": "e1"}],
    })

    summaries = persistence.fetch_summaries_range("daily", "2026-08-01", "2026-08-02")
    assert len(summaries) == 1
    assert summaries[0]["event_index"] == [{"event_id": "e1"}]


def test_fetch_summaries_range_with_no_rows_returns_empty_list(persistence):
    assert persistence.fetch_summaries_range("daily", "2020-01-01", "2020-12-31") == []


def test_writing_a_summary_for_a_period_that_already_has_one_does_not_duplicate(persistence):
    period = {"period_start": "2026-08-01", "period_end": "2026-08-02"}

    persistence.write_summary("daily", {**period, "summary_text": "first", "generated_at": "2026-08-02"})
    persistence.write_summary("daily", {**period, "summary_text": "second", "generated_at": "2026-08-03"})

    summaries = persistence.fetch_summaries_range("daily", "2026-08-01", "2026-08-02")
    assert len(summaries) == 1
    assert summaries[0]["summary_text"] == "second"


# -- Users --------------------------------------------------------------


def test_user_crud_round_trip(persistence):
    persistence.write_user("100", "commander")

    assert persistence.read_user("100") == {"telegram_identity": "100", "permission_level": "commander"}
    assert persistence.list_users() == [{"telegram_identity": "100", "permission_level": "commander"}]

    persistence.delete_user("100")
    assert persistence.read_user("100") is None


def test_deleting_an_unknown_user_raises_not_found(persistence):
    with pytest.raises(NotFoundError):
        persistence.delete_user("nonexistent")


# -- Held events (not yet implemented — owned by §6.2/§6.7) -----------------


def test_held_event_operations_are_not_yet_implemented(persistence):
    # Includes 2.11's named failure case ("resolving a hold that is not
    # held") — held-event storage doesn't exist yet, so every operation
    # here raises NotImplementedError rather than a domain-specific error.
    # Update this test to expect that domain error once §6.2/§6.7 land.
    with pytest.raises(NotImplementedError):
        persistence.store_held_event("clarification", {})

    with pytest.raises(NotImplementedError):
        persistence.list_held_events("clarification")

    with pytest.raises(NotImplementedError):
        persistence.resolve_held_event("approval", "never-existed", {})
