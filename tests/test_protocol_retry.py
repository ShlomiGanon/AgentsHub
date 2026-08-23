import pytest

from agents.errors import AgentModelError
from agents.results import AgentResult
from agents.tooling import ToolInfo
from protocols.model import Step
from protocols.retry import execute_step_with_retry


class _ScriptedAgent:
    """A duck-typed stand-in for agents.base.Agent — retry.py only ever
    calls .process()/.exposed_tools(), never checks the type, so tests
    don't need crewai or a real Agent subclass at all.
    """

    name = "scripted_agent"

    def __init__(self, tool_infos=(), responses=()):
        self._tool_infos = tool_infos
        self._responses = list(responses)
        self.calls = []

    def exposed_tools(self):
        return self._tool_infos

    def process(self, text, allowed_tools):
        self.calls.append((text, tuple(allowed_tools)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeSettings:
    def __init__(self, retry_count):
        self.retry_count = retry_count
        self.call_count = 0

    def get_retry_count(self):
        self.call_count += 1
        return self.retry_count


def _step(allowed_tools=("check_status",)):
    return Step(agent_name="scripted_agent", task_text="check gate 3", allowed_tools=allowed_tools)


READ_ONLY_TOOL = (ToolInfo(name="check_status", description="d", side_effecting=False, idempotent=None),)
SIDE_EFFECTING_TOOL = (ToolInfo(name="record_action", description="d", side_effecting=True, idempotent=False),)


def _sleeps():
    calls = []
    return calls, calls.append


def test_successful_first_attempt_returns_immediately():
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentResult(status="success", text="ok")])
    sleeps, sleep_fn = _sleeps()

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=sleep_fn)

    assert outcome.succeeded
    assert outcome.result_text == "ok"
    assert outcome.attempt_count == 1
    assert sleeps == []


def test_execution_failure_is_retried_with_unchanged_task_text():
    agent = _ScriptedAgent(
        tool_infos=READ_ONLY_TOOL,
        responses=[AgentModelError("scripted_agent", "boom"), AgentModelError("scripted_agent", "boom again"), AgentResult(status="success", text="ok")],
    )
    sleeps, sleep_fn = _sleeps()

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(5), sleep_fn=sleep_fn)

    assert outcome.succeeded
    assert outcome.attempt_count == 3
    assert {text for text, _ in agent.calls} == {"check gate 3"}  # never composed/modified
    assert len(sleeps) == 2  # backoff before each retry, not before the first attempt


def test_unclear_task_is_rewritten_and_resent():
    agent = _ScriptedAgent(
        tool_infos=READ_ONLY_TOOL,
        responses=[AgentResult(status="unclear_task", text="which gate?"), AgentResult(status="success", text="ok")],
    )
    rewrites = []

    def rewriter(step, missing):
        rewrites.append(missing)
        return f"check gate 3 (clarified: {missing})"

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(5), task_rewriter=rewriter, sleep_fn=lambda s: None)

    assert outcome.succeeded
    assert rewrites == ["which gate?"]
    assert agent.calls[1][0] == "check gate 3 (clarified: which gate?)"


def test_no_rewriter_fails_immediately_on_unclear_task():
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentResult(status="unclear_task", text="which gate?")])

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(5), task_rewriter=None, sleep_fn=lambda s: None)

    assert not outcome.succeeded
    assert outcome.attempt_count == 1
    assert len(agent.calls) == 1  # never blindly resent unchanged


def test_side_effecting_nonidempotent_tool_blocks_retry_after_first_failure():
    agent = _ScriptedAgent(
        tool_infos=SIDE_EFFECTING_TOOL,
        responses=[AgentModelError("scripted_agent", "boom")] * 5,
    )

    outcome = execute_step_with_retry(agent, _step(allowed_tools=("record_action",)), _FakeSettings(5), sleep_fn=lambda s: None)

    assert not outcome.succeeded
    assert outcome.attempt_count == 1
    assert len(agent.calls) == 1  # never retried even though the attempt limit allows more


def test_idempotent_side_effecting_tool_may_retry():
    idempotent_tool = (ToolInfo(name="set_status", description="d", side_effecting=True, idempotent=True),)
    agent = _ScriptedAgent(
        tool_infos=idempotent_tool,
        responses=[AgentModelError("scripted_agent", "boom"), AgentResult(status="success", text="ok")],
    )

    outcome = execute_step_with_retry(agent, _step(allowed_tools=("set_status",)), _FakeSettings(5), sleep_fn=lambda s: None)

    assert outcome.succeeded
    assert outcome.attempt_count == 2


def test_read_only_step_retries_up_to_the_limit_then_fails():
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentModelError("scripted_agent", "boom")] * 3)

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=lambda s: None)

    assert not outcome.succeeded
    assert outcome.attempt_count == 3
    assert len(agent.calls) == 3


def test_execution_failures_and_unclear_task_share_one_attempt_limit():
    agent = _ScriptedAgent(
        tool_infos=READ_ONLY_TOOL,
        responses=[AgentModelError("scripted_agent", "boom"), AgentResult(status="unclear_task", text="x")],
    )

    outcome = execute_step_with_retry(
        agent, _step(), _FakeSettings(2), task_rewriter=lambda step, missing: "rewritten", sleep_fn=lambda s: None
    )

    assert not outcome.succeeded
    assert outcome.attempt_count == 2  # both kinds counted against the same limit


def test_attempt_limit_is_read_live_not_cached():
    settings = _FakeSettings(3)
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentModelError("scripted_agent", "boom")] * 3)

    execute_step_with_retry(agent, _step(), settings, sleep_fn=lambda s: None)

    assert settings.call_count == 3  # read fresh on every attempt, never cached once


def test_backoff_is_applied_between_attempts_via_injectable_sleep_fn():
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentModelError("scripted_agent", "boom")] * 3)
    sleeps, sleep_fn = _sleeps()

    execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=sleep_fn, backoff_seconds=2.5)

    assert sleeps == [2.5, 2.5]  # between attempts 1->2 and 2->3, not after the last
