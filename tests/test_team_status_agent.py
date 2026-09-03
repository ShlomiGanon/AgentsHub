from datetime import datetime, timedelta, timezone

from agents import AgentResult
from agents import runtime as agent_runtime
from agents.team_status_agent import TeamStatusAgent
from protocols import Step, execute_step_with_retry


class _TestTeamStatusAgent(TeamStatusAgent):
    status_db_path = ""


class _Settings:
    def get_retry_count(self):
        return 1


def _agent(tmp_path):
    _TestTeamStatusAgent.status_db_path = str(tmp_path / "team-status.db")
    return _TestTeamStatusAgent(model="test-model")


def _call_tool(agent, name, **kwargs):
    token = agent_runtime._current_allowed_tools.set(frozenset({name}))
    try:
        return agent._wrapped_tools[name](**kwargs)
    finally:
        agent_runtime._current_allowed_tools.reset(token)


def _prepare_roster(agent, opened_at):
    for identity, name in (
        ("101", "Alex Cohen"),
        ("102", "Dana Levi"),
        ("103", "Jordan Mizrahi"),
        ("104", "Noa Israeli"),
    ):
        agent.register_member(identity, name, opened_at.isoformat())
    assert agent.approve_roster("commander-1", opened_at.isoformat()) == 4


def test_daily_cycle_status_report_and_multiday_unavailability(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)  # 08:00 Israel time
    _prepare_roster(agent, opened_at)

    request_text = _call_tool(agent, "start_daily_attendance_check", now_iso=opened_at.isoformat())
    assert "Alex Cohen" in request_text
    assert "Dana Levi" in request_text

    assert "was stored" in _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="101",
        source_message_id="message-1",
        availability="available",
        original_text="I am available today",
        received_at=(opened_at + timedelta(minutes=10)).isoformat(),
    )
    assert "was stored" in _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="102",
        source_message_id="message-2",
        availability="unavailable",
        reason="sick",
        unavailable_days=5,
        original_text="Unavailable, sick for five days",
        received_at=(opened_at + timedelta(minutes=20)).isoformat(),
    )

    report = _call_tool(
        agent,
        "report_team_availability",
        as_of_iso=(opened_at + timedelta(minutes=30)).isoformat(),
    )
    assert "Alex Cohen: available" in report
    assert "Dana Levi: unavailable" in report
    assert "reason: sick" in report
    assert "Jordan Mizrahi: awaiting response" in report
    assert "Available: 1" in report
    assert "Unavailable: 1" in report
    assert "Awaiting response: 2" in report

    next_day = opened_at + timedelta(days=1)
    next_request = _call_tool(agent, "start_daily_attendance_check", now_iso=next_day.isoformat())
    assert "Alex Cohen" in next_request
    assert "Jordan Mizrahi" in next_request
    assert "Dana Levi" not in next_request

    next_report = _call_tool(agent, "report_team_availability", as_of_iso=next_day.isoformat())
    assert "Dana Levi: unavailable" in next_report
    assert "Alex Cohen: awaiting response" in next_report


def test_daily_check_becomes_due_only_at_configured_local_hour(tmp_path):
    agent = _agent(tmp_path)
    before_check = datetime(2026, 9, 3, 4, 59, tzinfo=timezone.utc)  # 07:59 Israel time
    at_check = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)  # 08:00 Israel time
    _prepare_roster(agent, before_check)

    assert not agent.attendance_check_due(before_check.isoformat())
    assert agent.attendance_check_due(at_check.isoformat())

    outbound_text = agent.run_scheduled_attendance_check(at_check.isoformat())
    assert "Daily readiness-team attendance check" in outbound_text
    assert not agent.attendance_check_due((at_check + timedelta(hours=12)).isoformat())
    assert agent.run_scheduled_attendance_check((at_check + timedelta(hours=12)).isoformat()) is None
    assert agent.attendance_check_due((at_check + timedelta(days=1)).isoformat())


def test_scheduler_does_nothing_until_roster_is_approved(tmp_path):
    agent = _agent(tmp_path)
    at_check = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    agent.register_member("101", "Alex Cohen", at_check.isoformat())

    assert agent.run_scheduled_attendance_check(at_check.isoformat()) is None
    assert agent.status_store.latest_cycle() is None


def test_member_is_requested_again_when_multiday_unavailability_expires(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    _prepare_roster(agent, opened_at)
    _call_tool(agent, "start_daily_attendance_check", now_iso=opened_at.isoformat())
    _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="102",
        source_message_id="message-unavailable",
        availability="unavailable",
        reason="medical leave",
        unavailable_days=5,
        original_text="Unavailable for five days",
        received_at=(opened_at + timedelta(minutes=20)).isoformat(),
    )

    before_expiration = _call_tool(
        agent,
        "start_daily_attendance_check",
        now_iso=(opened_at + timedelta(days=4)).isoformat(),
    )
    assert "Dana Levi" not in before_expiration

    after_expiration = _call_tool(
        agent,
        "start_daily_attendance_check",
        now_iso=(opened_at + timedelta(days=5, minutes=21)).isoformat(),
    )
    assert "Dana Levi" in after_expiration


def test_unavailable_requires_reason_and_duration(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    _prepare_roster(agent, opened_at)
    _call_tool(agent, "start_daily_attendance_check", now_iso=opened_at.isoformat())

    missing_reason = _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="101",
        source_message_id="message-1",
        availability="unavailable",
        original_text="I am unavailable",
        unavailable_days=2,
        received_at=(opened_at + timedelta(minutes=5)).isoformat(),
    )
    missing_duration = _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="101",
        source_message_id="message-2",
        availability="unavailable",
        reason="sick",
        original_text="I am sick",
        received_at=(opened_at + timedelta(minutes=6)).isoformat(),
    )

    assert "provide a reason" in missing_reason
    assert "how many days" in missing_duration


def test_late_response_changes_status_only_after_commander_approval(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    _prepare_roster(agent, opened_at)
    _call_tool(agent, "start_daily_attendance_check", now_iso=opened_at.isoformat())

    late_result = _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="104",
        source_message_id="late-message",
        availability="available",
        original_text="Available",
        received_at=(opened_at + timedelta(hours=2)).isoformat(),
    )
    assert "pending commander approval" in late_result
    pending = agent.status_store.pending_late_responses()
    assert len(pending) == 1

    before = _call_tool(agent, "report_team_availability", as_of_iso=(opened_at + timedelta(hours=2)).isoformat())
    assert "Noa Israeli: awaiting response" in before

    agent.review_late_response(
        pending[0]["response_id"],
        approved=True,
        commander_identity="commander-1",
        reviewed_at=(opened_at + timedelta(hours=2, minutes=5)).isoformat(),
    )
    after = _call_tool(agent, "report_team_availability", as_of_iso=(opened_at + timedelta(hours=2, minutes=6)).isoformat())
    assert "Noa Israeli: available" in after


def test_rejected_late_response_leaves_member_awaiting_response(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    _prepare_roster(agent, opened_at)
    _call_tool(agent, "start_daily_attendance_check", now_iso=opened_at.isoformat())
    _call_tool(
        agent,
        "record_attendance_response",
        telegram_identity="104",
        source_message_id="late-rejected-message",
        availability="available",
        original_text="Available",
        received_at=(opened_at + timedelta(hours=2)).isoformat(),
    )
    pending = agent.status_store.pending_late_responses()

    agent.review_late_response(
        pending[0]["response_id"],
        approved=False,
        commander_identity="commander-1",
        reviewed_at=(opened_at + timedelta(hours=2, minutes=5)).isoformat(),
    )

    report = _call_tool(
        agent,
        "report_team_availability",
        as_of_iso=(opened_at + timedelta(hours=2, minutes=6)).isoformat(),
    )
    assert "Noa Israeli: awaiting response" in report


def test_tool_metadata_preserves_side_effect_and_idempotency_policy(tmp_path):
    agent = _agent(tmp_path)
    tools = {item.name: item for item in agent.exposed_tools()}

    assert tools["report_team_availability"].side_effecting is False
    assert tools["report_team_availability"].idempotent is None
    assert tools["start_daily_attendance_check"].side_effecting is True
    assert tools["start_daily_attendance_check"].idempotent is True
    assert tools["record_attendance_response"].side_effecting is True
    assert tools["record_attendance_response"].idempotent is True


def test_report_protocol_executes_the_read_only_agent_tool(tmp_path):
    agent = _agent(tmp_path)
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    _prepare_roster(agent, opened_at)
    _call_tool(agent, "start_daily_attendance_check", now_iso=opened_at.isoformat())

    def process(_task, allowed_tools):
        assert allowed_tools == ["report_team_availability"]
        text = _call_tool(agent, "report_team_availability", as_of_iso=opened_at.isoformat())
        return AgentResult(status="success", text=text)

    agent.process = process
    step = Step(
        agent_name="team_status_agent",
        task_text="Return the current readiness-team status",
        allowed_tools=("report_team_availability",),
    )
    outcome = execute_step_with_retry(agent, step, _Settings(), sleep_fn=lambda _seconds: None)

    assert outcome.succeeded
    assert "Readiness-team status" in outcome.result_text
    assert "Total: 4" in outcome.result_text
