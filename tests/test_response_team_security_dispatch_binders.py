"""profiles/response_team.py's Phase B declared "agent" step binders (report_security_info,
report_security_incident, dispatch_neighboring_force) and the unit-unavailable commander alert
check -- the task text is a fixed template bound from event fields, never freely authored by
task_formulation (the confirmed root cause of the session's over-dispatch bug), while the
specialist agent still makes its own dispatch/no-dispatch judgment via a real LLM turn."""

from profiles.response_team import (
    _bind_dispatch_neighboring_force,
    _bind_report_security_incident,
    _bind_report_security_info,
    _report_security_incident_unit_unavailable,
)


# -- _bind_report_security_info: structurally cannot dispatch -----------------


def test_report_security_info_binder_produces_zero_steps():
    # No participating agents, no tools, no steps -- protocols/executor.py's execute_steps
    # completes an empty step list instantly with completed=True, zero LLM calls.
    assert _bind_report_security_info({"raw_text": "small fire, already out"}) == ()


# -- _bind_report_security_incident: fixed template, agent still decides -----


def test_report_security_incident_binder_produces_one_agent_step_with_the_dispatch_tool():
    event = {"raw_text": "suspicious van near the fence", "area": "east_orchards", "description": "a white van"}

    (step,) = _bind_report_security_incident(event)

    assert step.kind == "agent"
    assert step.agent_name == "surveillance_agent"
    assert step.allowed_tools == ("dispatch_drone_to_area",)
    assert "suspicious van near the fence" in step.task_text
    assert "east_orchards" in step.task_text
    assert "a white van" in step.task_text


def test_report_security_incident_binder_task_text_lets_the_agent_decline_dispatch():
    # The fixed template must not force a dispatch instruction (the over-dispatch bug's exact
    # root cause) -- it must explicitly allow "no dispatch needed" as a valid outcome.
    (step,) = _bind_report_security_incident({"raw_text": "x", "area": "a", "description": "d"})

    assert "not warranted" in step.task_text
    assert "do not call the tool" in step.task_text


def test_report_security_incident_binder_handles_missing_area_and_description():
    (step,) = _bind_report_security_incident({"raw_text": "x"})

    assert "(unresolved)" in step.task_text
    assert "(none provided)" in step.task_text


# -- _bind_dispatch_neighboring_force -----------------------------------------


def test_dispatch_neighboring_force_binder_produces_one_agent_step_with_the_dispatch_tool():
    event = {"raw_text": "casualty needs an ambulance", "area": "expansion_neighborhood", "description": "gunshot wound"}

    (step,) = _bind_dispatch_neighboring_force(event)

    assert step.kind == "agent"
    assert step.agent_name == "neighboring_forces_agent"
    assert step.allowed_tools == ("dispatch_neighboring_force",)
    assert "casualty needs an ambulance" in step.task_text
    assert "expansion_neighborhood" in step.task_text


# -- _report_security_incident_unit_unavailable -------------------------------


class _FakeSurveillanceStore:
    def __init__(self, cameras):
        self._cameras = cameras

    def list_cameras(self, area=None):
        if area is None:
            return self._cameras
        return [c for c in self._cameras if c["area"] == area]


class _FakeSurveillanceAgent:
    def __init__(self, cameras):
        self.surveillance_store = _FakeSurveillanceStore(cameras)


class _FakeRegistry:
    def __init__(self, surveillance_agent):
        self._surveillance_agent = surveillance_agent

    def get(self, name):
        assert name == "surveillance_agent"
        return self._surveillance_agent


def test_unit_unavailable_check_returns_none_when_dispatch_succeeded():
    registry = _FakeRegistry(_FakeSurveillanceAgent([]))
    step_results = ["Drone dispatched successfully:\n- Mission ID: MSN-ABC"]

    assert _report_security_incident_unit_unavailable({"area": "east_gate"}, step_results, registry) is None


def test_unit_unavailable_check_fires_on_the_known_no_drone_marker_and_lists_cameras():
    registry = _FakeRegistry(_FakeSurveillanceAgent([
        {"camera_id": "CAM-01", "area": "east_gate", "status": "active"},
        {"camera_id": "CAM-02", "area": "east_gate", "status": "degraded"},
    ]))
    step_results = ["Drone dispatch failed: No ready drones available in fleet for immediate dispatch."]

    alert = _report_security_incident_unit_unavailable({"area": "east_gate"}, step_results, registry)

    assert alert is not None
    assert "CAM-01" in alert and "CAM-02" in alert
    assert "ambulance" in alert and "police" in alert and "k9" in alert and "yasam" in alert


def test_unit_unavailable_check_reports_no_cameras_when_none_cover_the_area():
    registry = _FakeRegistry(_FakeSurveillanceAgent([]))
    step_results = ["Drone dispatch failed: No ready drones available in fleet for immediate dispatch."]

    alert = _report_security_incident_unit_unavailable({"area": "old_public_building"}, step_results, registry)

    assert alert is not None
    assert "old_public_building" in alert
