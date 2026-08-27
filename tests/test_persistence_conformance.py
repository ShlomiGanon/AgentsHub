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


def test_fetch_event_by_id(persistence):
    event_id = persistence.append_event(_minimal_event(raw_text="specific text"))

    event = persistence.fetch_event(event_id)

    assert event["event_id"] == event_id
    assert event["raw_text"] == "specific text"


def test_fetch_event_returns_none_for_an_unknown_id(persistence):
    assert persistence.fetch_event("does-not-exist") is None


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


# -- Held events (§6.7) -------------------------------------------------


def test_store_list_and_resolve_a_held_event(persistence):
    hold_id = persistence.store_held_event("approval", {"event_id": "evt-1", "reason": "flagged_protocol"})

    [held] = persistence.list_held_events("approval")
    assert held["hold_id"] == hold_id
    assert held["event_id"] == "evt-1"
    assert held["reason"] == "flagged_protocol"
    assert held["resolved"] is False

    persistence.resolve_held_event("approval", hold_id, {"resolved_by": "commander-1", "decision": "approved"})

    assert persistence.list_held_events("approval") == []


def test_resolving_a_hold_that_is_not_held_raises(persistence):
    # 2.11's named failure case, now against the real implementation.
    with pytest.raises(NotFoundError):
        persistence.resolve_held_event("approval", "never-existed", {"resolved_by": "commander-1"})


def test_resolving_an_already_resolved_hold_raises(persistence):
    hold_id = persistence.store_held_event("approval", {"event_id": "evt-1"})
    persistence.resolve_held_event("approval", hold_id, {"resolved_by": "commander-1"})

    with pytest.raises(NotFoundError):
        persistence.resolve_held_event("approval", hold_id, {"resolved_by": "commander-2"})


def test_list_held_events_is_scoped_to_its_kind(persistence):
    persistence.store_held_event("approval", {"event_id": "evt-1"})
    persistence.store_held_event("clarification", {"event_id": "evt-2"})

    assert len(persistence.list_held_events("approval")) == 1
    assert len(persistence.list_held_events("clarification")) == 1


def test_fetch_held_event_by_event_id_while_pending(persistence):
    hold_id = persistence.store_held_event("approval", {"event_id": "evt-1", "reason": "flagged_protocol"})

    held = persistence.fetch_held_event("approval", "evt-1")

    assert held["hold_id"] == hold_id
    assert held["reason"] == "flagged_protocol"
    assert held["resolved"] is False
    assert held["resolved_by"] is None
    assert held["resolved_at"] is None


def test_fetch_held_event_after_resolution_reports_resolver_and_time(persistence):
    hold_id = persistence.store_held_event("approval", {"event_id": "evt-1"})
    persistence.resolve_held_event("approval", hold_id, {"resolved_by": "commander-1", "resolved_at": "2026-08-24T09:00:00", "decision": "approved"})

    held = persistence.fetch_held_event("approval", "evt-1")

    assert held["resolved"] is True
    assert held["resolved_by"] == "commander-1"
    assert held["resolved_at"] == "2026-08-24T09:00:00"
    assert held["resolution"]["decision"] == "approved"


def test_fetch_held_event_returns_none_for_an_unknown_event_id(persistence):
    assert persistence.fetch_held_event("approval", "does-not-exist") is None


def test_fetch_held_event_is_scoped_to_its_kind(persistence):
    persistence.store_held_event("clarification", {"event_id": "evt-1"})

    assert persistence.fetch_held_event("approval", "evt-1") is None
    assert persistence.fetch_held_event("clarification", "evt-1") is not None


# -- Notification log (§8.12) ------------------------------------------------


def test_storing_a_held_event_writes_a_matching_notification_row(persistence):
    persistence.store_held_event("approval", {"event_id": "evt-1"})
    persistence.store_held_event("clarification", {"event_id": "evt-2"})

    notifications = persistence.fetch_notifications_since(0)

    kinds_by_event = {n["event_id"]: n["kind"] for n in notifications}
    assert kinds_by_event["evt-1"] == "approval_hold"
    assert kinds_by_event["evt-2"] == "clarification_hold"


@pytest.mark.parametrize(
    "outcome,expected_kinds",
    [
        ("succeeded", {"job_finished"}),
        ("declined", {"job_finished"}),
        ("failed", {"job_failed"}),
        ("uncertain", {"job_finished", "uncertain_verdict"}),
        ("closed_on_precedent", {"job_finished", "precedent_closure"}),
        ("no_match_protocol", {"job_finished", "no_match_notice"}),
    ],
)
def test_setting_an_event_outcome_writes_the_right_notification_kinds(persistence, outcome, expected_kinds):
    event_id = persistence.append_event(_minimal_event())

    persistence.update_event(event_id, {"outcome": outcome})

    notifications = persistence.fetch_notifications_since(0)
    kinds = {n["kind"] for n in notifications if n["event_id"] == event_id}
    assert kinds == expected_kinds


def test_updating_an_event_with_no_outcome_change_writes_no_notification(persistence):
    event_id = persistence.append_event(_minimal_event())

    persistence.update_event(event_id, {"risk_level": "high", "risk_reason": "why"})

    assert persistence.fetch_notifications_since(0) == []


def test_notifications_since_a_cursor_returns_only_what_came_after_it(persistence):
    persistence.store_held_event("approval", {"event_id": "evt-1"})
    first_batch = persistence.fetch_notifications_since(0)
    cursor = first_batch[-1]["sequence_id"]

    # Polling again at the same cursor — no redelivery.
    assert persistence.fetch_notifications_since(cursor) == []

    persistence.store_held_event("clarification", {"event_id": "evt-2"})

    second_batch = persistence.fetch_notifications_since(cursor)
    assert len(second_batch) == 1
    assert second_batch[0]["event_id"] == "evt-2"
    assert second_batch[0]["sequence_id"] > cursor


def test_notifications_since_zero_returns_everything_recorded(persistence):
    persistence.store_held_event("approval", {"event_id": "evt-1"})
    persistence.store_held_event("approval", {"event_id": "evt-2"})

    assert len(persistence.fetch_notifications_since(0)) == 2


def test_notifications_are_returned_in_ascending_sequence_order(persistence):
    persistence.store_held_event("approval", {"event_id": "evt-1"})
    persistence.store_held_event("approval", {"event_id": "evt-2"})
    persistence.store_held_event("approval", {"event_id": "evt-3"})

    notifications = persistence.fetch_notifications_since(0)
    sequence_ids = [n["sequence_id"] for n in notifications]
    assert sequence_ids == sorted(sequence_ids)


# -- Structured log entries (§1.8 follow-up) ---------------------------------


def test_write_and_fetch_log_entries_round_trip(persistence):
    persistence.write_log_entry("trace-1", {"level": "INFO", "logger": "x", "message": "hello", "custom_field": 42})

    [entry] = persistence.fetch_log_entries("trace-1")
    assert entry["trace_id"] == "trace-1"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "x"
    assert entry["message"] == "hello"
    assert entry["custom_field"] == 42
    assert entry["timestamp"]  # captured automatically by the write path


def test_fetch_log_entries_is_scoped_to_its_trace_id(persistence):
    persistence.write_log_entry("trace-1", {"message": "a"})
    persistence.write_log_entry("trace-2", {"message": "b"})

    assert [e["message"] for e in persistence.fetch_log_entries("trace-1")] == ["a"]
    assert [e["message"] for e in persistence.fetch_log_entries("trace-2")] == ["b"]


def test_fetch_log_entries_for_an_unknown_trace_id_returns_empty_list(persistence):
    assert persistence.fetch_log_entries("never-logged") == []


def test_log_entries_for_one_trace_id_are_returned_in_the_order_they_were_written(persistence):
    for i in range(5):
        persistence.write_log_entry("trace-1", {"message": f"step {i}", "i": i})

    entries = persistence.fetch_log_entries("trace-1")
    assert [e["i"] for e in entries] == [0, 1, 2, 3, 4]


def test_write_log_entry_accepts_no_trace_id(persistence):
    persistence.write_log_entry(None, {"message": "startup warning"})

    assert persistence.fetch_log_entries("") == []  # never conflated with "no trace"
