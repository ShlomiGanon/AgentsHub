from agents import base
from agents.friendly_forces_agent import FriendlyForcesAgent


def test_constructed_with_a_model_like_any_other_agent():
    agent = FriendlyForcesAgent(model="some-model")

    assert agent.model == "some-model"
    assert agent.name == "friendly_forces_agent"


def test_role_and_system_prompt_are_real_text_not_placeholders():
    agent = FriendlyForcesAgent(model="m")

    assert len(agent.role) > 40
    assert len(agent.system_prompt) > 40
    assert "TODO" not in agent.role
    assert "placeholder" not in agent.role.lower()


def test_exposes_exactly_the_four_dispatch_tools_with_the_right_marks():
    agent = FriendlyForcesAgent(model="m")
    tools = {t.name: t for t in agent.exposed_tools()}

    assert set(tools) == {"dispatch_ambulance", "dispatch_police", "dispatch_firefighters", "dispatch_military"}

    for tool_info in tools.values():
        assert tool_info.side_effecting is True
        assert tool_info.idempotent is False


def test_dispatch_ambulance_records_the_request_and_confirms():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_ambulance"}))
    try:
        result = agent._wrapped_tools["dispatch_ambulance"](location="gate-3", patient_count=2, severity="critical", note="two casualties")
    finally:
        base._current_allowed_tools.reset(token)

    assert "gate-3" in result
    assert len(agent.dispatches_recorded) == 1
    assert "patient_count=2" in agent.dispatches_recorded[0]
    assert "severity=critical" in agent.dispatches_recorded[0]
    assert "note=two casualties" in agent.dispatches_recorded[0]


def test_dispatch_ambulance_is_blocked_when_not_allowed():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_police"}))  # dispatch_ambulance not allowed
    try:
        result = agent._wrapped_tools["dispatch_ambulance"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_ambulance_genuinely_records_each_call_it_receives():
    # Mirrors record_action's own rationale (tests/test_reference_agent.py): the tool has to
    # actually accumulate state, not just return a canned string, so a second call is
    # observably different from stopping after one — this is what makes "a retry does not
    # repeat a dispatch" testable later.
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_ambulance"}))
    try:
        agent._wrapped_tools["dispatch_ambulance"](location="gate-3")
        agent._wrapped_tools["dispatch_ambulance"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.dispatches_recorded) == 2  # two calls really did record twice


def test_dispatch_police_records_the_request_and_confirms():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_police"}))
    try:
        result = agent._wrapped_tools["dispatch_police"](location="gate-3", unit_count=3, incident_type="break-in", note="two suspects")
    finally:
        base._current_allowed_tools.reset(token)

    assert "gate-3" in result
    assert len(agent.dispatches_recorded) == 1
    assert "unit_count=3" in agent.dispatches_recorded[0]
    assert "incident_type=break-in" in agent.dispatches_recorded[0]
    assert "note=two suspects" in agent.dispatches_recorded[0]


def test_dispatch_police_is_blocked_when_not_allowed():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_ambulance"}))  # dispatch_police not allowed
    try:
        result = agent._wrapped_tools["dispatch_police"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_police_genuinely_records_each_call_it_receives():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_police"}))
    try:
        agent._wrapped_tools["dispatch_police"](location="gate-3")
        agent._wrapped_tools["dispatch_police"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.dispatches_recorded) == 2


def test_dispatch_firefighters_records_the_request_and_confirms():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_firefighters"}))
    try:
        result = agent._wrapped_tools["dispatch_firefighters"](location="gate-3", truck_count=2, incident_type="structure fire", note="spreading")
    finally:
        base._current_allowed_tools.reset(token)

    assert "gate-3" in result
    assert len(agent.dispatches_recorded) == 1
    assert "truck_count=2" in agent.dispatches_recorded[0]
    assert "incident_type=structure fire" in agent.dispatches_recorded[0]
    assert "note=spreading" in agent.dispatches_recorded[0]


def test_dispatch_firefighters_is_blocked_when_not_allowed():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_ambulance"}))  # dispatch_firefighters not allowed
    try:
        result = agent._wrapped_tools["dispatch_firefighters"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_firefighters_genuinely_records_each_call_it_receives():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_firefighters"}))
    try:
        agent._wrapped_tools["dispatch_firefighters"](location="gate-3")
        agent._wrapped_tools["dispatch_firefighters"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.dispatches_recorded) == 2


def test_dispatch_military_records_the_request_and_confirms():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_military"}))
    try:
        result = agent._wrapped_tools["dispatch_military"](location="gate-3", unit_type="infantry", force_size=12, note="perimeter reinforcement")
    finally:
        base._current_allowed_tools.reset(token)

    assert "gate-3" in result
    assert len(agent.dispatches_recorded) == 1
    assert "force_size=12" in agent.dispatches_recorded[0]
    assert "unit_type=infantry" in agent.dispatches_recorded[0]
    assert "note=perimeter reinforcement" in agent.dispatches_recorded[0]


def test_dispatch_military_is_blocked_when_not_allowed():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_ambulance"}))  # dispatch_military not allowed
    try:
        result = agent._wrapped_tools["dispatch_military"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_military_genuinely_records_each_call_it_receives():
    agent = FriendlyForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_military"}))
    try:
        agent._wrapped_tools["dispatch_military"](location="gate-3")
        agent._wrapped_tools["dispatch_military"](location="gate-3")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.dispatches_recorded) == 2
