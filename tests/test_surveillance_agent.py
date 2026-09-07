from agents import SurveillanceAgent
from agents import runtime as agent_runtime


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
        "get_surveillance_overview",
        "update_camera_observation",
    }
    assert expected_tools.issubset(tool_names)


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
