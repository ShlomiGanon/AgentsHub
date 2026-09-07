import pytest

from agents.base import Agent
from agents.runtime import DuplicateAgentNameError, build_agent_registry
from agents.runtime import tool


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

from agents.results import UNCLEAR_TASK_PREFIX, AgentResult, parse_agent_output


def test_plain_output_is_success():
    result = parse_agent_output("Gate 3 is nominal, no smoke detected.")

    assert result == AgentResult(status="success", text="Gate 3 is nominal, no smoke detected.")


def test_unclear_task_sentinel_is_parsed_out():
    result = parse_agent_output(f"{UNCLEAR_TASK_PREFIX} the task did not say which gate to check")

    assert result.status == "unclear_task"
    assert result.text == "the task did not say which gate to check"


def test_sentinel_is_recognized_even_with_surrounding_whitespace():
    result = parse_agent_output(f"  \n{UNCLEAR_TASK_PREFIX} missing the target location\n  ")

    assert result.status == "unclear_task"
    assert result.text == "missing the target location"


def test_sentinel_text_never_leaks_the_raw_prefix_into_success_path():
    # A message that merely mentions the phrase mid-sentence is not the
    # sentinel — only a message *starting* with it is.
    result = parse_agent_output(f"Everything is fine, not an {UNCLEAR_TASK_PREFIX} situation.")

    assert result.status == "success"
