"""Deterministic checks for simulation protocol alignment. No billed model calls."""

import asyncio

from agents.team_status_agent import _aware_datetime
from bot.simulator_transport import SimulatorTelegramClient
from messages import get_catalog
from messages.camera_names import resolve_camera_id
from orchestrator.situational_picture import question_requests_picture, read_picture_directly
from persistence import open_response_team_roster_store
from profiles.contracts import AreaRegistry
import profiles.firefighting  # noqa: F401  -- finish the profile import cycle first
import profiles.response_team  # noqa: F401
from profiles.firefighting_protocols import (
    PROTOCOLS as FIRE_PROTOCOLS,
    _bind_close_contained_fire,
    _bind_correct_false_fire_report,
)
from profiles.response_team import CAMERAS
from profiles.response_team_protocols import (
    PROTOCOLS as RESPONSE_PROTOCOLS,
    _bind_armed_threat,
    _bind_close_security_incident,
    _bind_correct_false_security_report,
    _bind_log_security_observation,
)
from profiles.response_team_agents import ResponseTeamRosterAgent
from profiles.response_team_simulation import SIMULATION_USERS
from api.admin_chrome_pages import _ACTING_IDENTITY_TEMPLATE
from history.event_pipeline import extraction_result_from_payload


class _CameraStore:
    def __init__(self, rows):
        self.rows = rows

    def get_camera(self, camera_id):
        for row in self.rows:
            if row["camera_id"] == camera_id:
                return row
        return None

    def list_cameras(self):
        return list(self.rows)


class _PictureRegistry:
    def get(self, name):
        return self

    def report_team_availability(self):
        return "roster ready"

    def get_surveillance_overview(self):
        return "cameras clear"

    def list_neighboring_force_dispatches(self):
        return "no dispatches"


def _protocol(protocols, name):
    return next(protocol for protocol in protocols if protocol.name == name)


def test_hebrew_area_label_and_containing_phrase_resolve_to_the_id():
    catalog = get_catalog("he")
    label = catalog.text("response_team.area.expansion_neighborhood")
    registry = AreaRegistry(
        areas=("expansion_neighborhood", "west_gate"),
        labels={"expansion_neighborhood": label, "west_gate": catalog.text("response_team.area.west_gate")},
    )

    assert registry.resolve(label) == "expansion_neighborhood"
    assert registry.resolve(f"בכניסה ל{label}") == "expansion_neighborhood"
    assert registry.resolve("expansion_neighborhood") == "expansion_neighborhood"

    class _Types:
        def is_valid(self, value):
            return True

    result = extraction_result_from_payload(
        {"classification": "security_incident", "area": f"בכניסה ל{label}", "entities": [], "description": "MDA"},
        "telegram",
        "2026-10-04T00:00:00+00:00",
        _Types(),
        registry,
    )
    assert result.area == "expansion_neighborhood"

    mda = catalog.text("response_team.simulation.sec001.phase3.step3.text")
    held = extraction_result_from_payload(
        {"classification": "security_incident", "area": None, "entities": [], "description": None},
        "telegram",
        "2026-10-04T00:00:00+00:00",
        _Types(),
        registry,
        raw_text=mda,
    )
    assert held.area == "expansion_neighborhood"

    fire_label = catalog.text("firefighting.area.ornim_street")
    fire_registry = AreaRegistry(
        areas=("ornim_street",),
        labels={"ornim_street": fire_label},
    )
    fire = extraction_result_from_payload(
        {"classification": "fire", "area": None, "entities": []},
        "telegram",
        "2026-10-04T00:00:00+00:00",
        _Types(),
        fire_registry,
        raw_text=catalog.text("firefighting.simulation.fire002.phase3.step3.text"),
    )
    assert fire.area == "ornim_street"


def test_camera_phrases_in_both_languages_resolve_to_the_catalog_id():
    hebrew = get_catalog("he")
    store = _CameraStore([
        {"camera_id": "CAM-01", "name": hebrew.text("response_team.camera.cam_01.name")},
        {"camera_id": "CAM-03", "name": hebrew.text("response_team.camera.cam_03.name")},
    ])

    assert resolve_camera_id(store, "מצלמה 1", "response_team") == "CAM-01"
    assert resolve_camera_id(store, "מצלמה 01", "response_team") == "CAM-01"
    assert resolve_camera_id(store, "CAM-01", "response_team") == "CAM-01"
    assert resolve_camera_id(store, "Camera 1", "response_team") == "CAM-01"
    assert resolve_camera_id(store, "Camera 05", "response_team") == "CAM-03"
    assert store.get_camera("CAM-01")["name"] == hebrew.text("response_team.camera.cam_01.name")
    assert CAMERAS[0]["name"] == hebrew.text("response_team.camera.cam_01.name")
    assert CAMERAS[0]["camera_id"] == "CAM-01"


def test_armed_threat_binds_drone_police_yasam_and_squad():
    steps = _bind_armed_threat({"area": "old_public_building", "description": "suspect on the roof"})
    assert [step.direct_tool_name for step in steps] == [
        "dispatch_drone_to_area",
        "dispatch_neighboring_force",
        "dispatch_neighboring_force",
        "dispatch_squad",
    ]
    assert steps[1].direct_tool_kwargs["kind"] == "police"
    assert steps[2].direct_tool_kwargs["kind"] == "yasam"
    assert steps[3].direct_tool_kwargs["target_area"] == "old_public_building"
    protocol = _protocol(RESPONSE_PROTOCOLS, "respond_armed_threat")
    assert protocol.viewer_reply_key == "response_team.reply.armed_threat"
    assert get_catalog("he").text(protocol.viewer_reply_key)


def test_correction_and_closure_notify_without_asking_about_cameras():
    correction = _bind_correct_false_security_report({"raw_text": "the gunfire report was false"})
    closure = _bind_close_security_incident({"raw_text": "the incident is under control"})
    assert correction[0].direct_tool_name == "post_operational_notice"
    assert closure[0].direct_tool_name == "post_operational_notice"
    assert "camera" not in correction[0].direct_tool_name
    correction_protocol = _protocol(RESPONSE_PROTOCOLS, "correct_false_security_report")
    assert correction_protocol.operational_notice_key == (
        "response_team.notice.false_gunfire"
    )
    assert correction_protocol.retracts_precedent
    assert _protocol(RESPONSE_PROTOCOLS, "close_security_incident").operational_notice_key == (
        "response_team.notice.incident_closed"
    )
    logged = _bind_log_security_observation({"raw_text": "firefighters already handled the small fire"})
    assert logged[0].direct_tool_name == "log_security_observation"


def test_yasam_commander_is_approved_on_an_already_approved_roster(tmp_path):
    persona = next(user for user in SIMULATION_USERS if user.key == "yasam_commander")
    assert persona.pre_approved_rosters == ("team_status",)

    store = open_response_team_roster_store(str(tmp_path / "roster.db"))
    store.register_member("existing", "Existing Member")
    store.approve_roster("commander")
    store.register_member("pending-real", "Pending Real Member")
    store.register_member("9000000000000014", "Yasam")
    store.approve_member("9000000000000014")

    members = {row["telegram_identity"]: row for row in store.list_members(approved_only=False)}
    assert members["9000000000000014"]["approved"]
    assert not members["pending-real"]["approved"]

    agent = ResponseTeamRosterAgent.__new__(ResponseTeamRosterAgent)
    agent.status_store = store
    stored = agent.report_team_movement(area="old_public_building", member_identity="9000000000000014")
    assert "old_public_building" in stored
    updated = {row["telegram_identity"]: row for row in store.list_members(approved_only=False)}
    assert updated["9000000000000014"]["current_area"] == "old_public_building"


def test_contained_fire_and_false_alarm_notify_without_a_camera_check():
    contained = _bind_close_contained_fire({"area": "pine_ridge", "raw_text": "the incident is contained"})
    false_alarm = _bind_correct_false_fire_report({"area": "ornim_street", "raw_text": "false alarm"})
    assert contained[0].direct_tool_name == "record_fire_status"
    assert contained[0].direct_tool_kwargs["status"] == "extinguished"
    assert contained[1].direct_tool_kwargs["notice_key"] == "firefighting.notice.incident_contained"
    assert false_alarm[-1].direct_tool_kwargs["notice_key"] == "firefighting.notice.false_alarm"
    assert _protocol(FIRE_PROTOCOLS, "close_contained_fire").viewer_reply_key
    assert _protocol(FIRE_PROTOCOLS, "correct_false_fire_report").retracts_precedent
    assert "YASAM" not in _protocol(FIRE_PROTOCOLS, "correct_false_fire_report").description


def test_picture_question_reads_tools_without_a_subagent():
    picture_request = get_catalog("he").text("response_team.simulation.sec001.phase1.step9.text")
    assert question_requests_picture(picture_request, "response_team")
    protocol = _protocol(RESPONSE_PROTOCOLS, "query_situational_picture")
    answer = read_picture_directly(protocol, _PictureRegistry())
    assert "roster ready" in answer
    assert "cameras clear" in answer
    assert "no dispatches" in answer


def test_naive_timestamp_is_treated_as_utc():
    parsed = _aware_datetime("2026-10-04T03:20:00")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_identical_failure_reply_is_not_appended_twice():
    async def scenario():
        client = SimulatorTelegramClient()
        mark = client.mark()
        message_id = await client.send_status("chat-1", "thinking")
        await client.edit_status("chat-1", message_id, "the report")
        await client.send_reply("chat-1", "the report", "origin")
        return client.reply_since(mark, "chat-1")

    assert asyncio.run(scenario()) == "the report"


def test_identity_card_splits_profile_selection_from_system_admin():
    template = _ACTING_IDENTITY_TEMPLATE
    assert template.count("<form") == 2
    assert 'type="checkbox"' not in template
    assert "admin.api.identity_save" in template
    assert "admin.api.identity_admin_button" in template
    assert 'name="use_system_admin"' in template
    assert 'class="identity-split"' in template
    assert template.count('class="identity-pane"') == 2
    assert 'class="identity-divider"' in template
