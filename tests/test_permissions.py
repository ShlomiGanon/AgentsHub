import pytest

from auth.permissions import ACTION_REQUIREMENTS, PermissionLevel, is_permitted


def test_commander_is_greater_than_viewer():
    assert PermissionLevel.COMMANDER > PermissionLevel.VIEWER


def test_viewer_permitted_viewer_level_actions():
    assert is_permitted(PermissionLevel.VIEWER, "send_message")
    assert is_permitted(PermissionLevel.VIEWER, "view_history")


def test_viewer_not_permitted_commander_level_actions():
    for action in ("resolve_hold", "approve_run", "edit_profile", "change_settings"):
        assert not is_permitted(PermissionLevel.VIEWER, action)


def test_commander_permitted_every_action():
    for action in ACTION_REQUIREMENTS:
        assert is_permitted(PermissionLevel.COMMANDER, action)


def test_unknown_action_raises():
    with pytest.raises(ValueError):
        is_permitted(PermissionLevel.COMMANDER, "delete_the_database")
