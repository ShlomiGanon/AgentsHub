import types

import pytest

from agents import adapter
from agents.reference import ReferenceAgent
from agents.registry import build_agent_registry
from orchestrator.errors import OrchestrationParseError
from orchestrator.formulation import _parse_formulation_response, formulate_tasks, rewrite_task
from protocols.model import CriticalityLevel, Protocol, Step


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


@pytest.fixture
def registry():
    agent = ReferenceAgent(model="m")
    return build_agent_registry({}, [agent])


def _protocol(**overrides):
    fields = dict(
        name="dispatch_response",
        description="d",
        participating_agents=("reference_agent",),
        approved_tools=("check_status", "record_action"),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    fields.update(overrides)
    return Protocol(**fields)


# -- Parser -------------------------------------------------------------


def test_parse_single_agent_block():
    tasks = _parse_formulation_response("AGENT: reference_agent\nTASK: check gate 3")

    assert tasks == {"reference_agent": "check gate 3"}


def test_parse_multiple_agent_blocks():
    response = "AGENT: a1\nTASK: do the first thing\nAGENT: a2\nTASK: do the second thing"

    tasks = _parse_formulation_response(response)

    assert tasks == {"a1": "do the first thing", "a2": "do the second thing"}


# -- formulate_tasks ------------------------------------------------------


def test_formulate_tasks_produces_a_step_per_participating_agent(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: check status at gate 3")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert len(result.steps) == 1
    assert result.steps[0].agent_name == "reference_agent"
    assert result.steps[0].task_text == "check status at gate 3"


def test_allowed_tools_are_filtered_to_what_the_agent_actually_exposes(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")
    protocol = _protocol(approved_tools=("check_status", "record_action", "some_other_tool_no_agent_has"))

    result = formulate_tasks(agent, protocol, registry, "raw", "fire", "north", "d")

    assert set(result.steps[0].allowed_tools) == {"check_status", "record_action"}


def test_missing_an_agents_block_fails_naming_that_agent(registry):
    agent = _ScriptedMainAgent("this response has no AGENT/TASK blocks at all")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert not result.success
    assert result.failed_agent_name == "reference_agent"


def test_unclear_task_status_fails_formulation(registry):
    agent = _ScriptedMainAgent("missing context", status="unclear_task")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert not result.success


def test_precedent_context_defaults_to_empty_and_is_optional(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    result = formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")  # no precedent_context passed

    assert result.success


def test_precedent_context_appears_in_the_prompt_when_given(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d", precedent_context=("prior incident X",))

    assert "prior incident X" in agent.calls[0][0]


def test_formulate_tasks_passes_no_tools_to_the_main_agent(registry):
    agent = _ScriptedMainAgent("AGENT: reference_agent\nTASK: t")

    formulate_tasks(agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert agent.calls[0][1] == []


# -- rewrite_task -----------------------------------------------------------


def test_rewrite_task_returns_the_full_response_as_the_new_task():
    agent = _ScriptedMainAgent("check status specifically at gate 3, not the whole perimeter")
    step = Step(agent_name="reference_agent", task_text="check status", allowed_tools=("check_status",))

    rewritten = rewrite_task(agent, step, "which location specifically")

    assert rewritten == "check status specifically at gate 3, not the whole perimeter"


def test_rewrite_task_raises_when_the_agent_reports_unclear_again():
    agent = _ScriptedMainAgent("still unclear", status="unclear_task")
    step = Step(agent_name="a", task_text="t", allowed_tools=())

    with pytest.raises(OrchestrationParseError):
        rewrite_task(agent, step, "missing X")


def test_rewrite_task_matches_the_executors_task_rewriter_signature():
    import functools

    from protocols.executor import execute_steps
    from agents.errors import AgentModelError

    agent = _ScriptedMainAgent("rewritten task text")
    rewriter = functools.partial(rewrite_task, agent)

    class _FailingThenSucceedingAgent:
        name = "reference_agent"
        _calls = 0

        def exposed_tools(self):
            return ()

        def process(self, text, allowed_tools):
            type(self)._calls += 1
            if type(self)._calls == 1:
                from agents.results import AgentResult

                return AgentResult(status="unclear_task", text="missing location")

            from agents.results import AgentResult

            return AgentResult(status="success", text="done")

    class _FakeSettings:
        def get_retry_count(self):
            return 3

    step = Step(agent_name="reference_agent", task_text="check status", allowed_tools=())
    result = execute_steps([step], {"reference_agent": _FailingThenSucceedingAgent()}, _FakeSettings(), task_rewriter=rewriter, sleep_fn=lambda s: None)

    assert result.completed
    assert result.step_outcomes[0].result_text == "done"


# -- End-to-end through the mocked adapter -----------------------------------


def test_end_to_end_through_the_mocked_adapter(monkeypatch, registry):
    from orchestrator.main_agent import MainAgent

    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("AGENT: reference_agent\nTASK: check gate 3 status")

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)

    main_agent = MainAgent(model="fake-model")
    result = formulate_tasks(main_agent, _protocol(), registry, "raw", "fire", "north", "d")

    assert result.success
    assert result.steps[0].task_text == "check gate 3 status"
