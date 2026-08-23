import types

import pytest

from agents import adapter
from orchestrator.errors import OrchestrationParseError
from orchestrator.intent import IntentResult, _parse_intent_response, classify_intent
from protocols.model import CriticalityLevel, Protocol


class _ScriptedMainAgent:
    def __init__(self, response_text, status="success"):
        self._response_text = response_text
        self._status = status
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


def _protocols():
    return (
        Protocol(
            name="dispatch_response",
            description="applies when a response must be dispatched",
            participating_agents=(),
            approved_tools=(),
            expected_success_output="x",
            criticality=CriticalityLevel.HIGH,
            approval_flag=True,
        ),
    )


# -- Parser -------------------------------------------------------------


@pytest.mark.parametrize("intent", ["question", "report", "request"])
def test_parse_each_intent(intent):
    result = _parse_intent_response(f"INTENT: {intent}\nREASON: because")

    assert result == IntentResult(intent=intent, reason="because")


def test_parse_is_case_insensitive():
    result = _parse_intent_response("INTENT: REQUEST\nREASON: r")

    assert result.intent == "request"


def test_parse_rejects_an_invalid_intent_word():
    with pytest.raises(OrchestrationParseError):
        _parse_intent_response("INTENT: complaint\nREASON: r")


def test_parse_rejects_missing_reason():
    with pytest.raises(OrchestrationParseError):
        _parse_intent_response("INTENT: question")


# -- classify_intent ------------------------------------------------------


def test_classify_intent_returns_the_parsed_result():
    agent = _ScriptedMainAgent("INTENT: request\nREASON: asks for a response to be dispatched")

    result = classify_intent(agent, _protocols(), "please send someone to gate 3")

    assert result.intent == "request"


def test_protocols_appear_in_the_prompt():
    agent = _ScriptedMainAgent("INTENT: question\nREASON: r")

    classify_intent(agent, _protocols(), "is gate 3 ok?")

    assert "dispatch_response" in agent.calls[0][0]
    assert "applies when a response must be dispatched" in agent.calls[0][0]


def test_classify_intent_passes_no_tools():
    agent = _ScriptedMainAgent("INTENT: question\nREASON: r")

    classify_intent(agent, _protocols(), "is gate 3 ok?")

    assert agent.calls[0][1] == []


def test_unclear_task_status_raises():
    agent = _ScriptedMainAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        classify_intent(agent, _protocols(), "some message")


def test_end_to_end_through_the_mocked_adapter(monkeypatch):
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("INTENT: report\nREASON: describes something that happened")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    result = classify_intent(main_agent, _protocols(), "smoke seen near gate 3")

    assert result.intent == "report"
