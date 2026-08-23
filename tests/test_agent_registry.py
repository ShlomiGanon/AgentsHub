import pytest

from agents.base import Agent
from agents.registry import DuplicateAgentNameError, build_agent_registry
from agents.tooling import tool


class _AgentA(Agent):
    name = "agent_a"
    role = "role a"
    system_prompt = "prompt a"

    @tool("tool_a", "does a", side_effecting=False)
    def tool_a(self):
        return "a"


class _AgentB(Agent):
    name = "agent_b"
    role = "role b"
    system_prompt = "prompt b"


def test_registry_holds_core_and_profile_agents_together():
    core = {"agent_a": _AgentA(model="m1")}
    registry = build_agent_registry(core, [_AgentB(model="m2")])

    assert {a.name for a in registry.all()} == {"agent_a", "agent_b"}


def test_lookup_by_name():
    registry = build_agent_registry({}, [_AgentA(model="m1")])

    assert registry.get("agent_a").model == "m1"


def test_lookup_of_unknown_name_raises():
    registry = build_agent_registry({}, [])

    with pytest.raises(KeyError):
        registry.get("does_not_exist")


def test_descriptor_for_returns_role_and_tools_together():
    registry = build_agent_registry({}, [_AgentA(model="m1")])

    descriptor = registry.descriptor_for("agent_a")
    assert descriptor.role == "role a"
    assert {t.name for t in descriptor.tools} == {"tool_a"}


def test_duplicate_name_across_core_and_profile_agents_is_rejected():
    core = {"agent_a": _AgentA(model="core-model")}
    with pytest.raises(DuplicateAgentNameError):
        build_agent_registry(core, [_AgentA(model="profile-model")])


def test_registry_registers_nothing_beyond_what_it_was_given():
    # No import-time self-registration: an Agent subclass that exists in
    # the codebase but was never passed in is simply absent.
    registry = build_agent_registry({}, [_AgentA(model="m1")])

    with pytest.raises(KeyError):
        registry.get("agent_b")
