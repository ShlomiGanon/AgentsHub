"""profiles/response_team.py's direct-tool step binders (Phase A) -- parameters bound
straight from an event's own extracted fields, no model call, used by
protocols.executor.execute_step_with_retry's kind='direct_tool' path."""

from profiles.response_team import (
    _as_aware_iso,
    _bind_record_attendance,
    _bind_report_team_movement,
    _bind_update_camera_status,
    _infer_camera_status,
)


# -- _bind_record_attendance ---------------------------------------------------


def test_record_attendance_available_needs_no_dates():
    event = {"absence_reason": None, "source_message_id": "m1", "raw_text": "available", "received_at": "2026-08-20T10:00:00Z"}

    (step,) = _bind_record_attendance(event)

    assert step.required_event_fields == ()
    assert step.direct_tool_kwargs["availability"] == "available"
    assert step.direct_tool_kwargs["unavailable_days"] == 0
    assert step.kind == "direct_tool"
    assert step.direct_tool_name == "record_attendance_response"


def test_record_attendance_unavailable_computes_day_count_from_the_interval():
    event = {
        "absence_reason": "מילואים",
        "availability_start": "2026-09-27T00:00:00+00:00",
        "availability_end": "2026-09-29T21:00:00+00:00",
        "source_message_id": "m1",
        "raw_text": "unavailable",
        "received_at": "2026-08-20T10:00:00Z",
    }

    (step,) = _bind_record_attendance(event)

    assert step.required_event_fields == ()
    assert step.direct_tool_kwargs["availability"] == "unavailable"
    assert step.direct_tool_kwargs["reason"] == "מילואים"
    assert step.direct_tool_kwargs["unavailable_days"] == 3  # rounds up a partial day


def test_record_attendance_unavailable_without_dates_requires_them():
    event = {"absence_reason": "sick", "source_message_id": "m1", "raw_text": "x", "received_at": "2026-08-20T10:00:00Z"}

    (step,) = _bind_record_attendance(event)

    assert set(step.required_event_fields) == {"availability_start", "availability_end"}
    assert "unavailable_days" not in step.direct_tool_kwargs


def test_record_attendance_normalizes_a_naive_received_at_to_utc():
    # A persisted event's received_at is stored without an explicit offset
    # (agents/team_status_agent.py's own _aware_datetime rejects a naive string outright) --
    # the binder must make it explicit before handing it to the tool.
    event = {"absence_reason": None, "source_message_id": "m1", "raw_text": "x", "received_at": "2026-08-20T10:00:00"}

    (step,) = _bind_record_attendance(event)

    assert step.direct_tool_kwargs["received_at"] == "2026-08-20T10:00:00+00:00"


def test_as_aware_iso_leaves_an_already_aware_timestamp_untouched():
    assert _as_aware_iso("2026-08-20T10:00:00+00:00") == "2026-08-20T10:00:00+00:00"
    assert _as_aware_iso("2026-08-20T10:00:00Z") == "2026-08-20T10:00:00+00:00"


# -- _bind_update_camera_status ------------------------------------------------


def test_update_camera_status_single_camera():
    event = {"entities": ["CAM-03"], "description": "intermittent reception", "raw_text": "CAM-03 is flaky"}

    (step,) = _bind_update_camera_status(event)

    assert step.direct_tool_kwargs == {"camera_id": "CAM-03", "observation": "intermittent reception", "status": "degraded"}


def test_update_camera_status_multi_camera_produces_one_step_per_camera():
    event = {"entities": ["CAM-01", "CAM-02"], "description": "both down", "raw_text": "both cameras are down"}

    steps = _bind_update_camera_status(event)

    assert [s.direct_tool_kwargs["camera_id"] for s in steps] == ["CAM-01", "CAM-02"]
    assert all(s.direct_tool_kwargs["observation"] == "both down" for s in steps)
    assert {s.step_id for s in steps} == {"1", "2"}


def test_update_camera_status_missing_fields_raises_no_camera_calls():
    event = {"entities": None, "description": None, "raw_text": "something's wrong with a camera"}

    steps = _bind_update_camera_status(event)

    assert len(steps) == 1
    assert set(steps[0].required_event_fields) == {"entities", "description"}
    assert steps[0].direct_tool_kwargs == {}


def test_infer_camera_status_detects_damage_and_recovery_and_defaults_to_degraded():
    assert _infer_camera_status("the cable was cut, sabotage suspected") == "offline"
    assert _infer_camera_status("the camera is back online now") == "active"
    assert _infer_camera_status("intermittent reception, cause unknown") == "degraded"


# -- _bind_report_team_movement -------------------------------------------------


def test_report_team_movement_with_area():
    event = {"area": "east_fence"}

    (step,) = _bind_report_team_movement(event)

    assert step.required_event_fields == ()
    assert step.direct_tool_kwargs == {"area": "east_fence"}


def test_report_team_movement_without_area_requires_it():
    event = {"area": None}

    (step,) = _bind_report_team_movement(event)

    assert step.required_event_fields == ("area",)
    assert step.direct_tool_kwargs == {}
