"""Attendance check scheduling."""

from orchestrator.attendance_schedule import attendance_dispatch
from orchestrator.group_routing import GroupBinding


def test_dispatch_uses_enabled_attendance_groups_and_the_earliest_hour():
    """Dispatch uses enabled attendance groups and the earliest hour."""
    bindings = (
        GroupBinding("-1", "team_status_agent", attendance_check_enabled=True, attendance_check_hour=10),
        GroupBinding("-2", "team_status_agent", attendance_check_enabled=True, attendance_check_hour=8),
        GroupBinding("-3", "team_status_agent", attendance_check_enabled=False, attendance_check_hour=7),
        GroupBinding("-4", "surveillance_agent", attendance_check_enabled=True, attendance_check_hour=6),
    )

    dispatch = attendance_dispatch(bindings, "team_status_agent")

    assert dispatch.check_hour == 8
    assert dispatch.target_chat_ids == ("-1", "-2")


def test_dispatch_is_empty_when_no_enabled_attendance_group_exists():
    """Dispatch is empty when no enabled attendance group exists."""
    bindings = (
        GroupBinding("-1", "team_status_agent", attendance_check_enabled=False, attendance_check_hour=8),
        GroupBinding("-2", "surveillance_agent", attendance_check_enabled=True, attendance_check_hour=8),
    )

    dispatch = attendance_dispatch(bindings, "team_status_agent")

    assert dispatch.check_hour is None
    assert dispatch.target_chat_ids == ()


def test_dispatch_skips_auto_registered_groups_in_safe_mode():
    """Dispatch skips auto registered groups in safe mode."""
    bindings = (
        GroupBinding("-1", "team_status_agent", auto_register=True, attendance_check_enabled=True, attendance_check_hour=8),
        GroupBinding("-2", "team_status_agent", auto_register=False, attendance_check_enabled=True, attendance_check_hour=9),
    )

    dispatch = attendance_dispatch(bindings, "team_status_agent", safe_mode=True)

    assert dispatch.check_hour == 9
    assert dispatch.target_chat_ids == ("-2",)
