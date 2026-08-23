from agents.errors import AgentModelError
from agents.results import AgentResult
from agents.tooling import ToolInfo
from protocols.executor import execute_steps
from protocols.model import Step

READ_ONLY_TOOL = (ToolInfo(name="check_status", description="d", side_effecting=False, idempotent=None),)


class _ScriptedAgent:
    def __init__(self, name, responses):
        self.name = name
        self._responses = list(responses)
        self.calls = []

    def exposed_tools(self):
        return READ_ONLY_TOOL

    def process(self, text, allowed_tools):
        self.calls.append((text, tuple(allowed_tools)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeSettings:
    def get_retry_count(self):
        return 3


def _no_sleep(seconds):
    pass


def _step(agent_name, task_text, allowed_tools=("check_status",)):
    return Step(agent_name=agent_name, task_text=task_text, allowed_tools=allowed_tools)


def test_a_single_successful_step():
    agent = _ScriptedAgent("a1", [AgentResult(status="success", text="ok")])
    steps = [_step("a1", "check gate 3")]

    result = execute_steps(steps, {"a1": agent}, _FakeSettings(), sleep_fn=_no_sleep)

    assert result.completed
    assert len(result.step_outcomes) == 1
    assert result.step_outcomes[0].result_text == "ok"


def test_task_text_reaches_the_agent_unmodified():
    agent = _ScriptedAgent("a1", [AgentResult(status="success", text="ok")])
    steps = [_step("a1", "exactly this text, nothing added")]

    execute_steps(steps, {"a1": agent}, _FakeSettings(), sleep_fn=_no_sleep)

    assert agent.calls[0][0] == "exactly this text, nothing added"


def test_approved_tools_are_passed_through_exactly():
    agent = _ScriptedAgent("a1", [AgentResult(status="success", text="ok")])
    steps = [_step("a1", "x", allowed_tools=("check_status", "some_other_tool"))]

    execute_steps(steps, {"a1": agent}, _FakeSettings(), sleep_fn=_no_sleep)

    assert agent.calls[0][1] == ("check_status", "some_other_tool")


def test_multi_step_run_executes_every_step_in_order_when_all_succeed():
    agent_a = _ScriptedAgent("a1", [AgentResult(status="success", text="first")])
    agent_b = _ScriptedAgent("a2", [AgentResult(status="success", text="second")])
    steps = [_step("a1", "do first"), _step("a2", "do second")]

    result = execute_steps(steps, {"a1": agent_a, "a2": agent_b}, _FakeSettings(), sleep_fn=_no_sleep)

    assert result.completed
    assert [o.result_text for o in result.step_outcomes] == ["first", "second"]


def test_run_stops_at_the_first_permanent_failure_and_keeps_prior_results():
    agent_a = _ScriptedAgent("a1", [AgentResult(status="success", text="first")])
    agent_b = _ScriptedAgent("a2", [AgentModelError("a2", "boom")] * 3)  # exhausts the limit of 3
    agent_c = _ScriptedAgent("a3", [AgentResult(status="success", text="never reached")])
    steps = [_step("a1", "first"), _step("a2", "second"), _step("a3", "third")]

    result = execute_steps(steps, {"a1": agent_a, "a2": agent_b, "a3": agent_c}, _FakeSettings(), sleep_fn=_no_sleep)

    assert not result.completed
    assert result.failed_step_index == 1
    assert result.failed_step_agent == "a2"
    assert result.failure_cause is not None
    # step one's result is preserved even though the run failed overall
    assert len(result.step_outcomes) == 2
    assert result.step_outcomes[0].result_text == "first"
    assert result.step_outcomes[0].succeeded is True
    assert result.step_outcomes[1].succeeded is False
    # the third step was never attempted
    assert agent_c.calls == []


def test_steps_are_independent_one_failing_does_not_touch_anothers_call_log():
    agent_a = _ScriptedAgent("a1", [AgentModelError("a1", "boom")] * 3)
    agent_b = _ScriptedAgent("a2", [AgentResult(status="success", text="second")])
    steps = [_step("a1", "first"), _step("a2", "second")]

    execute_steps(steps, {"a1": agent_a, "a2": agent_b}, _FakeSettings(), sleep_fn=_no_sleep)

    assert agent_b.calls == []  # never reached, and nothing about it was touched
