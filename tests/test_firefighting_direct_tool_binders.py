"""profiles/firefighting.py direct-tool binders for crew availability and shift status."""

from profiles.firefighting import (
    _bind_record_crew_availability,
    _bind_record_crew_shift_status,
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
