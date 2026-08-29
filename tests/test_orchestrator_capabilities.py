"""Role-aware capability context (docs/Next_Plan.md §4.3, §4.4, §8, §11)."""

from auth.permissions import PermissionLevel, RequestedOperation, ViewerAllowedAction, is_permitted
from orchestrator.capabilities import CAPABILITY_DESCRIPTORS, build_role_aware_system_context, visible_capabilities


def test_every_descriptor_operation_is_a_real_requested_operation():
    for descriptor in CAPABILITY_DESCRIPTORS:
        assert isinstance(descriptor.operation, RequestedOperation)


def test_visible_capabilities_matches_is_permitted_for_every_descriptor_and_level():
    for level in (PermissionLevel.VIEWER, PermissionLevel.COMMANDER):
        visible_names = {descriptor.name for descriptor in visible_capabilities(level)}
        for descriptor in CAPABILITY_DESCRIPTORS:
            expected = is_permitted(level, descriptor.operation)
            assert (descriptor.name in visible_names) == expected


def test_a_descriptor_whose_operation_is_absent_from_viewer_allowed_action_is_hidden_from_a_viewer():
    # docs/Next_Plan.md §11's "a viewer action is removed from
    # ViewerAllowedAction disappears from both execution and
    # self-description" — proven structurally here: every descriptor built
    # on an operation outside the current ViewerAllowedAction membership is
    # absent from a viewer's self-description (this list) exactly because
    # it is also denied at execution (is_permitted), since both derive from
    # the identical check. Changing ViewerAllowedAction's membership changes
    # both together, with nothing else to keep in sync.
    viewer_allowed_values = {member.value for member in ViewerAllowedAction}
    viewer_visible_names = {descriptor.name for descriptor in visible_capabilities(PermissionLevel.VIEWER)}

    commander_only_descriptors = [d for d in CAPABILITY_DESCRIPTORS if d.operation.value not in viewer_allowed_values]
    assert commander_only_descriptors, "fixture assumption: at least one descriptor must be commander-only"

    for descriptor in commander_only_descriptors:
        assert descriptor.name not in viewer_visible_names
        assert not is_permitted(PermissionLevel.VIEWER, descriptor.operation)
        assert is_permitted(PermissionLevel.COMMANDER, descriptor.operation)


def test_build_role_aware_system_context_capabilities_match_visible_capabilities():
    for level in (PermissionLevel.VIEWER, PermissionLevel.COMMANDER):
        context = build_role_aware_system_context(level, "Test Service", (), _EmptyRegistry(), (), ())
        expected_names = {descriptor.name for descriptor in visible_capabilities(level)}
        assert {entry["name"] for entry in context["capabilities"]} == expected_names


def test_build_role_aware_system_context_omits_protocols_and_sub_agents_for_a_viewer():
    context = build_role_aware_system_context(PermissionLevel.VIEWER, "Test Service", (), _EmptyRegistry(), (), ())

    assert "protocols" not in context
    assert "sub_agents" not in context


def test_build_role_aware_system_context_includes_protocols_and_sub_agents_for_a_commander():
    context = build_role_aware_system_context(PermissionLevel.COMMANDER, "Test Service", (), _EmptyRegistry(), (), ())

    assert "protocols" in context
    assert "sub_agents" in context


class _EmptyRegistry:
    def all(self):
        return []
