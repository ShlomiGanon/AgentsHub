"""History extraction, timestamp handling, and event writes."""

import json

import pytest

from history.interface import ExtractionExecutionError, extract_event
from profiles import AreaRegistry
from profiles import EventTypeRegistry


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


# -- Event type descriptions in the extraction prompt (follow-up to Stage 4, docs/bar_improves.md)


def _capture_prompt(response_text):
    captured = []

    def _invoker(prompt):
        captured.append(prompt)
        return response_text

    return captured, _invoker


def test_extraction_prompt_includes_each_declared_event_type_description():
    registry = EventTypeRegistry(
        types=("fire", "medical"),
        descriptions={"fire": "A structure or vegetation fire.", "medical": "A medical incident with a casualty."},
    )
    captured, invoker = _capture_prompt(_response())

    extract_event("smoke at gate", "telegram", "2026-08-20T10:00:00", registry, AreaRegistry(("north",)), invoker)

    [prompt] = captured
    assert "A structure or vegetation fire." in prompt
    assert "A medical incident with a casualty." in prompt


def test_extraction_prompt_omits_description_text_for_a_type_that_has_none():
    registry = EventTypeRegistry(
        types=("fire", "medical"), descriptions={"fire": "A structure or vegetation fire."}
    )
    captured, invoker = _capture_prompt(_response())

    extract_event("smoke at gate", "telegram", "2026-08-20T10:00:00", registry, AreaRegistry(("north",)), invoker)

    [prompt] = captured
    assert "A structure or vegetation fire." in prompt
    # "medical" itself still appears (the bare type-name list), just with no description text.
    assert "medical" in prompt


def test_extraction_prompt_is_unchanged_when_the_profile_declares_no_event_type_descriptions():
    """Regression check: a profile that doesn't use the new, optional
    EVENT_TYPE_DESCRIPTIONS attribute gets exactly the same prompt as
    before this attribute existed."""
    registry_without_descriptions = EventTypeRegistry(types=("fire", "medical"))
    registry_with_empty_descriptions = EventTypeRegistry(types=("fire", "medical"), descriptions={})
    captured_a, invoker_a = _capture_prompt(_response())
    captured_b, invoker_b = _capture_prompt(_response())

    extract_event(
        "smoke at gate", "telegram", "2026-08-20T10:00:00", registry_without_descriptions,
        AreaRegistry(("north",)), invoker_a,
    )
    extract_event(
        "smoke at gate", "telegram", "2026-08-20T10:00:00", registry_with_empty_descriptions,
        AreaRegistry(("north",)), invoker_b,
    )

    assert captured_a == captured_b
    assert "Event type descriptions:" not in captured_a[0]


def test_extraction_prompt_carries_a_real_response_team_profiles_declared_descriptions(monkeypatch):
    """Against a real operational profile (docs/bar_improves.md), not just a
    synthetic registry."""
    from config.base import TierModel
    from profiles import build_event_type_registry
    from profiles.loader import load_profile

    monkeypatch.setenv("RESPONSE_TEAM_BOT_TOKEN", "test-token")
    core_model = TierModel(model="openai/test-core-model", api_key="test-key")
    sub_model = TierModel(model="openai/test-sub-model", api_key="test-key")
    loaded = load_profile("profiles.response_team", core_model, sub_model)
    registry = build_event_type_registry(loaded)

    captured, invoker = _capture_prompt(
        json.dumps(
            {
                "classification": "perimeter_observation", "area": "west_gate", "entities": [],
                "description": "heavy equipment near the western gate", "severity": None,
                "occurred_at": "2026-09-10T10:00:00",
            }
        )
    )

    extract_event(
        "heavy equipment parked near the western gate", "telegram", "2026-09-10T10:00:00", registry,
        AreaRegistry(loaded.areas), invoker,
    )

    [prompt] = captured
    from profiles.response_team import EVENT_TYPE_DESCRIPTIONS

    for description in EVENT_TYPE_DESCRIPTIONS.values():
        assert description in prompt


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


def test_malformed_optional_field_is_dropped_not_the_whole_report(caplog):
    # Stage 1 (docs/bar_improves.md): a non-scalar value for an optional
    # field (severity here) must not reject an otherwise-usable report.
    with caplog.at_level("INFO"):
        result = extract_event(
            "smoke at gate",
            "telegram",
            "2026-08-20T10:00:00",
            EventTypeRegistry(("fire",)),
            AreaRegistry(("north",)),
            lambda prompt: _response(severity={"level": "high"}),
        )

    assert result.severity is None
    assert result.classification == "fire"
    assert result.area == "north"
    dropped_records = [r for r in caplog.records if getattr(r, "event", None) == "extraction_optional_field_dropped"]
    assert len(dropped_records) == 1
    assert dropped_records[0].field == "severity"
    assert dropped_records[0].received_type == "dict"


def test_malformed_optional_entities_field_is_dropped_not_the_whole_report(caplog):
    with caplog.at_level("INFO"):
        result = extract_event(
            "smoke at gate",
            "telegram",
            "2026-08-20T10:00:00",
            EventTypeRegistry(("fire",)),
            AreaRegistry(("north",)),
            lambda prompt: _response(entities="gate"),
        )

    assert result.entities == ()
    assert result.classification == "fire"
    dropped_records = [r for r in caplog.records if getattr(r, "event", None) == "extraction_optional_field_dropped"]
    assert len(dropped_records) == 1
    assert dropped_records[0].field == "entities"
    assert dropped_records[0].received_type == "str"


def test_valid_optional_fields_are_unaffected_by_the_malformed_field_tolerance():
    result = extract_event(
        "smoke at gate",
        "telegram",
        "2026-08-20T10:00:00",
        EventTypeRegistry(("fire",)),
        AreaRegistry(("north",)),
        lambda prompt: _response(),
    )

    assert result.severity == "high"
    assert result.entities == ("gate",)
    assert result.classification == "fire"
    assert result.area == "north"


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


def test_initial_event_rejects_an_unknown_sender_permission_level(store):
    with pytest.raises(ValueError, match="sender_permission_level"):
        record_initial_event(
            store,
            InitialEventEnvelope(
                raw_text="smoke",
                source="sensor",
                received_at="2026-08-20T10:00:00",
                sender_identity="sensor-1",
                sender_permission_level="administrator",
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


from datetime import datetime, timedelta, timezone

import pytest

from history.time_utils import (
    UTC,
    add_month,
    day_bounds,
    iter_days,
    iter_months,
    iter_years,
    month_bounds,
    parse_timestamp,
    storage_timestamp,
    year_bounds,
)

# -- parse_timestamp ----------------------------------------------------


def test_parse_timestamp_accepts_a_z_suffix_as_utc():
    parsed = parse_timestamp("2026-08-24T09:00:00Z")

    assert parsed == datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


def test_parse_timestamp_treats_a_naive_timestamp_as_already_utc():
    parsed = parse_timestamp("2026-08-24T09:00:00")

    assert parsed == datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


def test_parse_timestamp_converts_a_non_utc_offset_to_utc():
    # 02:00 at +05:00 is 21:00 UTC the *previous* day — the exact
    # near-midnight-crossing-a-day case worth being explicit about.
    parsed = parse_timestamp("2026-03-15T02:00:00+05:00")

    assert parsed == datetime(2026, 3, 14, 21, 0, 0, tzinfo=UTC)


def test_parse_timestamp_strips_surrounding_whitespace():
    assert parse_timestamp("  2026-08-24T09:00:00Z  ") == datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


def test_parse_timestamp_rejects_unparseable_input_with_value_error():
    # history/extraction.py's own call site catches exactly ValueError
    # (wrapping it as ExtractionExecutionError) — confirming the existing
    # contract, not inventing a new one.
    with pytest.raises(ValueError):
        parse_timestamp("not a timestamp at all")


def test_parse_timestamp_rejects_an_empty_string():
    with pytest.raises(ValueError):
        parse_timestamp("")


# -- storage_timestamp ----------------------------------------------------


def test_storage_timestamp_round_trips_through_parse_timestamp():
    original = "2026-08-24T09:00:00Z"
    assert storage_timestamp(parse_timestamp(original)) == "2026-08-24T09:00:00"


def test_storage_timestamp_strips_timezone_and_truncates_to_seconds():
    value = datetime(2026, 8, 24, 9, 30, 15, 123456, tzinfo=UTC)

    assert storage_timestamp(value) == "2026-08-24T09:30:15"


def test_storage_timestamp_converts_a_non_utc_aware_value_to_utc_first():
    value = datetime(2026, 3, 15, 2, 0, 0, tzinfo=timezone(timedelta(hours=5)))

    assert storage_timestamp(value) == "2026-03-14T21:00:00"


# -- day_bounds -----------------------------------------------------------


def test_day_bounds_is_midnight_to_midnight_utc():
    start, end = day_bounds(datetime(2026, 8, 24, 15, 30, tzinfo=UTC))

    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 25, 0, 0, tzinfo=UTC)


def test_day_bounds_crosses_a_month_end():
    start, end = day_bounds(datetime(2026, 1, 31, 12, 0, tzinfo=UTC))

    assert start == datetime(2026, 1, 31, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 2, 1, 0, 0, tzinfo=UTC)


def test_day_bounds_crosses_a_year_end():
    start, end = day_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))

    assert start == datetime(2026, 12, 31, 0, 0, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)


def test_day_bounds_uses_the_given_datetimes_own_date_not_a_utc_renormalization():
    # day_bounds does not itself convert to UTC — it takes .date() from
    # whatever it's handed. Production code always feeds it the output of
    # parse_timestamp (already UTC), so this is documenting the contract,
    # not a bug: a caller that skips parse_timestamp and passes a
    # non-UTC-aware value gets bounds for *that* value's own calendar
    # date, not the UTC-equivalent one.
    non_utc = datetime(2026, 3, 15, 2, 0, 0, tzinfo=timezone(timedelta(hours=5)))  # 21:00 UTC on the 14th

    start, _end = day_bounds(non_utc)

    assert start == datetime(2026, 3, 15, 0, 0, tzinfo=UTC)  # the 15th, not the 14th


# -- month_bounds / add_month ----------------------------------------------


def test_month_bounds_within_a_year():
    start, end = month_bounds(datetime(2026, 3, 15, tzinfo=UTC))

    assert start == datetime(2026, 3, 1, tzinfo=UTC)
    assert end == datetime(2026, 4, 1, tzinfo=UTC)


def test_month_bounds_rolls_december_into_next_january():
    start, end = month_bounds(datetime(2026, 12, 15, tzinfo=UTC))

    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_add_month_rolls_the_year_over():
    assert add_month(datetime(2026, 12, 1, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)


def test_add_month_within_a_year():
    assert add_month(datetime(2026, 3, 1, tzinfo=UTC)) == datetime(2026, 4, 1, tzinfo=UTC)


# -- year_bounds ------------------------------------------------------------


def test_year_bounds():
    start, end = year_bounds(datetime(2026, 6, 15, tzinfo=UTC))

    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_year_bounds_at_the_very_edge_of_the_year():
    start, end = year_bounds(datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC))

    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


# -- iter_days / iter_months / iter_years — leap years and multi-boundary --
# -- crossings --------------------------------------------------------------


def test_iter_days_counts_29_days_in_a_leap_year_february():
    # 2028 is a leap year.
    days = list(iter_days(datetime(2028, 2, 1, tzinfo=UTC), datetime(2028, 3, 1, tzinfo=UTC)))

    assert len(days) == 29
    assert days[-1][0] == datetime(2028, 2, 29, tzinfo=UTC)


def test_iter_days_counts_28_days_in_a_non_leap_year_february():
    # 2026 is not a leap year.
    days = list(iter_days(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)))

    assert len(days) == 28
    assert days[-1][0] == datetime(2026, 2, 28, tzinfo=UTC)


def test_iter_days_each_pair_is_exactly_one_day_apart_and_contiguous():
    days = list(iter_days(datetime(2026, 1, 30, tzinfo=UTC), datetime(2026, 2, 2, tzinfo=UTC)))

    assert days == [
        (datetime(2026, 1, 30, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC)),
        (datetime(2026, 1, 31, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
        (datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 2, 2, tzinfo=UTC)),
    ]


def test_iter_months_crosses_a_year_boundary():
    months = list(iter_months(datetime(2026, 11, 1, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC)))

    assert months == [
        (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
        (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC)),
    ]


def test_iter_years_across_multiple_years():
    years = list(iter_years(datetime(2024, 6, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)))

    assert years == [
        (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
        (datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
    ]


def test_iter_days_with_start_equal_to_end_yields_nothing():
    assert list(iter_days(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))) == []
