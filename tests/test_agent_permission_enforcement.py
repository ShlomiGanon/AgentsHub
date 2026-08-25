import pytest

from agents import base
from agents.base import Agent
from agents.tooling import tool


class _ToolAgent(Agent):
    name = "test_agent"
    role = "a test role"
    system_prompt = "a test system prompt"

    def __init__(self, model="test-model"):
        self.calls = []
        super().__init__(model)

    @tool("read_thing", "reads a thing", side_effecting=False)
    def read_thing(self):
        self.calls.append("read_thing")
        return "read ok"

    @tool("write_thing", "writes a thing", side_effecting=True, idempotent=False)
    def write_thing(self):
        self.calls.append("write_thing")
        return "write ok"


class _IncompleteAgent(Agent):
    role = "missing a name and a system prompt"


def test_missing_required_class_attrs_fails_at_construction():
    with pytest.raises(TypeError, match="name"):
        _IncompleteAgent(model="x")


def test_descriptor_is_built_at_construction_with_both_tools():
    agent = _ToolAgent()

    assert agent.descriptor.name == "test_agent"
    assert agent.descriptor.model == "test-model"
    assert {t.name for t in agent.exposed_tools()} == {"read_thing", "write_thing"}


def test_tool_call_allowed_when_in_allowed_tools():
    agent = _ToolAgent()

    token = base._current_allowed_tools.set(frozenset({"read_thing"}))
    try:
        result = agent._wrapped_tools["read_thing"]()
    finally:
        base._current_allowed_tools.reset(token)

    assert result == "read ok"
    assert agent.calls == ["read_thing"]


def test_tool_call_refused_when_not_in_allowed_tools():
    agent = _ToolAgent()

    token = base._current_allowed_tools.set(frozenset({"read_thing"}))  # write_thing not allowed
    try:
        result = agent._wrapped_tools["write_thing"]()
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.calls == []  # the real method never ran


def test_tool_call_refused_outside_any_call_context():
    agent = _ToolAgent()

    result = agent._wrapped_tools["write_thing"]()  # no allowed_tools context set at all

    assert "not permitted" in result
    assert agent.calls == []


def test_blocked_attempt_is_logged(caplog):
    agent = _ToolAgent()

    with caplog.at_level("INFO"):
        agent._wrapped_tools["write_thing"]()

    blocked = [r for r in caplog.records if getattr(r, "event", None) == "tool_blocked"]
    assert len(blocked) == 1
    assert blocked[0].agent == "test_agent"
    assert blocked[0].tool == "write_thing"


def test_allowed_attempt_is_logged(caplog):
    # DEBUG, not INFO — a successful tool call is internal detail, noise
    # in normal operation, unlike a blocked call (which stays INFO,
    # asserted separately above by test_blocked_attempt_is_logged).
    agent = _ToolAgent()

    token = base._current_allowed_tools.set(frozenset({"read_thing"}))
    try:
        with caplog.at_level("DEBUG"):
            agent._wrapped_tools["read_thing"]()
    finally:
        base._current_allowed_tools.reset(token)

    calls = [r for r in caplog.records if getattr(r, "event", None) == "tool_call"]
    assert len(calls) == 1
    assert calls[0].levelname == "DEBUG"
    assert calls[0].agent == "test_agent"
    assert calls[0].tool == "read_thing"


def test_permission_check_is_per_call_not_bound_at_construction():
    # The same agent instance legitimately has different permissions on
    # two consecutive calls — nothing about the wrapping is fixed once.
    agent = _ToolAgent()

    token = base._current_allowed_tools.set(frozenset({"write_thing"}))
    try:
        first = agent._wrapped_tools["write_thing"]()
    finally:
        base._current_allowed_tools.reset(token)

    token = base._current_allowed_tools.set(frozenset({"read_thing"}))
    try:
        second = agent._wrapped_tools["write_thing"]()
    finally:
        base._current_allowed_tools.reset(token)

    assert first == "write ok"
    assert "not permitted" in second
