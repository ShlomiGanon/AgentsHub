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
