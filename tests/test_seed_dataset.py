"""Confirms every §2.12 bullet is actually satisfied by the seed dataset,
not just that it loads without error.
"""

import pytest

from fixtures.seed_events import REFERENCE_NOW, SEED_EVENTS, load_seed_dataset
from persistence.sqlite_backend import SQLitePersistence


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "seed.db"))
    load_seed_dataset(backend)
    yield backend
    backend.close()


def test_every_record_loads_without_error(store):
    events = store.fetch_events_range("2000-01-01", "2100-01-01")
    assert len(events) == len(SEED_EVENTS)


def test_contains_partial_reports_with_empty_fields():
    assert any(e.get("area") is None or e.get("severity") is None for e in SEED_EVENTS)


def test_contains_a_contradictory_pair_about_the_same_occurrence():
    by_classification_area_time = {}
    for event in SEED_EVENTS:
        key = (event.get("classification"), event.get("area"), event.get("occurred_at"))
        by_classification_area_time.setdefault(key, []).append(event)

    contradictory_groups = [group for group in by_classification_area_time.values() if len(group) > 1]
    assert contradictory_groups, "expected at least one pair sharing classification/area/occurrence"

    descriptions = {e["description"] for e in contradictory_groups[0]}
    assert len(descriptions) > 1, "a contradictory pair must actually disagree, not just co-occur"


def test_contains_a_report_whose_occurrence_precedes_its_receipt():
    assert any(
        event.get("occurred_at") and event["occurred_at"] < event["received_at"]
        for event in SEED_EVENTS
    )


def test_contains_both_high_and_low_risk_records():
    risk_levels = {event.get("risk_level") for event in SEED_EVENTS}
    assert "high" in risk_levels
    assert "low" in risk_levels


def test_repeated_fire_north_sector_events_span_inside_and_outside_a_typical_window(store):
    window_start = "2026-07-21T12:00:00"  # 30 days before REFERENCE_NOW

    matches = store.fetch_events_by_type_area_window("fire", "north_sector", window_start, REFERENCE_NOW)
    matched_ids = {event["event_id"] for event in matches}

    assert "seed-fire-north-3" not in matched_ids, "the near-miss record must fall outside the window"
    assert {"seed-fire-north-1", "seed-fire-north-2", "seed-fire-north-unresolved"} <= matched_ids


def test_contains_at_least_one_unresolved_prior_event():
    assert any(event.get("outcome") is None for event in SEED_EVENTS)


def test_contains_a_resolved_clarification_hold_from_unclassifiable_text():
    resolved_holds = [event for event in SEED_EVENTS if event.get("clarification_held")]

    assert resolved_holds
    assert all(event.get("clarification_chosen_classification") for event in resolved_holds)


def test_contains_human_activation_records():
    human_activation_events = [event for event in SEED_EVENTS if event["classification"] == "human_activation"]

    assert len(human_activation_events) >= 2
