import pytest

from agents import base
from agents.base import Agent
from agents.runtime import tool


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

import pytest

from agents.runtime import ToolInfo, exposed_tools_for, tool, tool_info_of


def test_tool_requires_side_effecting_explicitly():
    # side_effecting is a required keyword-only parameter with no default —
    # omitting it entirely is a TypeError from Python itself, which is
    # exactly "no default" enforced as strongly as possible.
    with pytest.raises(TypeError, match="side_effecting"):
        tool("t", "does a thing")

def test_side_effecting_true_requires_idempotent():
    with pytest.raises(ValueError, match="idempotent"):
        tool("t", "does a thing", side_effecting=True)


def test_idempotent_forbidden_for_read_only_tool():
    with pytest.raises(ValueError, match="no meaning"):
        tool("t", "does a thing", side_effecting=False, idempotent=True)


def test_read_only_tool_decorates_successfully():
    @tool("check_status", "Returns a canned status.", side_effecting=False)
    def check_status(self):
        return "ok"

    info = tool_info_of(check_status)
    assert info == ToolInfo(name="check_status", description="Returns a canned status.", side_effecting=False, idempotent=None)


def test_side_effecting_tool_decorates_successfully():
    @tool("record_action", "Records that it acted.", side_effecting=True, idempotent=False)
    def record_action(self):
        return "recorded"

    info = tool_info_of(record_action)
    assert info.side_effecting is True
    assert info.idempotent is False


def test_exposed_tools_for_derives_from_actual_methods():
    class Toy:
        @tool("a", "does a", side_effecting=False)
        def a(self):
            return None

        @tool("b", "does b", side_effecting=True, idempotent=True)
        def b(self):
            return None

        def not_a_tool(self):
            return None

    names = {t.name for t in exposed_tools_for(Toy())}
    assert names == {"a", "b"}


def test_a_tool_added_to_the_class_shows_up_without_a_hand_maintained_list():
    class Toy:
        @tool("a", "does a", side_effecting=False)
        def a(self):
            return None

    assert len(exposed_tools_for(Toy())) == 1

    class ToyWithMore(Toy):
        @tool("c", "does c", side_effecting=False)
        def c(self):
            return None

    assert len(exposed_tools_for(ToyWithMore())) == 2
