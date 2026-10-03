"""Shared direct-tool binder helpers used by both operational profiles."""

from protocols.binders import as_aware_iso, attendance_tool_kwargs, bind_record_attendance_response


def test_as_aware_iso_normalizes_naive_utc_and_keeps_aware_values():
    """As aware iso normalizes naive utc and keeps aware values."""
    assert as_aware_iso("2026-08-20T10:00:00") == "2026-08-20T10:00:00+00:00"
    assert as_aware_iso("2026-08-20T10:00:00+00:00") == "2026-08-20T10:00:00+00:00"
    assert as_aware_iso("2026-08-20T10:00:00Z") == "2026-08-20T10:00:00+00:00"


def test_attendance_kwargs_available_needs_no_dates():
    """Attendance kwargs available needs no dates."""
    kwargs, required = attendance_tool_kwargs(
        {"absence_reason": None, "source_message_id": "m1", "raw_text": "available", "received_at": "2026-08-20T10:00:00Z"}
    )

    assert required == ()
    assert kwargs["availability"] == "available"
    assert kwargs["unavailable_days"] == 0


def test_bind_record_attendance_response_uses_the_calling_profile_agent_name():
    """Bind record attendance response uses the calling profile agent name."""
    event = {"absence_reason": None, "source_message_id": "m1", "raw_text": "available", "received_at": "2026-08-20T10:00:00Z"}

    (step,) = bind_record_attendance_response(
        event, agent_name="team_status_agent", task_text="Record crew availability."
    )

    assert step.agent_name == "team_status_agent"
    assert step.direct_tool_name == "record_attendance_response"
    assert step.kind == "direct_tool"
