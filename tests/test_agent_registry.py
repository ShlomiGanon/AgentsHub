"""Agent registry construction and lookup."""

import pytest

from agents.base import Agent
from agents.runtime import DuplicateAgentNameError, build_agent_registry
from agents.runtime import tool


class _AgentA(Agent):
    """AgentA."""
    name = "agent_a"
    role = "role a"
    system_prompt = "prompt a"

    @tool("tool_a", "does a", side_effecting=False)
    def tool_a(self):
        """Tool a."""
        return "a"


class _AgentB(Agent):
    """AgentB."""
    name = "agent_b"
    role = "role b"
    system_prompt = "prompt b"


def test_registry_holds_core_and_profile_agents_together():
    """Registry holds core and profile agents together."""
    core = {"agent_a": _AgentA(model="m1")}
    registry = build_agent_registry(core, [_AgentB(model="m2")])

    assert {a.name for a in registry.all()} == {"agent_a", "agent_b"}


def test_lookup_by_name():
    """Lookup by name."""
    registry = build_agent_registry({}, [_AgentA(model="m1")])

    assert registry.get("agent_a").model == "m1"


def test_lookup_of_unknown_name_raises():
    """Lookup of unknown name raises."""
    registry = build_agent_registry({}, [])

    with pytest.raises(KeyError):
        registry.get("does_not_exist")


def test_descriptor_for_returns_role_and_tools_together():
    """Descriptor for returns role and tools together."""
    registry = build_agent_registry({}, [_AgentA(model="m1")])

    descriptor = registry.descriptor_for("agent_a")
    assert descriptor.role == "role a"
    assert {t.name for t in descriptor.tools} == {"tool_a"}


def test_duplicate_name_across_core_and_profile_agents_is_rejected():
    """Duplicate name across core and profile agents is rejected."""
    core = {"agent_a": _AgentA(model="core-model")}
    with pytest.raises(DuplicateAgentNameError):
        build_agent_registry(core, [_AgentA(model="profile-model")])


def test_registry_registers_nothing_beyond_what_it_was_given():
    # No import-time self-registration: an Agent subclass that exists in
    # the codebase but was never passed in is simply absent.
    """Registry registers nothing beyond what it was given."""
    registry = build_agent_registry({}, [_AgentA(model="m1")])

    with pytest.raises(KeyError):
        registry.get("agent_b")

from agents.results import AgentResult, parse_agent_output


def test_plain_output_is_success():
    """Plain output is success."""
    result = parse_agent_output("Gate 3 is nominal, no smoke detected.")

    assert result == AgentResult(status="success", text="Gate 3 is nominal, no smoke detected.")


def test_unclear_task_json_is_parsed_into_the_status_field():
    """Unclear task json is parsed into the status field."""
    result = parse_agent_output('{"status": "unclear_task", "text": "the task did not say which gate to check"}')

    assert result.status == "unclear_task"
    assert result.text == "the task did not say which gate to check"


def test_unclear_task_json_is_recognized_with_surrounding_whitespace():
    """Unclear task json is recognized with surrounding whitespace."""
    result = parse_agent_output('  \n{"status": "unclear_task", "text": "missing the target location"}\n  ')

    assert result.status == "unclear_task"
    assert result.text == "missing the target location"


def test_plain_text_that_mentions_unclear_task_is_still_success():
    """Plain text that mentions unclear task is still success."""
    result = parse_agent_output('Everything is fine, not an {"status": "unclear_task"} situation.')

    assert result.status == "success"


def test_json_success_payload_is_parsed_into_the_status_field():
    """Json success payload is parsed into the status field."""
    result = parse_agent_output('{"status": "success", "text": "gate 3 is nominal"}')

    assert result == AgentResult(status="success", text="gate 3 is nominal")
