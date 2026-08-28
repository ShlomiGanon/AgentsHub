"""Permission model (work_plan.md §1.9)."""

from enum import IntEnum


class PermissionLevel(IntEnum):
    VIEWER = 1
    COMMANDER = 2


ACTION_REQUIREMENTS: dict[str, PermissionLevel] = {
    "send_message": PermissionLevel.VIEWER,
    "view_history": PermissionLevel.VIEWER,
    "resolve_hold": PermissionLevel.COMMANDER,
    "approve_run": PermissionLevel.COMMANDER,
    "edit_profile": PermissionLevel.COMMANDER,
    "change_settings": PermissionLevel.COMMANDER,
    "view_commander_roster": PermissionLevel.COMMANDER,
    "poll_notifications": PermissionLevel.COMMANDER,
}


def is_permitted(level: PermissionLevel, action: str) -> bool:
    if action not in ACTION_REQUIREMENTS:
        raise ValueError(f"unknown action: '{action}'")

    return level >= ACTION_REQUIREMENTS[action]
