"""Resolve which Telegram groups receive the daily attendance prompt.

Hour and enable live on the group row (`telegram_groups`). The API asks this
module which groups are eligible and which hour opens the cycle; the attendance
agent only opens or reuses the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from orchestrator.group_routing import GroupBinding


@dataclass(frozen=True)
class AttendanceDispatch:
    """Which groups get today's attendance prompt, and the hour that opens the cycle."""

    check_hour: int | None
    target_chat_ids: tuple[str, ...]


def attendance_dispatch(
    bindings: Iterable[GroupBinding],
    agent_name: str,
    *,
    safe_mode: bool = False,
) -> AttendanceDispatch:
    """Pick eligible groups and the hour that opens today's attendance cycle."""
    eligible = [
        binding
        for binding in bindings
        if binding.agent_name == agent_name
        and binding.attendance_check_enabled
        and not (safe_mode and binding.auto_register)
    ]
    if not eligible:
        return AttendanceDispatch(check_hour=None, target_chat_ids=())
    return AttendanceDispatch(
        check_hour=min(int(binding.attendance_check_hour) for binding in eligible),
        target_chat_ids=tuple(binding.chat_id for binding in eligible),
    )
