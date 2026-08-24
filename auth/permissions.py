"""Permission model (work_plan.md §1.9).

Two levels, ordered so "at least commander" is a meaningful comparison and
a third level can be inserted later without rewriting comparisons. One
table maps each action to its minimum level, and one function — used by
both the API and the bot — answers "does this level permit this action",
never "is this user a commander". A check written the second way is missed
when a third level is added and silently grants or denies the wrong thing.
"""

from enum import IntEnum


class PermissionLevel(IntEnum):
    VIEWER = 1
    COMMANDER = 2


# The minimum level each action requires. Add a level or an action by
# editing this one place.
ACTION_REQUIREMENTS: dict[str, PermissionLevel] = {
    "send_message": PermissionLevel.VIEWER,
    "view_history": PermissionLevel.VIEWER,
    "resolve_hold": PermissionLevel.COMMANDER,
    "approve_run": PermissionLevel.COMMANDER,
    "edit_profile": PermissionLevel.COMMANDER,
    "change_settings": PermissionLevel.COMMANDER,
    # §8.13/§8.12 — the bot's own service identity is the only real caller
    # of either (docs/allowed_calls.md: "bot calls only api"), and both
    # expose commander-shaped information (the full commander roster; every
    # notification kind, including clarification/approval hold detail) —
    # commander level, same as the things they exist to let the bot deliver.
    "view_commander_roster": PermissionLevel.COMMANDER,
    "poll_notifications": PermissionLevel.COMMANDER,
}


def is_permitted(level: PermissionLevel, action: str) -> bool:
    if action not in ACTION_REQUIREMENTS:
        raise ValueError(f"unknown action: '{action}'")

    return level >= ACTION_REQUIREMENTS[action]
