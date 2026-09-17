"""Trusted event metadata propagation into the attendance specialist tool."""

import sqlite3
from datetime import datetime, timedelta, timezone

from agents import adapter
from agents import runtime as agent_runtime
from agents.team_status_agent import TeamStatusAgent


class _TestTeamStatusAgent(TeamStatusAgent):
    status_db_path = ""


def _agent(tmp_path):
    _TestTeamStatusAgent.status_db_path = str(tmp_path / "team-status.db")
    return _TestTeamStatusAgent(model="test-model")


def _open_approved_cycle(agent, opened_at):
    agent.register_member("member-1", "Display Name", opened_at.isoformat())
    agent.approve_roster("commander-1", opened_at.isoformat())
    token = agent_runtime._current_allowed_tools.set(frozenset({"start_daily_attendance_check"}))
    try:
        agent._wrapped_tools["start_daily_attendance_check"](now_iso=opened_at.isoformat())
    finally:
        agent_runtime._current_allowed_tools.reset(token)


def test_event_metadata_is_injected_and_sender_identity_is_authenticated(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    received_at = (opened_at + timedelta(minutes=10)).isoformat()
    original_text = "אני במילואים מראשון עד שלישי בערב, לא זמין ביישוב"
    event_metadata = {
        "source_message_id": "event-source-42",
        "original_text": original_text,
        "received_at": received_at,
        "availability_start": "2026-09-19T21:00:00+00:00",
        "availability_end": "2026-09-22T17:00:00+00:00",
    }
    _open_approved_cycle(agent, opened_at)

    # The model supplies only business fields. Even forged trusted kwargs are
    # ignored while the worker's event metadata is installed.
    token = agent_runtime._current_allowed_tools.set(frozenset({"record_attendance_response"}))
    try:
        with agent_runtime.authenticated_request_identity("member-1"), agent_runtime.trusted_event_metadata(
            event_metadata
        ):
            result = agent._wrapped_tools["record_attendance_response"](
                source_message_id="model-invented-id",
                availability="unavailable",
                original_text="model-invented-text",
            reason="מילואים",
            unavailable_days=3,
            availability_start="2020-01-01T00:00:00+00:00",
            availability_end="2020-01-02T00:00:00+00:00",
                received_at="2020-01-01T00:00:00+00:00",
            )
    finally:
        agent_runtime._current_allowed_tools.reset(token)

    assert result == "The attendance response was stored."
    with sqlite3.connect(agent.status_db_path) as connection:
        row = connection.execute(
            """
            SELECT source_message_id, telegram_identity, original_text, received_at, availability_start, availability_end
            FROM attendance_responses
            """
        ).fetchone()
    assert row == (
        "event-source-42",
        "member-1",
        original_text,
        received_at,
        "2026-09-19T21:00:00+00:00",
        "2026-09-22T17:00:00+00:00",
    )


def test_attendance_tool_schema_exposes_business_fields_only(tmp_path):
    agent = _agent(tmp_path)
    crewai_module = adapter._get_crewai()
    built = adapter._build_crewai_tools(crewai_module, agent.name, agent._wrapped_tools, agent.descriptor.tools)
    attendance_tool = next(tool for tool in built if tool.name == "record_attendance_response")

    assert set(attendance_tool.args_schema.model_fields) == {
        "availability",
        "reason",
        "unavailable_days",
    }
    assert "source_message_id" not in attendance_tool.args_schema.model_fields
    assert "original_text" not in attendance_tool.args_schema.model_fields
    assert "received_at" not in attendance_tool.args_schema.model_fields


def test_metadata_context_is_scoped_and_does_not_leak_between_events():
    assert agent_runtime._trusted_event_metadata.get() is None
    metadata = {"source_message_id": "event-1", "original_text": "text", "received_at": "time"}
    with agent_runtime.trusted_event_metadata(metadata):
        assert agent_runtime._trusted_event_metadata.get() == metadata
    assert agent_runtime._trusted_event_metadata.get() is None
