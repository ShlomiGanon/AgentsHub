from agents import SurveillanceAgent
from agents import runtime as agent_runtime
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor


class _TestSurveillanceAgent(SurveillanceAgent):
    surveillance_db_path = ""


def _agent(tmp_path):
    _TestSurveillanceAgent.surveillance_db_path = str(tmp_path / "surveillance.db")
    return _TestSurveillanceAgent(model="test-model")


def _call_tool(agent, name, **kwargs):
    token = agent_runtime._current_allowed_tools.set(frozenset({name}))
    try:
        return agent._wrapped_tools[name](**kwargs)
    finally:
        agent_runtime._current_allowed_tools.reset(token)


def test_surveillance_agent_descriptor_and_tools_exposed(tmp_path):
    agent = _agent(tmp_path)
    assert agent.name == "surveillance_agent"
    assert "visual surveillance" in agent.role.lower()

    tool_names = set(agent._wrapped_tools.keys())
    expected_tools = {
        "get_camera_feeds",
        "get_drone_fleet_status",
        "dispatch_drone_to_area",
        "get_active_missions",
        "return_drone_to_base",
        "get_surveillance_overview",
        "update_camera_observation",
    }
    assert expected_tools.issubset(tool_names)

    dispatch_info = next(tool for tool in agent.exposed_tools() if tool.name == "dispatch_drone_to_area")
    assert "specific_drone_id is optional" in dispatch_info.description
    assert "Never ask for a drone ID" in agent.system_prompt
    assert "Use at most 6 lines total" in agent.system_prompt
    assert "Never use Markdown tables" in agent.system_prompt


def test_get_camera_feeds_tool(tmp_path):
    agent = _agent(tmp_path)

    # All cameras
    feed_output = _call_tool(agent, "get_camera_feeds")
    assert "Camera feeds" in feed_output
    assert "CAM-01" in feed_output
    assert "North Perimeter Gate" in feed_output

    # Specific area filter
    north_output = _call_tool(agent, "get_camera_feeds", area="north_gate")
    assert "CAM-01" in north_output

    # Specific camera ID
    cam_output = _call_tool(agent, "get_camera_feeds", camera_id="CAM-02")
    assert "CAM-02" in cam_output
    assert "Thermal sweep" in cam_output

    # Unknown camera
    missing_output = _call_tool(agent, "get_camera_feeds", camera_id="CAM-UNKNOWN")
    assert "not found" in missing_output


def test_get_drone_fleet_status_tool(tmp_path):
    agent = _agent(tmp_path)

    output = _call_tool(agent, "get_drone_fleet_status")
    assert "Drone Fleet Status" in output
    assert "Eagle-1" in output
    assert "Falcon-2" in output
    assert "READY" in output

    ready_only = _call_tool(agent, "get_drone_fleet_status", status_filter="ready")
    assert "Eagle-1" in ready_only


def test_dispatch_drone_to_area_tool_and_active_missions(tmp_path):
    agent = _agent(tmp_path)

    # Missing target area or incident description requires clarification
    err1 = _call_tool(agent, "dispatch_drone_to_area", target_area="", incident_description="test")
    assert "Clarification required" in err1

    err2 = _call_tool(agent, "dispatch_drone_to_area", target_area="north_gate", incident_description="")
    assert "Clarification required" in err2

    # Successful dispatch
    dispatch_result = _call_tool(
        agent,
        "dispatch_drone_to_area",
        target_area="north_gate",
        incident_description="Thermal anomaly detected along fence",
        mission_type="recon",
        dispatched_by="commander_sarah",
    )
    assert "Drone dispatched successfully" in dispatch_result
    assert "Mission ID: MSN-" in dispatch_result
    assert "Estimated Arrival (ETA):" in dispatch_result
    assert "north_gate" in dispatch_result

    # Check active missions tool
    active_output = _call_tool(agent, "get_active_missions")
    assert "Active Drone Missions" in active_output
    assert "MSN-" in active_output
    assert "north_gate" in active_output


def test_update_camera_observation_and_overview(tmp_path):
    agent = _agent(tmp_path)

    update_result = _call_tool(
        agent,
        "update_camera_observation",
        camera_id="CAM-03",
        new_observation="Fence vibration sensor triggered at sector E-4.",
    )
    assert "successfully updated" in update_result

    overview = _call_tool(agent, "get_surveillance_overview", area="east_fence")
    assert "Tactical Surveillance Overview" in overview
    assert "Fence vibration sensor" in overview


def test_return_single_active_drone_without_identifier(tmp_path):
    agent = _agent(tmp_path)
    _call_tool(
        agent,
        "dispatch_drone_to_area",
        target_area="north_gate",
        incident_description="Check fence movement",
    )
    [active_before_return] = agent.surveillance_store.get_active_missions()

    result = _call_tool(agent, "return_drone_to_base")

    assert "Drone returned to base successfully" in result
    assert active_before_return["callsign"] in result
    assert agent.surveillance_store.get_active_missions() == []
    assert agent.surveillance_store.get_drone(active_before_return["drone_id"])["status"] == "ready"


def test_return_requires_selection_when_multiple_drones_are_active(tmp_path):
    agent = _agent(tmp_path)
    first = _call_tool(
        agent,
        "dispatch_drone_to_area",
        target_area="north_gate",
        incident_description="First mission",
    )
    second = _call_tool(
        agent,
        "dispatch_drone_to_area",
        target_area="south_sector",
        incident_description="Second mission",
    )
    assert "Drone dispatched successfully" in first
    assert "Drone dispatched successfully" in second

    choices = _call_tool(agent, "return_drone_to_base")
    assert choices.startswith("DRONE_SELECTION_REQUIRED:")
    assert "Multiple drones" in choices
    assert "Eagle-1" in choices
    assert "Falcon-2" in choices
    assert "No drone state was changed" in choices
    active_before_selection = agent.surveillance_store.get_active_missions()
    assert len(active_before_selection) == 2

    selected = active_before_selection[1]
    returned = _call_tool(agent, "return_drone_to_base", drone_or_mission_id=selected["callsign"])
    assert selected["callsign"] in returned
    remaining = agent.surveillance_store.get_active_missions()
    assert len(remaining) == 1
    assert remaining[0]["drone_id"] != selected["drone_id"]


def test_return_all_recalls_every_active_drone_atomically(tmp_path):
    agent = _agent(tmp_path)
    _call_tool(agent, "dispatch_drone_to_area", target_area="north_gate", incident_description="First mission")
    _call_tool(agent, "dispatch_drone_to_area", target_area="south_sector", incident_description="Second mission")

    result = _call_tool(agent, "return_drone_to_base", drone_or_mission_id="כולם")

    assert "All active drones returned to base" in result
    assert "Eagle-1" in result
    assert "Falcon-2" in result
    assert agent.surveillance_store.get_active_missions() == []
    drones = {drone["callsign"]: drone for drone in agent.surveillance_store.list_drones()}
    assert drones["Eagle-1"]["status"] == "ready"
    assert drones["Falcon-2"]["status"] == "ready"


def test_process_preserves_exact_recall_selection_across_tool_thread(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    _call_tool(agent, "dispatch_drone_to_area", target_area="north_gate", incident_description="First")
    _call_tool(agent, "dispatch_drone_to_area", target_area="south_sector", incident_description="Second")

    def fake_invoke(descriptor, wrapped_tools, text, timeout_seconds, invocation_policy=None):
        with ThreadPoolExecutor(max_workers=1) as executor:
            tool_output = executor.submit(copy_context().run, wrapped_tools["return_drone_to_base"]).result()
        assert tool_output.startswith("DRONE_SELECTION_REQUIRED:")
        return "model rewrote and hid the selection marker"

    monkeypatch.setattr(agent_runtime, "invoke", fake_invoke)
    result = agent.process("return the drone", ["return_drone_to_base"])

    assert result.text.startswith("DRONE_SELECTION_REQUIRED:")
    assert "Eagle-1" in result.text
    assert "Falcon-2" in result.text
    assert len(agent.surveillance_store.get_active_missions()) == 2


def test_surveillance_process_applies_a_small_default_output_budget(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    captured = {}

    def fake_invoke(descriptor, wrapped_tools, text, timeout_seconds, invocation_policy=None):
        captured["policy"] = invocation_policy
        return "concise"

    monkeypatch.setattr(agent_runtime, "invoke", fake_invoke)
    result = agent.process("מה מצב הרחפנים?", ["get_drone_fleet_status"])

    assert result.text == "concise"
    assert captured["policy"].max_output_tokens == 220
    assert captured["policy"].reasoning_effort == "none"
