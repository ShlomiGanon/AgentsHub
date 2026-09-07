import pytest

from persistence.exceptions import NotFoundError, PersistenceError
from persistence.sqlite_store import SQLitePersistence


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "test.db"))
    yield backend
    backend.close()


def _minimal_event(**overrides):
    event = {
        "received_at": "2026-08-01T10:00:00",
        "source": "sensor",
        "sender_identity": "sensor-7",
        "occurred_at": "2026-08-01T10:00:00",
        "raw_text": "smoke reported near the west gate",
    }
    event.update(overrides)
    return event


# -- append_event / fetch (§2.3, §2.5) -----------------------------------


def test_append_event_returns_a_generated_id_when_none_given(store):
    event_id = store.append_event(_minimal_event())

    assert event_id


def test_append_event_respects_a_supplied_id(store):
    event_id = store.append_event(_minimal_event(event_id="fixed-id-1"))

    assert event_id == "fixed-id-1"


def test_telegram_event_may_be_appended_with_no_occurrence_timestamp_yet(store):
    # §6.11 writes the event before extraction runs — occurred_at is only
    # known afterwards for a Telegram-originated event.
    event_id = store.append_event(_minimal_event(source="telegram", occurred_at=None))

    events = store.fetch_events_range("0000-01-01", "9999-01-01")
    assert event_id not in [e["event_id"] for e in events]  # not yet occurrence-dated, so not range-queryable


def test_raw_text_is_preserved_exactly(store):
    raw = "Fire!! near gate 3 -- 2 ppl seen, smoke heavy"
    event_id = store.append_event(_minimal_event(raw_text=raw))

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert event["raw_text"] == raw


def test_fetch_events_range_orders_by_occurred_at(store):
    store.append_event(_minimal_event(event_id="e2", occurred_at="2026-08-02T00:00:00"))
    store.append_event(_minimal_event(event_id="e1", occurred_at="2026-08-01T00:00:00"))

    events = store.fetch_events_range("2026-08-01T00:00:00", "2026-08-02T23:59:59")
    assert [e["event_id"] for e in events] == ["e1", "e2"]


def test_fetch_events_range_excludes_the_half_open_end_boundary(store):
    store.append_event(_minimal_event(event_id="inside", occurred_at="2026-08-01T23:59:59"))
    store.append_event(_minimal_event(event_id="at-end", occurred_at="2026-08-02T00:00:00"))

    events = store.fetch_events_range("2026-08-01T00:00:00", "2026-08-02T00:00:00")

    assert [event["event_id"] for event in events] == ["inside"]


def test_fetch_events_by_type_area_window_matches_both_fields_exactly(store):
    store.append_event(_minimal_event(event_id="match", classification="fire", area="north", occurred_at="2026-08-01T00:00:00"))
    store.append_event(_minimal_event(event_id="wrong_area", classification="fire", area="south", occurred_at="2026-08-01T00:00:00"))
    store.append_event(_minimal_event(event_id="wrong_type", classification="medical", area="north", occurred_at="2026-08-01T00:00:00"))
    store.append_event(_minimal_event(event_id="outside_window", classification="fire", area="north", occurred_at="2020-01-01T00:00:00"))

    matches = store.fetch_events_by_type_area_window("fire", "north", "2026-01-01T00:00:00", "2026-12-31T00:00:00")

    assert [e["event_id"] for e in matches] == ["match"]


def test_entities_and_precedent_ids_round_trip_as_lists(store):
    event_id = store.append_event(_minimal_event(entities=["gate-3", "watchtower-2"], precedent_matched_event_ids=["prior-1"]))

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert event["entities"] == ["gate-3", "watchtower-2"]
    assert event["precedent_matched_event_ids"] == ["prior-1"]


def test_boolean_hold_flags_round_trip_as_booleans(store):
    event_id = store.append_event(_minimal_event(clarification_held=True, approval_held=False))

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert event["clarification_held"] is True
    assert event["approval_held"] is False


# -- update_event ----------------------------------------------------------


def test_update_event_merges_onto_the_existing_row(store):
    event_id = store.append_event(_minimal_event())

    store.update_event(event_id, {"risk_level": "high", "risk_reason": "multiple prior incidents"})

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert event["risk_level"] == "high"
    assert event["risk_reason"] == "multiple prior incidents"


def test_update_event_can_set_occurred_at_after_extraction(store):
    event_id = store.append_event(_minimal_event(source="telegram", occurred_at=None))

    store.update_event(event_id, {"occurred_at": "2026-08-01T09:00:00", "occurred_at_is_fallback": False})

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert event["event_id"] == event_id
    assert event["occurred_at"] == "2026-08-01T09:00:00"


def test_update_unknown_event_raises_not_found(store):
    with pytest.raises(NotFoundError):
        store.update_event("does-not-exist", {"risk_level": "low"})


def test_update_event_rejects_raw_text(store):
    event_id = store.append_event(_minimal_event())

    with pytest.raises(PersistenceError, match="raw_text"):
        store.update_event(event_id, {"raw_text": "a rewritten account"})


def test_update_event_rejects_envelope_fields(store):
    event_id = store.append_event(_minimal_event())

    with pytest.raises(PersistenceError):
        store.update_event(event_id, {"event_id": "different-id"})


def test_update_event_upserts_a_single_step_without_touching_others(store):
    event_id = store.append_event(_minimal_event())

    store.update_event(event_id, {"steps": [{"step_index": 0, "agent_name": "reference_agent", "task_text": "check status", "allowed_tools": ["check_status"], "result_text": "ok", "attempt_count": 1}]})
    store.update_event(event_id, {"steps": [{"step_index": 1, "agent_name": "reference_agent", "task_text": "log it", "allowed_tools": [], "result_text": "done", "attempt_count": 1}]})

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert [s["step_index"] for s in event["steps"]] == [0, 1]
    assert event["steps"][0]["result_text"] == "ok"


def test_update_event_step_upsert_overwrites_the_same_index(store):
    event_id = store.append_event(_minimal_event())

    store.update_event(event_id, {"steps": [{"step_index": 0, "agent_name": "a1", "task_text": "first try", "allowed_tools": [], "attempt_count": 1}]})
    store.update_event(event_id, {"steps": [{"step_index": 0, "agent_name": "a1", "task_text": "first try", "allowed_tools": [], "result_text": "succeeded on retry", "attempt_count": 2}]})

    [event] = store.fetch_events_range("2026-01-01", "2026-12-31")
    assert len(event["steps"]) == 1
    assert event["steps"][0]["attempt_count"] == 2
    assert event["steps"][0]["result_text"] == "succeeded on retry"


# -- Summaries (§2.6) --------------------------------------------------------


def test_write_and_fetch_a_summary(store):
    store.write_summary("daily", {
        "summary_text": "3 fire reports, all resolved",
        "period_start": "2026-08-01T00:00:00",
        "period_end": "2026-08-01T23:59:59",
        "generated_at": "2026-08-02T00:05:00",
        "event_index": [{"event_id": "e1"}],
    })

    [summary] = store.fetch_summaries_range("daily", "2026-08-01T00:00:00", "2026-08-01T23:59:59")
    assert summary["summary_text"] == "3 fire reports, all resolved"
    assert summary["event_index"] == [{"event_id": "e1"}]


def test_writing_a_summary_for_an_already_summarized_period_overwrites(store):
    period = {"period_start": "2026-08-01T00:00:00", "period_end": "2026-08-01T23:59:59"}

    store.write_summary("daily", {**period, "summary_text": "first pass", "generated_at": "2026-08-02T00:00:00"})
    store.write_summary("daily", {**period, "summary_text": "regenerated after a late report", "generated_at": "2026-08-02T01:00:00"})

    summaries = store.fetch_summaries_range("daily", "2026-08-01T00:00:00", "2026-08-01T23:59:59")
    assert len(summaries) == 1
    assert summaries[0]["summary_text"] == "regenerated after a late report"


def test_fetch_summaries_range_returns_periods_overlapping_the_range(store):
    store.write_summary("monthly", {
        "summary_text": "august",
        "period_start": "2026-08-01T00:00:00",
        "period_end": "2026-08-31T23:59:59",
        "generated_at": "2026-09-01T00:00:00",
    })
    store.write_summary("monthly", {
        "summary_text": "october",
        "period_start": "2026-10-01T00:00:00",
        "period_end": "2026-10-31T23:59:59",
        "generated_at": "2026-11-01T00:00:00",
    })

    summaries = store.fetch_summaries_range("monthly", "2026-08-15T00:00:00", "2026-09-15T00:00:00")
    assert [s["summary_text"] for s in summaries] == ["august"]


def test_write_summary_rejects_an_unknown_level(store):
    with pytest.raises(PersistenceError):
        store.write_summary("weekly", {"summary_text": "x", "period_start": "a", "period_end": "b", "generated_at": "c"})
