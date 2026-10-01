"""Dual-profile protocol identity: exclusive descriptions, no copied wording, no mixed tools."""

import profiles.firefighting as firefighting
import profiles.response_team as response_team
from auth.permissions import PermissionLevel
from orchestrator.capabilities import CAPABILITY_DESCRIPTORS, visible_capabilities


def test_response_team_owns_squad_dispatch_and_excludes_it_from_neighboring_force():
    names = {protocol.name for protocol in response_team.PROTOCOLS}
    assert "dispatch_own_squad" in names
    neighboring = next(p for p in response_team.PROTOCOLS if p.name == "dispatch_neighboring_force")
    own_squad = next(p for p in response_team.PROTOCOLS if p.name == "dispatch_own_squad")
    incident = next(p for p in response_team.PROTOCOLS if p.name == "report_security_incident")
    camera = next(p for p in response_team.PROTOCOLS if p.name == "update_camera_status")

    assert own_squad.approved_tools == ("dispatch_squad",)
    assert "dispatch_squad" not in neighboring.approved_tools
    assert "dispatch_own_squad" in neighboring.description
    assert "dispatch_neighboring_force" in own_squad.description
    assert "update_camera_status" in incident.description
    assert "CAM-01" in camera.description and "CAM-02" in camera.description
    assert incident.direct_tool_binder is not None
    assert own_squad.direct_tool_binder is not None


def test_firefighting_drone_protocols_are_exclusive_and_not_copied_from_response_team():
    by_name = {protocol.name: protocol for protocol in firefighting.PROTOCOLS}
    fire = by_name["report_fire_incident"]
    drone = by_name["dispatch_drone_to_incident"]
    camera = by_name["update_camera_observation"]
    mutual = by_name["dispatch_mutual_aid"]
    apparatus = by_name["report_apparatus_movement"]

    assert "dispatch_drone_to_incident" in fire.description
    assert "report_fire_incident" in drone.description
    assert "update_camera_observation" in fire.description
    assert "report_fire_incident" in camera.description
    assert "Ashed 3" in mutual.description and "Carmel 1" in mutual.description
    assert "dispatch_mutual_aid" in apparatus.description
    assert fire.direct_tool_binder is not None
    assert drone.direct_tool_binder is not None
    assert camera.direct_tool_binder is not None
    assert mutual.direct_tool_binder is not None

    rt_text = " ".join(protocol.description for protocol in response_team.PROTOCOLS)
    ff_text = " ".join(protocol.description for protocol in firefighting.PROTOCOLS)
    assert "YASAM" in rt_text
    assert "YASAM" not in ff_text
    assert "dispatch_own_squad" in rt_text
    assert "dispatch_own_squad" not in ff_text
    assert "pine ridge" in ff_text.lower() or "pine_ridge" in ff_text
    assert "Ashed 3" not in rt_text


def test_explain_approval_policy_is_visible_to_viewers_and_commanders():
    names = {descriptor.name for descriptor in CAPABILITY_DESCRIPTORS}
    assert "explain_approval_policy" in names
    for level in (PermissionLevel.VIEWER, PermissionLevel.COMMANDER):
        visible_names = {descriptor.name for descriptor in visible_capabilities(level)}
        assert "explain_approval_policy" in visible_names
