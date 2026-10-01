"""profiles/firefighting.py direct-tool binders for crew availability and shift status."""

from profiles.firefighting import (
    _bind_dispatch_drone,
    _bind_dispatch_mutual_aid,
    _bind_record_crew_availability,
    _bind_record_crew_shift_status,
    _bind_update_camera_observation,
)


def test_crew_availability_available_needs_no_dates():
    event = {"absence_reason": None, "source_message_id": "m1", "raw_text": "available", "received_at": "2026-08-20T10:00:00Z"}

    (step,) = _bind_record_crew_availability(event)

    assert step.required_event_fields == ()
    assert step.direct_tool_name == "record_attendance_response"
    assert step.direct_tool_kwargs["availability"] == "available"
    assert step.direct_tool_kwargs["unavailable_days"] == 0
    assert step.kind == "direct_tool"
    assert step.agent_name == "team_status_agent"


def test_crew_availability_unavailable_computes_day_count():
    event = {
        "absence_reason": "medical checkup",
        "availability_start": "2026-09-27T00:00:00+00:00",
        "availability_end": "2026-09-29T21:00:00+00:00",
        "source_message_id": "m1",
        "raw_text": "unavailable",
        "received_at": "2026-08-20T10:00:00Z",
    }

    (step,) = _bind_record_crew_availability(event)

    assert step.direct_tool_kwargs["availability"] == "unavailable"
    assert step.direct_tool_kwargs["reason"] == "medical checkup"
    assert step.direct_tool_kwargs["unavailable_days"] == 3


def test_crew_shift_status_binds_all_members_and_named_apparatus():
    event = {
        "entities": ["Ashed 3", "Carmel 1"],
        "description": "entire crew available; Ashed 3 and Carmel 1 operational",
        "raw_text": "entire crew available",
        "source_message_id": "m1",
        "received_at": "2026-09-09T07:00:00",
    }

    steps = _bind_record_crew_shift_status(event)

    assert steps[0].direct_tool_name == "record_crew_shift_status"
    assert steps[0].direct_tool_kwargs["member_identities"] == "all"
    assert steps[0].direct_tool_kwargs["availability"] == "available"
    assert [step.allowed_tools for step in steps[1:]] == [("update_apparatus_status",), ("update_apparatus_status",)]
    assert "Ashed 3" in steps[1].task_text
    assert "Carmel 1" in steps[2].task_text


def test_crew_shift_status_without_apparatus_is_only_the_shift_tool():
    event = {"raw_text": "all crew available", "received_at": "2026-09-09T07:00:00"}

    (step,) = _bind_record_crew_shift_status(event)

    assert step.direct_tool_name == "record_crew_shift_status"
    assert step.direct_tool_kwargs["member_identities"] == "all"


def test_update_camera_observation_one_step_per_camera():
    event = {"entities": ["CAM-02", "CAM-03"], "description": "lens paused; thermal confusion", "raw_text": "both cameras"}

    steps = _bind_update_camera_observation(event)

    assert {s.step_id for s in steps} == {"1", "2"}
    assert all(s.kind == "agent" for s in steps)
    assert "MUST call update_camera_observation exactly once" in steps[0].task_text
    assert "CAM-02" in steps[0].task_text and "CAM-02" not in steps[1].task_text


def test_update_camera_observation_missing_entities_requires_them():
    event = {"entities": None, "raw_text": "a camera is down"}

    (step,) = _bind_update_camera_observation(event)

    assert step.required_event_fields == ("entities",)


def test_dispatch_drone_binds_pine_ridge():
    event = {"area": "pine_ridge", "description": "smoke first detected", "raw_text": "smoke on the ridge"}

    (step,) = _bind_dispatch_drone(event)

    assert step.direct_tool_name == "dispatch_drone_to_area"
    assert step.direct_tool_kwargs["target_area"] == "pine_ridge"
    assert step.direct_tool_kwargs["incident_description"] == "smoke first detected"


def test_dispatch_mutual_aid_requires_area_then_asks_the_agent_to_call_the_tool():
    missing = _bind_dispatch_mutual_aid({"area": None, "raw_text": "need water tankers"})
    assert missing[0].required_event_fields == ("area",)

    (step,) = _bind_dispatch_mutual_aid({"area": "chemical_plant", "description": "need two water tankers"})
    assert step.kind == "agent"
    assert step.allowed_tools == ("dispatch_neighboring_force",)
    assert "MUST call dispatch_neighboring_force exactly once" in step.task_text
    assert "chemical_plant" in step.task_text
