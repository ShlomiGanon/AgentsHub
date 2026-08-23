from agents import base
from agents.reference import ReferenceAgent


def test_constructed_with_a_model_like_any_other_agent():
    agent = ReferenceAgent(model="some-model")

    assert agent.model == "some-model"
    assert agent.name == "reference_agent"


def test_role_and_system_prompt_are_real_text_not_placeholders():
    agent = ReferenceAgent(model="m")

    assert len(agent.role) > 40
    assert len(agent.system_prompt) > 40
    assert "TODO" not in agent.role
    assert "placeholder" not in agent.role.lower()


def test_exposes_exactly_the_two_stub_tools_with_the_right_marks():
    agent = ReferenceAgent(model="m")
    tools = {t.name: t for t in agent.exposed_tools()}

    assert set(tools) == {"check_status", "record_action"}

    assert tools["check_status"].side_effecting is False
    assert tools["check_status"].idempotent is None

    assert tools["record_action"].side_effecting is True
    assert tools["record_action"].idempotent is False


def test_check_status_is_read_only_and_returns_a_canned_status():
    agent = ReferenceAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"check_status"}))
    try:
        result = agent._wrapped_tools["check_status"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "gate-3" in result
    assert agent.actions_taken == []


def test_record_action_genuinely_records_each_call_it_receives():
    # This is what makes "a retry does not repeat an action" testable
    # later (§4.5) — the tool has to actually accumulate state, not just
    # return a canned string, so a second call is observably different
    # from stopping after one.
    agent = ReferenceAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"record_action"}))
    try:
        agent._wrapped_tools["record_action"](location="gate-3", note="dispatched")
        agent._wrapped_tools["record_action"](location="gate-3", note="dispatched")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.actions_taken) == 2  # two calls really did record twice


def test_record_action_is_blocked_when_not_allowed():
    agent = ReferenceAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"check_status"}))  # record_action not allowed
    try:
        result = agent._wrapped_tools["record_action"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.actions_taken == []
