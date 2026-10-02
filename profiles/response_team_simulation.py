"""Response Team operational seed data and declared simulations."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agents import (
    Agent,
    InvocationPolicy,
    NeighboringForcesAgent as _NeighboringForcesAgentBase,
    SurveillanceAgent,
    TeamStatusAgent,
    failed_tool_result,
    get_authenticated_request_identity,
    tool,
)
from messages import get_catalog
from persistence import (
    SurveillancePersistenceError,
    TeamStatusPersistenceError,
    open_incident_responder_store,
    open_response_team_roster_store,
    open_response_team_surveillance_store,
)
from profiles.admin_tables import AdminColumn, AdminTable
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol, Step

import profiles.response_team as _facade
globals().update({name: getattr(_facade, name) for name in dir(_facade) if not name.startswith("__")})

def _ensure_operational_seed_data() -> None:
    """Create-if-missing cameras/drones; open yesterday's local attendance
    cycle if none exists yet (otherwise SEC_001's absence reports fail the
    daily-cycle rule, and today's due check would already be spent). Never
    overwrites an existing row -- the same "create if missing, never touch
    if present" idiom
    `profiles.simulation_provisioning.ensure_simulation_entities` already
    uses for simulation users/groups (item 3/4 of docs/responce_improve.md's
    provisioning list; items 1/2/5 are already covered by that routine's
    existing simulation-user/roster handling once this profile declares
    `SIMULATION_ROSTERS`/personas with `pre_approved_rosters`, below).

    Called automatically, on every profile load (live or simulated), by
    `ensure_simulation_entities` via this module's `OPERATIONAL_SEED`
    attribute -- see that function's own docstring."""

    surveillance = open_response_team_surveillance_store(DB_PATH, eta_fn=eta_seconds, home_area=DRONES_WAREHOUSE)
    for camera in CAMERAS:
        surveillance.ensure_camera(**camera)
    for drone in DRONES:
        surveillance.ensure_drone(**drone)

    roster = open_response_team_roster_store(DB_PATH)
    if roster.roster_is_approved() and roster.latest_cycle() is None:
        now = datetime.now(timezone.utc)
        yesterday = (now.astimezone(ZoneInfo("Asia/Jerusalem")) - timedelta(days=1)).date().isoformat()
        deadline = now + timedelta(hours=1)
        roster.open_cycle(yesterday, now.isoformat(), deadline.isoformat())


OPERATIONAL_SEED = _ensure_operational_seed_data


# -- Simulations (docs/responce_improve.md) -----------------------------------
#
# SEC_001 series only, moved here unchanged in content from the deleted
# `profiles/standby_squad.py` (migrated in turn from `profiles/unified_test.py`,
# which had itself migrated it from the legacy `fixtures/admin_scenarios/*.json`
# bundled fixtures) -- offsets stay exactly as they were (unique per profile,
# not globally -- profiles/simulation.py). `pre_approved_rosters` stays only
# on the six response-team fighters. Camera numbers in the step text below
# were rewritten to this profile's CAM-01/CAM-02/CAM-03 IDs in
# `messages/he.py` / `messages/en.py`. No `response_team_sim` twin (a
# simulation is just another deployment of this same profile module).

SIMULATION_USERS = [
    SimulationPersona(key="eli_response_team", offset=0, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.eli_response_team"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="yossi_technician", offset=1, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.yossi_technician")),
    SimulationPersona(key="sdemot_security_coordinator", offset=2, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.sdemot_security_coordinator")),
    SimulationPersona(key="danny_response_team", offset=3, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.danny_response_team"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="site_security_officer", offset=4, permission_level="commander", full_name=_catalog_text("response_team.simulation.sec001.persona.site_security_officer")),
    SimulationPersona(key="michael_response_team", offset=5, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.michael_response_team"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="police_duty_officer", offset=6, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.police_duty_officer")),
    SimulationPersona(key="yuval_response_team", offset=7, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.yuval_response_team"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="patrol_unit_40", offset=8, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.patrol_unit_40")),
    SimulationPersona(key="gil_response_team", offset=9, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.gil_response_team"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="resident_avraham", offset=10, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.resident_avraham")),
    SimulationPersona(key="dan_response_team", offset=11, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.dan_response_team"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="mda_dispatch", offset=12, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.mda_dispatch")),
    SimulationPersona(key="police_patrol", offset=13, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.police_patrol")),
    SimulationPersona(key="yasam_commander", offset=14, permission_level="viewer", full_name=_catalog_text("response_team.simulation.sec001.persona.yasam_commander")),
]

SIMULATION_GROUPS = [
    SimulationGroup(key="response_team", offset=0, agent_name="roster_agent", label=_catalog_text("response_team.simulation.response_team_label")),
    SimulationGroup(key="cameras", offset=1, agent_name="surveillance_agent", label=_catalog_text("response_team.simulation.sec001.group.cameras.label")),
    SimulationGroup(key="external_forces", offset=2, agent_name="neighboring_forces_agent", label=_catalog_text("response_team.simulation.sec001.group.external_forces.label")),
]

SIMULATION_ROSTERS = [
    SimulationRoster(key="team_status", open=open_response_team_roster_store, db_path=DB_PATH),
]

SEC001_CHATS = (
    {"key": "response_team", "kind": "message", "label": _catalog_text("response_team.simulation.sec001.chat.response_team.label"), "telegram_chat_type": "supergroup", "telegram_chat_id": "response_team"},
    {"key": "cameras", "kind": "message", "label": _catalog_text("response_team.simulation.sec001.chat.cameras.label"), "telegram_chat_type": "supergroup", "telegram_chat_id": "cameras"},
    {"key": "external_forces", "kind": "message", "label": _catalog_text("response_team.simulation.sec001.chat.external_forces.label"), "telegram_chat_type": "supergroup", "telegram_chat_id": "external_forces"},
    {"key": "commander_dm", "kind": "message", "label": _catalog_text("response_team.simulation.sec001.chat.commander_dm.label"), "telegram_chat_type": "private"},
)

SIMULATIONS = [
    SimulationScenario(
        key="sec001_phase1",
    title=_catalog_text("response_team.simulation.sec001.phase1.title"),
    description=_catalog_text("response_team.simulation.sec001.phase1.description"),
    tags=("sec001", "phase1"),
    raw={
        "scenario": {
            "id": "SEC_001_PHASE_1",
            "title": _catalog_text("response_team.simulation.sec001.phase1.title"),
            "description": _catalog_text("response_team.simulation.sec001.phase1.description"),
            "tags": ["sec001", "phase1"],
        },
        "chats": list(SEC001_CHATS),
        "steps": [
            {
                "step": 1, "chat": "response_team", "sender_identity": "eli_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.eli_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step1.text"),
            },
            {
                "step": 2, "chat": "cameras", "sender_identity": "yossi_technician",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step2.text"),
            },
            {
                "step": 3, "chat": "external_forces", "sender_identity": "sdemot_security_coordinator",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.sdemot_security_coordinator"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step3.text"),
            },
            {
                "step": 4, "chat": "response_team", "sender_identity": "danny_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.danny_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step4.text"),
            },
            {
                "step": 5, "chat": "commander_dm", "sender_identity": "site_security_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step5.text"),
            },
            {
                "step": 6, "chat": "response_team", "sender_identity": "michael_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.michael_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step6.text"),
            },
            {
                "step": 7, "chat": "cameras", "sender_identity": "yossi_technician",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step7.text"),
            },
            {
                "step": 8, "chat": "external_forces", "sender_identity": "sdemot_security_coordinator",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.sdemot_security_coordinator"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step8.text"),
            },
            {
                "step": 9, "chat": "commander_dm", "sender_identity": "site_security_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step9.text"),
            },
        ],
    },
    ),

    SimulationScenario(
        key="sec001_phase2",
    title=_catalog_text("response_team.simulation.sec001.phase2.title"),
    description=_catalog_text("response_team.simulation.sec001.phase2.description"),
    tags=("sec001", "phase2"),
    raw={
        "scenario": {
            "id": "SEC_001_PHASE_2",
            "title": _catalog_text("response_team.simulation.sec001.phase2.title"),
            "description": _catalog_text("response_team.simulation.sec001.phase2.description"),
            "tags": ["sec001", "phase2"],
        },
        "chats": list(SEC001_CHATS),
        "steps": [
            {
                "step": 1, "chat": "cameras", "sender_identity": "yossi_technician",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step1.text"),
            },
            {
                "step": 2, "chat": "external_forces", "sender_identity": "police_duty_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.police_duty_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step2.text"),
            },
            {
                "step": 3, "chat": "response_team", "sender_identity": "yuval_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yuval_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step3.text"),
            },
            {
                "step": 4, "chat": "commander_dm", "sender_identity": "site_security_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step4.text"),
            },
            {
                "step": 5, "chat": "cameras", "sender_identity": "yossi_technician",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step5.text"),
            },
            {
                "step": 6, "chat": "external_forces", "sender_identity": "patrol_unit_40",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.patrol_unit_40"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step6.text"),
            },
            {
                "step": 7, "chat": "commander_dm", "sender_identity": "site_security_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step7.text"),
            },
            {
                "step": 8, "chat": "response_team", "sender_identity": "gil_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.gil_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step8.text"),
            },
            {
                "step": 9, "chat": "response_team", "sender_identity": "gil_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.gil_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step9.text"),
            },
        ],
    },
    ),

    SimulationScenario(
        key="sec001_phase3",
    title=_catalog_text("response_team.simulation.sec001.phase3.title"),
    description=_catalog_text("response_team.simulation.sec001.phase3.description"),
    tags=("sec001", "phase3"),
    raw={
        "scenario": {
            "id": "SEC_001_PHASE_3",
            "title": _catalog_text("response_team.simulation.sec001.phase3.title"),
            "description": _catalog_text("response_team.simulation.sec001.phase3.description"),
            "tags": ["sec001", "phase3"],
        },
        "chats": list(SEC001_CHATS),
        "steps": [
            {
                "step": 1, "chat": "response_team", "sender_identity": "resident_avraham",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.resident_avraham"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step1.text"),
            },
            {
                "step": 2, "chat": "response_team", "sender_identity": "dan_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.dan_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step2.text"),
            },
            {
                "step": 3, "chat": "external_forces", "sender_identity": "mda_dispatch",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.mda_dispatch"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step3.text"),
            },
            {
                "step": 4, "chat": "commander_dm", "sender_identity": "site_security_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step4.text"),
            },
            {
                "step": 5, "chat": "external_forces", "sender_identity": "police_patrol",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.police_patrol"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step5.text"),
            },
            {
                "step": 6, "chat": "response_team", "sender_identity": "gil_response_team",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.gil_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step6.text"),
            },
            {
                "step": 7, "chat": "cameras", "sender_identity": "yossi_technician",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step7.text"),
            },
            {
                "step": 8, "chat": "external_forces", "sender_identity": "yasam_commander",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yasam_commander"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step8.text"),
            },
            {
                "step": 9, "chat": "external_forces", "sender_identity": "yasam_commander",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yasam_commander"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step9.text"),
            },
            {
                "step": 10, "chat": "commander_dm", "sender_identity": "site_security_officer",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step10.text"),
            },
        ],
    },
    ),
]


# == Admin-panel tables (docs/Admin_Tables_Plan.md) ==========================
#
# Every write_fn is a thin wrapper around a store method on this profile's own already-shared
# store classes (persistence/response_team_store.py's ResponseTeamSurveillanceStore/
# NeighboringForceStore, persistence/team_status_store.py's SQLiteTeamStatusPersistence) --
# never a direct SQL statement in this module. Every cascade an edit implies (a drone leaving
# an active mission, an attendance approval stamp) happens inside those store methods, so it's
# identical whether the edit came from the admin panel or (where a live tool exists) a real
# report.

