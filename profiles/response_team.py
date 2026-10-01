"""Response Team (SEC) operational profile (docs/responce_improve.md).

One profile = one deployment = one database file = one API port = one
Telegram bot, exactly the isolation mechanism every other profile in this
codebase already uses. SEC_001 (the readiness-squad narrative previously
carried by the now-deleted `profiles/standby_squad.py`) is treated as a real,
live event here: its absence reports, camera faults, security incidents, and
neighboring-force dispatch requests write to this profile's own database
through this profile's own agents/tools, the same way any other real report
would.

Architecture (docs/responce_improve.md's own rules, restated briefly):
  - Single DB: `DB_PATH` below (`data/response_team/response_team_history.db`).
    Roster/attendance, surveillance (cameras/drones), and neighboring-force
    dispatch state all live in *separate tables in that same file* --
    `persistence/response_team_store.py` opens `DB_PATH` itself and runs its
    own `CREATE TABLE IF NOT EXISTS` DDL; none of this is added to the
    shared `persistence/schema.py` (every profile, including Fire and
    Rescue, would otherwise inherit it).
  - The three agents below are profile-owned subclasses of shared bases --
    `TeamStatusAgent`/`SurveillanceAgent`, and, since
    docs/Admin_Tables_Plan.md's extraction, `agents.neighboring_forces_agent
    .NeighboringForcesAgent` too (this profile's own subclass adds only its
    "squad" special case on top). None of their tools are added to the
    shared `agents/surveillance_agent.py`, `agents/team_status_agent.py`, or
    `agents/roster_agent.py` modules, which stay untouched.
  - Profile module text stays English, per the same hard constraint as
    every other profile module in this repo -- Hebrew lives only in
    `messages/he.py`, read here (via `_catalog_text`) only for this
    profile's SEC_001 simulation content, the same narrow exception
    `profiles/standby_squad.py` used to document.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

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

DEFAULT_LANGUAGE = "he"


def _catalog_text(key: str, **values) -> str:
    """Look up one message in this profile's own language -- the one place
    this module reads Hebrew text (SEC_001's simulation content only), so
    tests/test_hebrew_leakage.py's "no Hebrew literal outside the message
    catalog" rule can enforce it. See messages/he.py / messages/en.py for
    the actual wording."""

    return get_catalog(DEFAULT_LANGUAGE).text(key, **values)


PROFILE_NAME = "Response Team"
MAX_ITER = 6
MODEL_TIMEOUT_SECONDS = 45

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "response_team"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "response_team_history.db")
RESETTABLE_DATABASES = (DB_PATH,)

API_PORT = 8907
# Admin-simulator message-kind steps proxy through this port (docs/
# bot_simulation_mode_design.md) -- freed by profiles/standby_squad.py's
# deletion; reused here unchanged since this profile now carries SEC_001.
SIMULATOR_PORT = 8915

BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS: list[str] = []

RETRY_COUNT = 2
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy(
    operational_decision_mode="merged",
    final_assessment_mode="low_risk_merged",
    event_queue_mode="policy",
)


# == Profile declarations (docs/responce_improve.md) ==========================

AREAS = [
    "west_gate",
    "east_gate",
    "east_fence",
    "east_orchards",
    "expansion_neighborhood",
    "old_public_building",
    "south_corner",
    "access_road",
    "drones_warehouse",
]

DRONES_WAREHOUSE = "drones_warehouse"

# Cameras: create-if-missing only (OPERATIONAL_SEED, below) -- an existing
# row is never overwritten. IDs/areas per docs/responce_improve.md's table;
# "Was in SEC_001" column ported into these three camera's own narrative in
# `messages/he.py` / `messages/en.py` (camera 03 -> CAM-01, camera 04 ->
# CAM-02, camera 08 -> CAM-03).
CAMERAS = (
    {
        "camera_id": "CAM-01",
        "name": "East Fence Camera 1",
        "area": "east_fence",
        "status": "active",
        "azimuth_degrees": 90,
        "feed_summary": "Clear view along the east fence line.",
    },
    {
        "camera_id": "CAM-02",
        "name": "East Fence Camera 2",
        "area": "east_fence",
        "status": "active",
        "azimuth_degrees": 110,
        "feed_summary": "Clear view along the east fence line, adjacent segment.",
    },
    {
        "camera_id": "CAM-03",
        "name": "South Corner Camera",
        "area": "south_corner",
        "status": "active",
        "azimuth_degrees": 200,
        "feed_summary": "Clear view of the south corner.",
    },
)

# Drones: home and recall target is DRONES_WAREHOUSE.
DRONES = (
    {
        "drone_id": "DRONE-01",
        "callsign": "Falcon-1",
        "model": "Matrice 350 RTK",
        "status": "ready",
        "battery_percent": 100,
        "current_area": DRONES_WAREHOUSE,
    },
    {
        "drone_id": "DRONE-02",
        "callsign": "Falcon-2",
        "model": "Matrice 350 RTK",
        "status": "ready",
        "battery_percent": 100,
        "current_area": DRONES_WAREHOUSE,
    },
)

# Neighboring force kinds and home bases -- profile constants, not a
# standing-units table (docs/responce_improve.md). No firefighters; YAMAG
# folds into 'yasam'.
FORCE_BASES = {
    "ambulance": "expansion_neighborhood",
    "police": "east_orchards",
    "k9": "east_orchards",
    "yasam": "old_public_building",
}

# Fixed capacity per external force kind -- NeighboringForceStore itself has no standing-units
# table (its own docstring: request/response log only), so this profile enforces a small,
# realistic pool here. Mirrors the drone fleet's own size (2) so the same kind of "resource ran
# out under sustained load" scenario is reproducible for forces, not just drones.
FORCE_POOL_SIZE = 2

# A dispatched unit stays busy for this long after dispatch, regardless of the dispatch's own
# en_route/arrived status -- arriving on scene doesn't free the unit; it's still occupied
# handling the incident. NeighboringForceStore's own en_route->arrived transition (computed
# from ETA alone) answers "has it gotten there yet", a genuinely different question from "is it
# still busy", so capacity here is checked against dispatched_at + this window, not status.
FORCE_BUSY_SECONDS = 2 * 60 * 60

# The response team's own roster, dispatched through the same tool as an external force
# (the resource-unavailable mechanism, orchestrator/flows.py) -- deliberately NOT in
# FORCE_BASES above (it is not a neighboring/external force; NeighboringForcesAgent contacts
# no real squad any more than it contacts a real ambulance). Checked against the roster's own
# live available-member count instead of FORCE_POOL_SIZE.
SQUAD_KIND = "squad"
SQUAD_ORIGIN_AREA = FORCE_BASES["police"]

# ETA matrix (seconds, symmetric; same cell = 45) -- docs/responce_improve.md.
_ETA_AREA_ORDER = (
    "west_gate",
    "east_gate",
    "east_fence",
    "east_orchards",
    "expansion_neighborhood",
    "old_public_building",
    "south_corner",
    "access_road",
    "drones_warehouse",
)

_ETA_MATRIX_SECONDS = (
    (45, 180, 210, 200, 180, 150, 120, 90, 90),
    (180, 45, 60, 90, 90, 100, 150, 120, 90),
    (210, 60, 45, 75, 90, 110, 150, 160, 120),
    (200, 90, 75, 45, 90, 100, 160, 150, 120),
    (180, 90, 90, 90, 45, 60, 140, 130, 100),
    (150, 100, 110, 100, 60, 45, 130, 140, 90),
    (120, 150, 150, 160, 140, 130, 45, 90, 80),
    (90, 120, 160, 150, 130, 140, 90, 45, 100),
    (90, 90, 120, 120, 100, 90, 80, 100, 45),
)

_ETA_INDEX = {area: index for index, area in enumerate(_ETA_AREA_ORDER)}


def eta_seconds(origin_area: str, target_area: str) -> int:
    """Seconds between two of this profile's areas, from the fixed,
    symmetric ETA matrix declared above. Falls back to 180s (the same
    generic default `persistence/surveillance_store.py` already uses for an
    unrecognized area) for an area this profile doesn't declare, rather than
    raising -- a model passing a slightly wrong area name should degrade,
    not crash the tool."""

    origin_index = _ETA_INDEX.get(origin_area)
    target_index = _ETA_INDEX.get(target_area)
    if origin_index is None or target_index is None:
        return 180
    return _ETA_MATRIX_SECONDS[origin_index][target_index]


# == Agents (profile-only; not added to any shared agents/*.py module) =======


EVENT_TYPES = [
    "attendance",
    "camera_status",
    "security_incident",
    "force_dispatch",
    "squad_dispatch",
    "team_movement",
    "situational_query",
    "incident_summary",
]

EVENT_TYPE_DESCRIPTIONS = {
    "attendance": (
        "A team member reporting their own attendance/availability status, with or without a "
        "stated reason or day count."
    ),
    "camera_status": (
        "A camera offline, degraded, back online, or physically damaged, including a physically "
        "cut camera communications cable. Camera identifiers go into entities. The physical "
        "observation (what was seen) belongs in description; any stated cause or suspicion from "
        "the reporter is the reporter's own claim, never recorded as fact."
    ),
    "security_incident": (
        "An unconfirmed hostile, suspicious, or security-relevant event -- a suspicious vehicle "
        "or person, gunfire, a sighted armed suspect, an intrusion, or a fence breach."
    ),
    "force_dispatch": (
        "A request to dispatch a real neighboring/external force (ambulance, police, K9, or "
        "YASAM) to an area -- never this site's own roster."
    ),
    "squad_dispatch": (
        "A request to send this site's own response-team roster, squad, or members to an area."
    ),
    "team_movement": "A team member's own movement or current position while on duty.",
    "situational_query": (
        "A request for a combined, current snapshot of roster/attendance, cameras, and/or "
        "neighboring-force dispatch status."
    ),
    "incident_summary": "A request for a retrospective, end-to-end summary of an incident already underway or closed.",
}

EVENT_TYPE_REQUIRED_FIELDS = {
    "attendance": ("availability_start", "availability_end"),
    "camera_status": ("area", "entities"),
    "security_incident": ("area",),
    "force_dispatch": ("area",),
    "squad_dispatch": ("area",),
    "team_movement": ("area",),
}


# == Profile-load provisioning (docs/responce_improve.md) ====================

from profiles.response_team_agents import (
    AGENTS,
    RESOURCE_UNAVAILABLE_DESCRIPTION,
    NeighboringForcesAgent,
    ResponseTeamRosterAgent,
    ResponseTeamSurveillanceAgent,
    _AREA_LABELS,
    _RESOURCE_KIND_LABELS,
    _describe_resource_unavailable,
    _find_resource_alternatives,
)
from profiles.response_team_protocols import (
    PROTOCOLS,
    _as_aware_iso,
    _bind_dispatch_drone,
    _bind_dispatch_own_squad,
    _bind_record_attendance,
    _bind_report_team_movement,
    _bind_update_camera_status,
)
from profiles.response_team_simulation import (
    OPERATIONAL_SEED,
    SIMULATION_GROUPS,
    SIMULATION_ROSTERS,
    SIMULATION_USERS,
    SIMULATIONS,
)
from profiles.response_team_admin_tables import ADMIN_TABLES
