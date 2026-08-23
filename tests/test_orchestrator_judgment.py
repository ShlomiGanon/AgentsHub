import types

import pytest

from agents import adapter
from orchestrator.errors import OrchestrationParseError
from orchestrator.judgment import SuccessVerdict, _parse_judgment_response, judge_success
from protocols.model import CriticalityLevel, Protocol, Step
from protocols.retry import StepOutcome


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


def _protocol():
    return Protocol(
        name="p",
        description="d",
        participating_agents=("a1",),
        approved_tools=(),
        expected_success_output="the location is confirmed clear",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _outcomes():
    step = Step(agent_name="a1", task_text="check gate 3", allowed_tools=())
    return (StepOutcome(step=step, result_text="gate 3 is clear", attempt_count=1, succeeded=True),)


# -- Parser -------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["success", "failure", "uncertain"])
def test_parse_each_verdict(verdict):
    result = _parse_judgment_response(f"VERDICT: {verdict}\nREASONING: because")

    assert result == SuccessVerdict(verdict=verdict, reasoning="because")


def test_parse_is_case_insensitive_on_verdict():
    result = _parse_judgment_response("VERDICT: SUCCESS\nREASONING: r")

    assert result.verdict == "success"


def test_parse_rejects_an_invalid_verdict_word():
    with pytest.raises(OrchestrationParseError):
        _parse_judgment_response("VERDICT: maybe\nREASONING: r")


def test_parse_rejects_missing_reasoning():
    with pytest.raises(OrchestrationParseError):
        _parse_judgment_response("VERDICT: success")


# -- judge_success ------------------------------------------------------


def test_judge_success_returns_the_parsed_verdict():
    agent = _ScriptedMainAgent("VERDICT: success\nREASONING: matches expected output")

    verdict = judge_success(agent, _protocol(), _outcomes())

    assert verdict.verdict == "success"


def test_judge_success_works_with_default_empty_insight_text():
    agent = _ScriptedMainAgent("VERDICT: uncertain\nREASONING: r")

    verdict = judge_success(agent, _protocol(), _outcomes())  # insight_text not passed

    assert verdict.verdict == "uncertain"
    assert "Insight from comparing" not in agent.calls[0][0]


def test_insight_text_appears_in_the_prompt_when_given():
    agent = _ScriptedMainAgent("VERDICT: success\nREASONING: r")

    judge_success(agent, _protocol(), _outcomes(), insight_text="matches a resolved precedent")

    assert "matches a resolved precedent" in agent.calls[0][0]


def test_judge_success_passes_no_tools():
    agent = _ScriptedMainAgent("VERDICT: success\nREASONING: r")

    judge_success(agent, _protocol(), _outcomes())

    assert agent.calls[0][1] == []


def test_unclear_task_status_raises():
    agent = _ScriptedMainAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        judge_success(agent, _protocol(), _outcomes())


def test_end_to_end_through_the_mocked_adapter(monkeypatch):
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("VERDICT: failure\nREASONING: gate was not actually checked")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    verdict = judge_success(main_agent, _protocol(), _outcomes())

    assert verdict.verdict == "failure"
