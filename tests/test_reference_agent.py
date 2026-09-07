from agents import base
from agents.reference import ReferenceAgent


def test_constructed_with_a_model_like_any_other_agent():
    agent = ReferenceAgent(model="some-model")

    assert agent.model == "some-model"
    assert agent.name == "reference_agent"


def test_role_and_system_prompt_are_real_text_not_placeholders():
    agent = ReferenceAgent(model="m")

    assert len(agent.role) > 40
    assert len(agent.system_prompt) > 40
    assert "TODO" not in agent.role
    assert "placeholder" not in agent.role.lower()


def test_exposes_exactly_the_two_stub_tools_with_the_right_marks():
    agent = ReferenceAgent(model="m")
    tools = {t.name: t for t in agent.exposed_tools()}

    assert set(tools) == {"check_status", "record_action"}

    assert tools["check_status"].side_effecting is False
    assert tools["check_status"].idempotent is None

    assert tools["record_action"].side_effecting is True
    assert tools["record_action"].idempotent is False


def test_check_status_is_read_only_and_returns_a_canned_status():
    agent = ReferenceAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"check_status"}))
    try:
        result = agent._wrapped_tools["check_status"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "gate-3" in result
    assert agent.actions_taken == []


def test_record_action_genuinely_records_each_call_it_receives():
    # This is what makes "a retry does not repeat an action" testable
    # later (§4.5) — the tool has to actually accumulate state, not just
    # return a canned string, so a second call is observably different
    # from stopping after one.
    agent = ReferenceAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"record_action"}))
    try:
        agent._wrapped_tools["record_action"](location="gate-3", note="dispatched")
        agent._wrapped_tools["record_action"](location="gate-3", note="dispatched")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.actions_taken) == 2  # two calls really did record twice


def test_record_action_is_blocked_when_not_allowed():
    agent = ReferenceAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"check_status"}))  # record_action not allowed
    try:
        result = agent._wrapped_tools["record_action"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.actions_taken == []

"""Confirms every §2.12 bullet is actually satisfied by the seed dataset,
not just that it loads without error.
"""

import pytest

from fixtures.seed_events import REFERENCE_NOW, SEED_EVENTS, load_seed_dataset
from persistence.sqlite_store import SQLitePersistence


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
