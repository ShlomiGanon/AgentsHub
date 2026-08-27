import json

import pytest

from history.interface import ExtractionExecutionError, extract_event
from registries.areas import AreaRegistry
from registries.event_types import EventTypeRegistry


def _response(**overrides):
    payload = {
        "classification": "fire",
        "area": "north",
        "entities": ["gate"],
        "description": "smoke at gate",
        "severity": "high",
        "occurred_at": "2026-08-19T22:00:00",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_sensor_uses_received_time_but_model_extracts_other_fields():
    result = extract_event(
        "smoke now",
        "sensor",
        "2026-08-20T10:00:00",
        EventTypeRegistry(("fire",)),
        AreaRegistry(("north",)),
        lambda prompt: _response(occurred_at="1900-01-01T00:00:00"),
    )

    assert result.occurred_at == "2026-08-20T10:00:00"
    assert result.classification_status == "resolved"


def test_invalid_closed_set_values_are_left_unresolved():
    result = extract_event(
        "unknown",
        "telegram",
        "2026-08-20T10:00:00",
        EventTypeRegistry(("fire",)),
        AreaRegistry(("north",)),
        lambda prompt: _response(classification="weather", area="west", occurred_at=None),
    )

    assert result.classification is None
    assert result.area is None
    assert result.occurred_at is None
    assert set(result.missing_fields) >= {"classification", "area", "occurred_at"}


def test_code_fence_is_the_only_cleanup_and_bad_json_is_an_execution_error():
    valid = extract_event(
        "x",
        "telegram",
        "2026-08-20T10:00:00",
        EventTypeRegistry(("fire",)),
        AreaRegistry(("north",)),
        lambda prompt: f"```json\n{_response()}\n```",
    )
    assert valid.classification == "fire"

    with pytest.raises(ExtractionExecutionError):
        extract_event(
            "x",
            "telegram",
            "2026-08-20T10:00:00",
            EventTypeRegistry(("fire",)),
            AreaRegistry(("north",)),
            lambda prompt: "classification: fire",
        )

from types import SimpleNamespace

import pytest

from history.interface import (
    ExtractionResult,
    InitialEventEnvelope,
    StepExecutionEnvelope,
    record_event_outcome,
    record_event_state,
    record_extracted_fields,
    record_initial_event,
    record_step_execution,
)
from persistence.interface import open_persistence


@pytest.fixture
def store(tmp_path):
    persistence = open_persistence(str(tmp_path / "history-write.db"))
    yield persistence
    persistence.close()


def _initial(store):
    return record_initial_event(
        store,
        InitialEventEnvelope(
            raw_text="smoke yesterday",
            source="telegram",
            received_at="2026-08-20T10:00:00",
            sender_identity="viewer-1",
        ),
    )


def test_history_write_path_is_incremental(store):
    event_id = _initial(store)
    result = ExtractionResult(
        classification="fire",
        classification_status="resolved",
        area="north",
        entities=("gate",),
        description="smoke",
        severity="moderate",
        occurred_at="2026-08-19T22:00:00",
        occurred_at_is_fallback=False,
        missing_fields=(),
    )

    record_extracted_fields(store, event_id, result)
    record_step_execution(store, event_id, StepExecutionEnvelope(0, "a", "check", ["read"], "ok", 1))
    record_event_outcome(store, event_id, "succeeded", insight_text="resolved")

    [event] = store.fetch_events_range("2026-08-19T00:00:00", "2026-08-20T00:00:00")
    assert event["raw_text"] == "smoke yesterday"
    assert event["classification"] == "fire"
    assert event["steps"][0]["result_text"] == "ok"
    assert event["outcome"] == "succeeded"


def test_state_allowlist_cannot_bypass_outcome_writer(store):
    event_id = _initial(store)

    record_event_state(store, event_id, {"risk_level": "high"})

    with pytest.raises(ValueError, match="outcome"):
        record_event_state(store, event_id, {"outcome": "succeeded"})


def test_state_update_can_set_classification_after_a_clarification_hold(store):
    # Added in Mission 6 (§6.11): resuming a clarification hold writes the
    # commander's chosen classification via this same allowlisted path,
    # without re-running extraction and discarding already-resolved
    # area/description/severity.
    event_id = _initial(store)

    record_event_state(store, event_id, {"classification": "fire"})

    assert store.fetch_event(event_id)["classification"] == "fire"


def test_scheduler_notification_requires_event_envelope_data(store):
    event_id = _initial(store)
    result = ExtractionResult("fire", "resolved", "north", (), None, None, "2026-08-19T22:00:00", False, ())
    scheduler = SimpleNamespace(notify_event_written=lambda *args: None)

    with pytest.raises(ValueError, match="source and received_at"):
        record_extracted_fields(store, event_id, result, scheduler)
