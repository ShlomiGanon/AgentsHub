"""Firefighting operational seed data and declared simulations."""

from datetime import datetime, timezone
from pathlib import Path

from agents import Agent, InvocationPolicy, NeighboringForcesAgent, SurveillanceAgent, TeamStatusAgent, failed_tool_result, get_authenticated_request_identity, tool
from messages import get_catalog
from persistence import (
    ApparatusStoreError,
    FireStoreError,
    open_apparatus_store,
    open_fire_store,
    open_incident_responder_store,
    open_response_team_surveillance_store,
    open_team_status_persistence,
)
from profiles.admin_tables import AdminColumn, AdminTable
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol, Step

import profiles.firefighting as _facade
globals().update({name: getattr(_facade, name) for name in dir(_facade) if not name.startswith("__")})

def _ensure_operational_seed_data() -> None:
    """Create-if-missing cameras/apparatus -- never overwrites an existing row, the same
    "create if missing, never touch if present" idiom response_team.py's own seed hook uses.
    Called automatically, on every profile load (live or simulated), by
    `ensure_simulation_entities` via this module's `OPERATIONAL_SEED` attribute."""

    surveillance = open_response_team_surveillance_store(
        FIREFIGHTING_SURVEILLANCE_DB_PATH, home_area=FIREFIGHTING_DRONE_HOME
    )
    for camera in CAMERAS:
        surveillance.ensure_camera(**camera)
    for drone in DRONES:
        surveillance.ensure_drone(**drone)

    apparatus_store = open_apparatus_store(FIREFIGHTING_APPARATUS_DB_PATH)
    for apparatus in APPARATUS:
        apparatus_store.ensure_apparatus(**apparatus)


OPERATIONAL_SEED = _ensure_operational_seed_data
SIMULATION_USERS = [
    SimulationPersona(key="lahav_avi_shift_commander", offset=0, permission_level="commander", full_name=_catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="omri_firefighter", offset=1, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.omri_firefighter"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="roni_surveillance_operator", offset=2, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator")),
    SimulationPersona(key="kkl_mountains_sector", offset=3, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector")),
    SimulationPersona(key="police_hub_agam", offset=4, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.police_hub_agam")),
    SimulationPersona(key="station_commander", offset=5, permission_level="commander", full_name=_catalog_text("firefighting.simulation.fire002.persona.station_commander")),
    SimulationPersona(key="yuval_ashed3_commander", offset=6, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="citizen_reports_group", offset=7, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.citizen_reports_group")),
    SimulationPersona(key="fire_police_patrol", offset=8, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.fire_police_patrol")),
    SimulationPersona(key="district_fire_commander", offset=9, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.district_fire_commander")),
    SimulationPersona(key="firefighter_team_a_4", offset=10, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.firefighter_team_a_4"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="firefighter_team_a_5", offset=11, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.firefighter_team_a_5"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="firefighter_team_a_6", offset=12, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.firefighter_team_a_6"), pre_approved_rosters=("team_status",)),
]

SIMULATION_GROUPS = [
    SimulationGroup(key="fire_response_team", offset=0, agent_name="team_status_agent", label=_catalog_text("firefighting.simulation.fire002.group.fire_response_team.label")),
    SimulationGroup(key="fire_cameras", offset=1, agent_name="surveillance_agent", label=_catalog_text("firefighting.simulation.fire002.group.fire_cameras.label")),
    SimulationGroup(key="fire_external_forces", offset=2, agent_name="neighboring_forces_agent", label=_catalog_text("firefighting.simulation.fire002.group.fire_external_forces.label")),
]

SIMULATION_ROSTERS = [
    SimulationRoster(key="team_status", open=open_team_status_persistence, db_path=FIREFIGHTING_CREW_STATUS_DB_PATH),
]

FIRE002_CHATS = (
    {"key": "fire_response_team", "kind": "message", "label": _catalog_text("firefighting.simulation.fire002.chat.fire_response_team.label"), "telegram_chat_type": "supergroup", "telegram_chat_id": "fire_response_team"},
    {"key": "fire_cameras", "kind": "message", "label": _catalog_text("firefighting.simulation.fire002.chat.fire_cameras.label"), "telegram_chat_type": "supergroup", "telegram_chat_id": "fire_cameras"},
    {"key": "fire_external_forces", "kind": "message", "label": _catalog_text("firefighting.simulation.fire002.chat.fire_external_forces.label"), "telegram_chat_type": "supergroup", "telegram_chat_id": "fire_external_forces"},
    {"key": "fire_commander_dm", "kind": "message", "label": _catalog_text("firefighting.simulation.fire002.chat.fire_commander_dm.label"), "telegram_chat_type": "private"},
)

SIMULATIONS = [
    SimulationScenario(
        key="fire002_phase1",
    title=_catalog_text("firefighting.simulation.fire002.phase1.title"),
    description=_catalog_text("firefighting.simulation.fire002.phase1.description"),
    tags=("fire002", "phase1"),
    raw={
        "scenario": {
            "id": "FIRE_002_PHASE_1",
            "title": _catalog_text("firefighting.simulation.fire002.phase1.title"),
            "description": _catalog_text("firefighting.simulation.fire002.phase1.description"),
            "tags": ["fire002", "phase1"],
        },
        "chats": list(FIRE002_CHATS),
        "steps": [
            {
                "step": 1, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step1.text"),
            },
            {
                "step": 2, "chat": "fire_response_team", "sender_identity": "omri_firefighter",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.omri_firefighter"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step2.text"),
            },
            {
                "step": 3, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step3.text"),
            },
            {
                "step": 4, "chat": "fire_external_forces", "sender_identity": "kkl_mountains_sector",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step4.text"),
            },
            {
                "step": 5, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step5.text"),
            },
            {
                "step": 6, "chat": "fire_external_forces", "sender_identity": "police_hub_agam",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step6.text"),
            },
            {
                "step": 7, "chat": "fire_commander_dm", "sender_identity": "station_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step7.text"),
            },
        ],
    },
    ),

    SimulationScenario(
        key="fire002_phase2",
    title=_catalog_text("firefighting.simulation.fire002.phase2.title"),
    description=_catalog_text("firefighting.simulation.fire002.phase2.description"),
    tags=("fire002", "phase2"),
    raw={
        "scenario": {
            "id": "FIRE_002_PHASE_2",
            "title": _catalog_text("firefighting.simulation.fire002.phase2.title"),
            "description": _catalog_text("firefighting.simulation.fire002.phase2.description"),
            "tags": ["fire002", "phase2"],
        },
        "chats": list(FIRE002_CHATS),
        "steps": [
            {
                "step": 1, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step1.text"),
            },
            {
                "step": 2, "chat": "fire_external_forces", "sender_identity": "police_hub_agam",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step2.text"),
            },
            {
                "step": 3, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step3.text"),
            },
            {
                "step": 4, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step4.text"),
            },
            {
                "step": 5, "chat": "fire_response_team", "sender_identity": "yuval_ashed3_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step5.text"),
            },
            {
                "step": 6, "chat": "fire_external_forces", "sender_identity": "kkl_mountains_sector",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step6.text"),
            },
            {
                "step": 7, "chat": "fire_commander_dm", "sender_identity": "station_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step7.text"),
            },
        ],
    },
    ),

    SimulationScenario(
        key="fire002_phase3",
    title=_catalog_text("firefighting.simulation.fire002.phase3.title"),
    description=_catalog_text("firefighting.simulation.fire002.phase3.description"),
    tags=("fire002", "phase3"),
    raw={
        "scenario": {
            "id": "FIRE_002_PHASE_3",
            "title": _catalog_text("firefighting.simulation.fire002.phase3.title"),
            "description": _catalog_text("firefighting.simulation.fire002.phase3.description"),
            "tags": ["fire002", "phase3"],
        },
        "chats": list(FIRE002_CHATS),
        "steps": [
            {
                "step": 1, "chat": "fire_response_team", "sender_identity": "yuval_ashed3_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step1.text"),
            },
            {
                "step": 2, "chat": "fire_external_forces", "sender_identity": "police_hub_agam",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step2.text"),
            },
            {
                "step": 3, "chat": "fire_response_team", "sender_identity": "citizen_reports_group",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.citizen_reports_group"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step3.text"),
            },
            {
                "step": 4, "chat": "fire_commander_dm", "sender_identity": "station_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step4.text"),
            },
            {
                "step": 5, "chat": "fire_external_forces", "sender_identity": "fire_police_patrol",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.fire_police_patrol"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step5.text"),
            },
            {
                "step": 6, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step6.text"),
            },
            {
                "step": 7, "chat": "fire_external_forces", "sender_identity": "district_fire_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.district_fire_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step7.text"),
            },
            {
                "step": 8, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step8.text"),
            },
            {
                "step": 9, "chat": "fire_commander_dm", "sender_identity": "station_commander",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step9.text"),
            },
        ],
    },
    ),
]


# == Admin-panel tables (docs/Admin_Tables_Plan.md) ==========================
#
# Every write_fn is a thin wrapper around a store method on the same already-shared store
# classes response_team.py uses (persistence/response_team_store.py's
# ResponseTeamSurveillanceStore/NeighboringForceStore, persistence/team_status_store.py's
# SQLiteTeamStatusPersistence) -- proof this mechanism is genuinely shared, not just the same
# shape reimplemented per profile.

