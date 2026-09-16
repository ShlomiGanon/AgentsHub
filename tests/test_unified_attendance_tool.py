"""Focused regression tests for the unified attendance write tool."""

import sqlite3
from datetime import datetime, timedelta, timezone

from agents import authenticated_request_identity
from agents import runtime as agent_runtime
from profiles import unified_test


MESSAGE = "אני במילואים מראשון עד שלישי בערב, לא זמין ביישוב"


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", str(tmp_path / "attendance.db"))
    agent = unified_test.UnifiedTeamStatusAgent(model="mock")
    opened = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    agent.status_store.register_member("member-1", "Test Member", opened.isoformat())
    agent.status_store.approve_roster("commander-1", opened.isoformat())
    agent.status_store.open_cycle(
        opened.date().isoformat(), opened.isoformat(), (opened + timedelta(hours=1)).isoformat()
    )
    return agent, opened


def _wrapped_call(agent, metadata, **business_args):
    token = agent_runtime._current_allowed_tools.set(frozenset({"record_attendance_response"}))
    try:
        with authenticated_request_identity("member-1"), agent_runtime.trusted_event_metadata(metadata):
            return agent._wrapped_tools["record_attendance_response"](**business_args)
    finally:
        agent_runtime._current_allowed_tools.reset(token)


def _row(agent):
    with sqlite3.connect(agent.status_db_path) as connection:
        return connection.execute(
            "SELECT source_message_id, telegram_identity, availability, reason, "
            "unavailable_until, original_text, received_at FROM attendance_responses"
        ).fetchone()


def test_explicit_unavailable_report_is_normalized_and_written_with_trusted_metadata(tmp_path, monkeypatch):
    agent, opened = _agent(tmp_path, monkeypatch)
    received_at = (opened + timedelta(minutes=10)).isoformat()
    result = _wrapped_call(
        agent,
        {
            "source_message_id": "event-message-16",
            "original_text": MESSAGE,
            "received_at": received_at,
        },
        # Hebrew is the localized model output seen in the live run.  The
        # runtime still exposes the canonical enum to persistence.
        availability="לא זמין",
        reason="שירות מילואים",
        unavailable_days=3,
        source_message_id="forged-by-model",
        original_text="forged text",
        received_at="2020-01-01T00:00:00+00:00",
    )

    assert "הבהרה" not in result
    source_id, identity, availability, reason, until, original_text, stored_at = _row(agent)
    assert source_id == "event-message-16"
    assert identity == "member-1"
    assert availability == "unavailable"
    assert reason == "שירות מילואים"
    assert datetime.fromisoformat(until) == datetime.fromisoformat(received_at) + timedelta(days=3)
    assert original_text == MESSAGE
    assert stored_at == received_at


def test_unavailable_without_reason_still_requests_business_clarification(tmp_path, monkeypatch):
    agent, opened = _agent(tmp_path, monkeypatch)
    result = _wrapped_call(
        agent,
        {
            "source_message_id": "event-no-reason",
            "original_text": "אני לא זמין היום",
            "received_at": (opened + timedelta(minutes=5)).isoformat(),
        },
        availability="לא זמין",
        reason="",
        unavailable_days=1,
    )

    assert result == unified_test.get_catalog("he").text("unified.team_status.clarify_reason")
    assert _row(agent) is None


def test_available_report_remains_available_and_discards_stale_unavailability_fields(tmp_path, monkeypatch):
    agent, opened = _agent(tmp_path, monkeypatch)
    received_at = (opened + timedelta(minutes=6)).isoformat()
    result = _wrapped_call(
        agent,
        {
            "source_message_id": "event-available",
            "original_text": "אני זמין היום",
            "received_at": received_at,
        },
        availability="זמין",
        reason="שירות מילואים (stale)",
        unavailable_days=4,
    )

    assert "הבהרה" not in result
    _, _, availability, reason, until, _, stored_at = _row(agent)
    assert availability == "available"
    assert reason is None
    assert until is None
    assert stored_at == received_at


def test_unknown_availability_value_is_not_accepted_as_a_fuzzy_match(tmp_path, monkeypatch):
    agent, opened = _agent(tmp_path, monkeypatch)
    result = _wrapped_call(
        agent,
        {
            "source_message_id": "event-unknown-status",
            "original_text": "status unclear",
            "received_at": (opened + timedelta(minutes=5)).isoformat(),
        },
        availability="maybe unavailable",
        reason="service",
        unavailable_days=2,
    )

    assert result == unified_test.get_catalog("he").text("unified.team_status.clarify_availability")
    assert _row(agent) is None
