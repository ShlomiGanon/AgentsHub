import pytest

from agents.errors import AgentModelError
from agents.results import AgentResult
from agents.runtime import ToolInfo
from protocols.model import Step
from protocols.executor import execute_step_with_retry


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
        self._resource_unavailable_signal = None

    def exposed_tools(self):
        return self._tool_infos

    def process(self, text, allowed_tools):
        self.calls.append((text, tuple(allowed_tools)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def signal_resource_unavailable(self, resource_kind, area, reason):
        self._resource_unavailable_signal = (resource_kind, area, reason)

    def take_resource_unavailable_signal(self):
        value = self._resource_unavailable_signal
        self._resource_unavailable_signal = None
        return value


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


# -- resource-unavailable signal: deterministic, set by a tool, never by wording -------------


class _SignalingAgent(_ScriptedAgent):
    """A tool method calling self.signal_resource_unavailable right before returning, exactly
    as a real dispatch tool would after its own persistence layer confirms no unit is
    available -- the process() override here stands in for that tool call. Instance state
    (not a ContextVar): a real dispatch tool proved this must survive CrewAI running the tool
    call in a thread/context the caller's ContextVar reads never saw."""

    def __init__(self, *args, signal_on_calls=frozenset({1}), **kwargs):
        super().__init__(*args, **kwargs)
        self._signal_on_calls = signal_on_calls
        self._call_number = 0

    def process(self, text, allowed_tools):
        self._call_number += 1
        if self._call_number in self._signal_on_calls:
            self.signal_resource_unavailable("drone", "east_gate", "no ready drones available")
        return super().process(text, allowed_tools)


def test_resource_unavailable_signal_is_attached_to_a_successful_agent_step_outcome():
    agent = _SignalingAgent(
        tool_infos=READ_ONLY_TOOL,
        responses=[AgentResult(status="success", text="Drone dispatch failed: no ready drones available")],
    )

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=lambda s: None)

    assert outcome.succeeded  # the specialist agent still produced a normal final answer
    assert outcome.resource_unavailable is not None
    assert outcome.resource_unavailable.resource_kind == "drone"
    assert outcome.resource_unavailable.area == "east_gate"
    assert outcome.resource_unavailable.reason == "no ready drones available"


def test_resource_unavailable_signal_is_absent_when_no_tool_signaled_it():
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentResult(status="success", text="ok")])

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=lambda s: None)

    assert outcome.resource_unavailable is None


def test_resource_unavailable_signal_does_not_leak_into_a_later_step_on_the_same_agent():
    # The same agent instance is reused across a multi-step protocol -- a signal from an
    # earlier step, once read by the executor, must not still be set for a later step whose
    # own tool call never signals anything.
    agent = _SignalingAgent(
        tool_infos=READ_ONLY_TOOL,
        responses=[
            AgentResult(status="success", text="Drone dispatch failed: no ready drones available"),
            AgentResult(status="success", text="ok"),
        ],
        signal_on_calls=frozenset({1}),
    )
    first = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=lambda s: None)
    assert first.resource_unavailable is not None

    second = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=lambda s: None)
    assert second.resource_unavailable is None


def test_resource_unavailable_signal_from_a_call_outside_any_step_never_leaks_into_the_next_step():
    # Fix (c): a viewer's read-only lookup (e.g. camera status) never goes through
    # execute_step_with_retry at all, so nothing ever consumes a signal it sets. The NEXT real
    # protocol step on that same agent instance must still start clean.
    agent = _ScriptedAgent(tool_infos=READ_ONLY_TOOL, responses=[AgentResult(status="success", text="ok")])
    agent.signal_resource_unavailable("camera", "east_gate", "no camera covers this area")  # never consumed by anything

    outcome = execute_step_with_retry(agent, _step(), _FakeSettings(3), sleep_fn=lambda s: None)

    assert outcome.resource_unavailable is None


# -- direct_tool steps: no crewai, no LLM call at all -------------------------


class _DirectToolAgent:
    """A duck-typed stand-in exposing a plain callable tool method -- no .process(), no
    crewai — proves execute_step_with_retry never touches the LLM path for kind='direct_tool'."""

    name = "scripted_agent"

    def __init__(self, tool_result=None, raises=None):
        self._tool_result = tool_result
        self._raises = raises
        self.calls = []
        self._resource_unavailable_signal = None

    def signal_resource_unavailable(self, resource_kind, area, reason):
        self._resource_unavailable_signal = (resource_kind, area, reason)

    def take_resource_unavailable_signal(self):
        value = self._resource_unavailable_signal
        self._resource_unavailable_signal = None
        return value

    def record_attendance_response(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._tool_result

    def exposed_tools(self):
        return READ_ONLY_TOOL

    def process(self, text, allowed_tools):
        raise AssertionError("a direct_tool step must never call .process() (no LLM call)")


def _direct_tool_step(kwargs, allowed_tools=("record_attendance_response",)):
    return Step(
        agent_name="scripted_agent", task_text="record attendance", allowed_tools=allowed_tools,
        kind="direct_tool", direct_tool_name="record_attendance_response", direct_tool_kwargs=kwargs,
    )


def test_direct_tool_step_calls_the_tool_method_directly_with_the_bound_kwargs():
    agent = _DirectToolAgent(tool_result="The attendance response was stored.")

    outcome = execute_step_with_retry(agent, _direct_tool_step({"availability": "available"}), _FakeSettings(2))

    assert agent.calls == [{"availability": "available"}]
    assert outcome.succeeded
    assert outcome.result_text == "The attendance response was stored."


def test_direct_tool_step_never_calls_process_even_when_it_would_raise():
    agent = _DirectToolAgent(tool_result="The attendance response was stored.")

    outcome = execute_step_with_retry(agent, _direct_tool_step({}), _FakeSettings(2))

    assert outcome.succeeded  # would have raised AssertionError above if .process() were ever called


def test_direct_tool_step_fails_on_a_known_failure_marker_in_the_tool_result():
    agent = _DirectToolAgent(tool_result="Clarification required: specify whether available or unavailable.")

    outcome = execute_step_with_retry(agent, _direct_tool_step({}), _FakeSettings(2))

    assert not outcome.succeeded
    assert outcome.status == "failed"
    assert "Clarification required" in outcome.failure_reason


def test_direct_tool_step_fails_when_the_tool_method_raises():
    agent = _DirectToolAgent(raises=RuntimeError("persistence unavailable"))

    outcome = execute_step_with_retry(agent, _direct_tool_step({}), _FakeSettings(2))

    assert not outcome.succeeded
    assert "persistence unavailable" in outcome.failure_reason


def test_direct_tool_step_has_no_retry_loop():
    # A single call, attempt_count=1, regardless of the configured retry limit -- there is no
    # crewai loop here to retry within.
    agent = _DirectToolAgent(tool_result="Clarification required: area is required.")

    outcome = execute_step_with_retry(agent, _direct_tool_step({}), _FakeSettings(5))

    assert len(agent.calls) == 1
    assert outcome.attempt_count == 1


class _SignalingDirectToolAgent(_DirectToolAgent):
    def record_attendance_response(self, **kwargs):
        self.signal_resource_unavailable("squad_member", "west_gate", "no roster members available")
        return super().record_attendance_response(**kwargs)


def test_direct_tool_step_also_attaches_a_resource_unavailable_signal():
    agent = _SignalingDirectToolAgent(tool_result="The attendance response was stored.")

    outcome = execute_step_with_retry(agent, _direct_tool_step({}), _FakeSettings(2))

    assert outcome.succeeded
    assert outcome.resource_unavailable is not None
    assert outcome.resource_unavailable.resource_kind == "squad_member"
