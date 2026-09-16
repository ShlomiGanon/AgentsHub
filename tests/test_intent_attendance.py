"""Intent-layer regressions for clear team attendance reports."""

import json

import pytest

from agents import runtime as agent_runtime
from agents.team_status_agent import TeamStatusAgent
from orchestrator.main_agent import IntentResult, classify_intent
from protocols.model import CriticalityLevel, Protocol


class _ScriptedAgent:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    def process(self, prompt, allowed_tools):
        self.calls.append(prompt)

        class _Result:
            status = "success"

            def __init__(self, text):
                self.text = text

        return _Result(self.response)


class _SequenceAgent(_ScriptedAgent):
    def __init__(self, responses: list[str]):
        super().__init__(responses[0])
        self.responses = responses

    def process(self, prompt, allowed_tools):
        self.calls.append(prompt)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]

        class _Result:
            status = "success"

            def __init__(self, text):
                self.text = text

        return _Result(response)


def _protocols():
    return (
        Protocol(
            name="record_attendance_response",
            description="records a readiness-team member's availability report",
            participating_agents=("team_status_agent",),
            approved_tools=("record_attendance_response",),
            expected_success_output="attendance response recorded",
            criticality=CriticalityLevel.LOW,
            approval_flag=False,
        ),
    )


def _structured_intent(**overrides) -> str:
    payload = {
        "primary_intent": "needs_clarification",
        "asks_for_information": False,
        "reports_occurrence": False,
        "requests_action": False,
        "social_only": False,
        "is_quoted": False,
        "is_hypothetical": False,
        "is_followup_without_context": False,
        "evidence": {},
        "matched_protocol_names": [],
        "reason": "missing business details",
        "ambiguity_reason": "reason or duration was not supplied",
        "clarification_question": "What is the reason?",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize(
    "message",
    [
        "אני במילואים מראשון עד שלישי בערב, לא זמין ביישוב",
        "קמתי עם חום גבוה, לא אוכל להשתתף בסיור הערב",
        "אני לא זמין היום",
    ],
)
def test_clear_attendance_statements_are_reports_even_when_model_asks_for_business_details(message):
    agent = _ScriptedAgent(_structured_intent())

    result = classify_intent(agent, _protocols(), message)

    # `report` is the intent-layer value that lets extraction select the
    # profile's team_attendance_report event type. Reason/duration validation
    # belongs to record_attendance_response after the event exists.
    assert result.intent == "report"
    assert len(agent.calls) == 1


def test_valid_operational_report_with_ambiguity_reason_is_not_coerced_to_clarification():
    message = "אני לא זמין היום"
    agent = _ScriptedAgent(
        _structured_intent(
            primary_intent="report",
            reports_occurrence=True,
            evidence={"report": message},
            matched_protocol_names=["record_attendance_response"],
        )
    )

    result = classify_intent(agent, _protocols(), message)

    assert result == IntentResult(
        "report", "missing business details"
    )
    assert len(agent.calls) == 1


def test_schema_valid_clarification_does_not_trigger_a_retry():
    agent = _ScriptedAgent(
        _structured_intent(
            ambiguity_reason="the current message says only 'do that'",
            clarification_question="What should I do?",
        )
    )

    result = classify_intent(agent, _protocols(), "do that")

    assert result.intent == "needs_clarification"
    assert len(agent.calls) == 1


def test_parse_failure_retries_once_and_accepts_the_repaired_response():
    message = "אני לא זמין היום"
    agent = _SequenceAgent(["not JSON", _structured_intent(primary_intent="report", reports_occurrence=True, evidence={"report": message})])

    result = classify_intent(agent, _protocols(), message)

    assert result.intent == "report"
    assert len(agent.calls) == 2
    assert "previous response was invalid" in agent.calls[1]


class _TeamStatusForTest(TeamStatusAgent):
    status_db_path = ""


def test_missing_unavailability_reason_is_asked_by_team_status_after_intent(tmp_path):
    _TeamStatusForTest.status_db_path = str(tmp_path / "team-status.db")
    agent = _TeamStatusForTest(model="test-model")
    agent.register_member("member-1", "Member One", "2026-09-16T08:00:00+00:00")
    agent.approve_roster("commander-1", "2026-09-16T08:00:00+00:00")

    token = agent_runtime._current_allowed_tools.set(frozenset({"record_attendance_response"}))
    try:
        with agent_runtime.authenticated_request_identity("member-1"):
            answer = agent._wrapped_tools["record_attendance_response"](
                source_message_id="attendance-1",
                availability="unavailable",
                original_text="אני לא זמין היום",
                reason="",
                unavailable_days=1,
            )
    finally:
        agent_runtime._current_allowed_tools.reset(token)

    assert "reason" in answer.lower()
