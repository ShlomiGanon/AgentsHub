"""profiles/response_team.py's direct-tool step binders (Phase A) -- parameters bound
straight from an event's own extracted fields, no model call, used by
protocols.executor.execute_step_with_retry's kind='direct_tool' path."""

from profiles.response_team import (
    _as_aware_iso,
    _bind_dispatch_drone,
    _bind_dispatch_own_squad,
    _bind_record_attendance,
    _bind_report_team_movement,
    _bind_update_camera_status,
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
#
# camera_id is bound deterministically (from the event's own already-extracted `entities`),
# but the resulting status is a judgment call from free text, not a keyword heuristic -- so
# these bind a normal `kind="agent"` step and let the specialist agent decide and call the
# tool itself, instead of a local `_infer_camera_status` pre-deciding it.


def test_update_camera_status_single_camera_binds_a_model_driven_step():
    event = {"entities": ["CAM-03"], "description": "intermittent reception", "raw_text": "CAM-03 is flaky"}

    (step,) = _bind_update_camera_status(event)

    assert step.kind == "agent"
    assert step.direct_tool_kwargs == {}
    assert step.allowed_tools == ("update_camera_status",)
    assert "CAM-03" in step.task_text
    assert "intermittent reception" in step.task_text
    assert "MUST call update_camera_status exactly once" in step.task_text


def test_update_camera_status_multi_camera_produces_one_step_per_camera():
    event = {"entities": ["CAM-01", "CAM-02"], "description": "both down", "raw_text": "both cameras are down"}

    steps = _bind_update_camera_status(event)

    assert {s.step_id for s in steps} == {"1", "2"}
    assert "CAM-01" in steps[0].task_text and "CAM-01" not in steps[1].task_text
    assert "CAM-02" in steps[1].task_text and "CAM-02" not in steps[0].task_text
    assert all("both down" in s.task_text for s in steps)


def test_update_camera_status_missing_fields_raises_no_camera_calls():
    event = {"entities": None, "description": None, "raw_text": "something's wrong with a camera"}

    steps = _bind_update_camera_status(event)

    assert len(steps) == 1
    assert set(steps[0].required_event_fields) == {"entities"}
    assert steps[0].direct_tool_kwargs == {}


# -- _bind_report_team_movement -------------------------------------------------


def test_report_team_movement_with_area():
    # Whether the report also indicates the member is responding to a specific incident (and so
    # should be linked via join_incident_response) is a judgment call from the free-text report,
    # not something a direct_tool binder can decide -- so this is an "agent" step, not
    # direct_tool, once area is known (see profiles/response_team.py's own comment there).
    event = {"area": "east_fence", "description": "heading to east fence"}

    (step,) = _bind_report_team_movement(event)

    assert step.required_event_fields == ()
    assert step.kind == "agent"
    assert "east_fence" in step.task_text
    assert step.allowed_tools == ("report_team_movement", "join_incident_response", "list_incident_responders")


def test_report_team_movement_without_area_requires_it():
    event = {"area": None}

    (step,) = _bind_report_team_movement(event)

    assert step.required_event_fields == ("area",)
    assert step.direct_tool_kwargs == {}


def test_dispatch_drone_binds_area_and_description():
    event = {"area": "east_gate", "description": "suspicious person", "raw_text": "person at east gate"}

    (step,) = _bind_dispatch_drone(event)

    assert step.kind == "direct_tool"
    assert step.direct_tool_name == "dispatch_drone_to_area"
    assert step.direct_tool_kwargs["target_area"] == "east_gate"
    assert step.direct_tool_kwargs["incident_description"] == "suspicious person"
    assert step.direct_tool_kwargs["mission_type"] == "recon"


def test_dispatch_drone_without_area_requires_it():
    event = {"area": None, "raw_text": "something hostile"}

    (step,) = _bind_dispatch_drone(event)

    assert step.required_event_fields == ("area",)
    assert step.direct_tool_kwargs == {}


def test_dispatch_own_squad_binds_unit_count_one():
    event = {"area": "east_orchards", "description": "send our people", "raw_text": "send our squad"}

    (step,) = _bind_dispatch_own_squad(event)

    assert step.kind == "direct_tool"
    assert step.direct_tool_name == "dispatch_squad"
    assert step.direct_tool_kwargs == {
        "target_area": "east_orchards",
        "unit_count": 1,
        "note": "send our people",
    }
