import pytest

from auth.permissions import (
    PermissionLevel,
    RequestedOperation,
    ViewerAllowedAction,
    is_permitted,
)


def test_commander_is_greater_than_viewer():
    assert PermissionLevel.COMMANDER > PermissionLevel.VIEWER


def test_commander_permitted_every_requested_operation():
    for operation in RequestedOperation:
        assert is_permitted(PermissionLevel.COMMANDER, operation)


def test_viewer_permitted_exactly_the_viewer_allowed_action_members():
    viewer_allowed_values = {member.value for member in ViewerAllowedAction}

    for operation in RequestedOperation:
        expected = operation.value in viewer_allowed_values
        assert is_permitted(PermissionLevel.VIEWER, operation) is expected


def test_viewer_allowed_action_members_all_map_to_a_requested_operation():
    known_operation_values = {operation.value for operation in RequestedOperation}
    for member in ViewerAllowedAction:
        assert member.value in known_operation_values


def test_operation_absent_from_viewer_allowed_action_is_commander_only():
    # RequestedOperation.LIST_PROTOCOLS is deliberately absent from the
    # approved initial ViewerAllowedAction member list (docs/Next_Plan.md §5).
    assert RequestedOperation.LIST_PROTOCOLS.value not in {member.value for member in ViewerAllowedAction}
    assert not is_permitted(PermissionLevel.VIEWER, RequestedOperation.LIST_PROTOCOLS)
    assert is_permitted(PermissionLevel.COMMANDER, RequestedOperation.LIST_PROTOCOLS)


def test_unsupported_operation_type_raises():
    with pytest.raises(TypeError):
        is_permitted(PermissionLevel.COMMANDER, 42)

    with pytest.raises(TypeError):
        is_permitted(PermissionLevel.COMMANDER, "send_message")  # legacy strings no longer accepted
