import types

import pytest

from agents import adapter
from history.precedent import PrecedentMatch
from orchestrator.errors import OrchestrationParseError
from orchestrator.insights import InsightsAgent, build_insight, construct_core_agents
from protocols.model import CriticalityLevel, Protocol, Step
from protocols.retry import StepOutcome


class _ScriptedInsightsAgent:
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
        name="status_check",
        description="d",
        participating_agents=("reference_agent",),
        approved_tools=(),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _outcomes():
    step = Step(agent_name="reference_agent", task_text="check gate 3", allowed_tools=())
    return (StepOutcome(step=step, result_text="gate 3 clear", attempt_count=1, succeeded=True),)


def _precedent(event_id="evt-old"):
    return PrecedentMatch(
        event_id=event_id,
        classification="fire",
        area="north_sector",
        occurred_at="2026-08-01T00:00:00",
        protocol_name="status_check",
        steps_summary=[],
        outcome="succeeded",
        resolved=True,
    )


def test_insights_agent_has_no_tools():
    agent = InsightsAgent(model="m")

    assert agent.exposed_tools() == ()


def test_build_insight_returns_the_agents_free_text_response():
    agent = _ScriptedInsightsAgent("This run matches a resolved precedent; the location is confirmed clear.")

    insight = build_insight(agent, _protocol(), _outcomes())

    assert insight == "This run matches a resolved precedent; the location is confirmed clear."


def test_build_insight_includes_both_task_and_result_for_every_step():
    agent = _ScriptedInsightsAgent("insight")

    build_insight(agent, _protocol(), _outcomes())

    prompt = agent.calls[0][0]
    assert "check gate 3" in prompt  # the task
    assert "gate 3 clear" in prompt  # the result


def test_build_insight_includes_comparable_history_when_given():
    agent = _ScriptedInsightsAgent("insight")

    build_insight(agent, _protocol(), _outcomes(), comparable_history=(_precedent(),))

    assert "evt-old" not in agent.calls[0][0]  # event ID itself isn't required in the prompt...
    assert "fire" in agent.calls[0][0]
    assert "resolved=True" in agent.calls[0][0]


def test_comparable_history_defaults_to_empty():
    agent = _ScriptedInsightsAgent("insight")

    build_insight(agent, _protocol(), _outcomes())  # no comparable_history passed

    assert "no comparable prior events found" in agent.calls[0][0]


def test_build_insight_passes_no_tools():
    agent = _ScriptedInsightsAgent("insight")

    build_insight(agent, _protocol(), _outcomes())

    assert agent.calls[0][1] == []


def test_unclear_task_status_raises():
    agent = _ScriptedInsightsAgent("missing info", status="unclear_task")

    with pytest.raises(OrchestrationParseError):
        build_insight(agent, _protocol(), _outcomes())


def test_construct_core_agents_returns_the_insights_agent_with_the_configured_model():
    from config.base import BaseConfig

    base_config = BaseConfig(main_agent_model="m", history_agent_model="h", insights_agent_model="the-insights-model")

    core_agents = construct_core_agents(base_config)

    assert set(core_agents) == {"insights_agent"}
    assert core_agents["insights_agent"].model == "the-insights-model"


def test_end_to_end_through_the_mocked_adapter(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("Consistent with a prior resolved incident at the same location.")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    agent = InsightsAgent(model="fake-model")
    insight = build_insight(agent, _protocol(), _outcomes(), comparable_history=(_precedent(),))

    assert insight == "Consistent with a prior resolved incident at the same location."
