"""Firefighting profile: fire-and-rescue command-and-control for crew status, visual
surveillance (fire cameras/thermal sensors), and mutual-aid dispatch (Profile Split Plan,
docs/Profile_Split_Plan.md)."""

from datetime import datetime, timezone
from pathlib import Path

from agents import Agent, InvocationPolicy, NeighboringForcesAgent, SurveillanceAgent, TeamStatusAgent, failed_tool_result, get_authenticated_request_identity, tool
from messages import get_catalog
from persistence import (
    ApparatusStoreError,
    open_apparatus_store,
    open_incident_responder_store,
    open_response_team_surveillance_store,
    open_team_status_persistence,
)
from profiles.admin_tables import AdminColumn, AdminTable
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol, Step

DEFAULT_LANGUAGE = "he"


def _catalog_text(key: str, **values) -> str:
    """Look up one message in this profile's own language -- the one place this module
    reads user-facing text, so tests/test_hebrew_leakage.py's "no Hebrew literal outside
    the message catalog" rule can enforce it."""

    return get_catalog(DEFAULT_LANGUAGE).text(key, **values)


PROFILE_NAME = "Firefighting"
MAX_ITER = 6
MODEL_TIMEOUT_SECONDS = 45

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "firefighting"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_history.db")
# The fire station itself -- the natural drone home base for this profile, mirroring
# response_team.py's own DRONES_WAREHOUSE (docs/Admin_Tables_Plan.md section 1).
FIREFIGHTING_DRONE_HOME = "fire_station"
FIREFIGHTING_SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_surveillance.db")
FIREFIGHTING_CREW_STATUS_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_crew_status.db")
FIREFIGHTING_FORCES_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_forces.db")
FIREFIGHTING_APPARATUS_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_apparatus.db")
RESETTABLE_DATABASES = (
    DB_PATH, FIREFIGHTING_SURVEILLANCE_DB_PATH, FIREFIGHTING_CREW_STATUS_DB_PATH, FIREFIGHTING_FORCES_DB_PATH,
    FIREFIGHTING_APPARATUS_DB_PATH,
)

# Mutual-aid force kinds and their home/staging area -- one of this profile's own 6 declared
# AREAS each, chosen from where each kind is actually mentioned in the FIRE_002 simulation text
# (docs/Admin_Tables_Plan.md section 3.1; see that doc for the exact quotes/citations and the
# explicit caveat that `ambulance`'s value below has no supporting simulation text at all).
FORCE_BASES = {
    "police": "ornim_street",
    "water_tankers": "chemical_plant",
    "aircraft": "pine_ridge",
    "ambulance": "ornim_street",
}
FORCE_POOL_SIZE = 2

# Cameras: create-if-missing only (OPERATIONAL_SEED, below), mirroring
# profiles/response_team.py's own CAMERAS/_ensure_operational_seed_data pattern exactly
# (docs/Admin_Tables_Plan.md's own simulation-alignment audit). IDs/areas match exactly how
# the FIRE_002 simulation text names them: "camera 02 (quarry junction)" (messages/he.py's
# fire002.phase1.step5.text) and "camera 03 (pine ridge)" (fire002.phase2.step1/step4.text).
CAMERAS = (
    {
        "camera_id": "CAM-02",
        "name": "Quarry Junction Camera",
        "area": "quarry_junction",
        "status": "active",
        "azimuth_degrees": 45,
        "feed_summary": "Clear view of the quarry junction approach.",
    },
    {
        "camera_id": "CAM-03",
        "name": "Pine Ridge Camera",
        "area": "pine_ridge",
        "status": "active",
        "azimuth_degrees": 0,
        "feed_summary": "Wide-angle overlook of the pine ridge tree line.",
    },
)


# Apparatus (engines/vehicles): create-if-missing only, same idiom as CAMERAS above. Named
# exactly as FIRE_002's own text names them: "Ashed 3 and Carmel 1 are operational, functional,
# and available at the station for assignment" (fire002.phase1.step1.text) -- both operational,
# at fire_station. persistence/apparatus_store.py is deliberately minimal (registry + status/
# area update only, no dispatch-log/capacity modeling like drones or neighboring forces).
APPARATUS = (
    {"apparatus_id": "APP-ASHED-3", "callsign": "Ashed 3", "status": "operational", "current_area": "fire_station"},
    {"apparatus_id": "APP-CARMEL-1", "callsign": "Carmel 1", "status": "operational", "current_area": "fire_station"},
)


# The dashboard runs exactly one profile at a time.  Both selectable profiles
# therefore use the deployment's single Telegram bot token; the supervisor
# fully stops the old bot before starting the newly selected profile.
BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []


EVENT_TYPES = [
    "crew_availability",
    "surveillance_report",
    "fire_incident",
    "mutual_aid_dispatch",
    "drone_dispatch",
    "historical_query",
    "apparatus_movement",
]

EVENT_TYPE_REQUIRED_FIELDS = {
    "fire_incident": ("area",),
    "mutual_aid_dispatch": ("area",),
}

# Originally approved (decision 1, Profile Split Plan implementation prompt) as exactly six,
# derived only from the FIRE_002 simulation text -- see docs/Profile_Split_Plan.md section 5.2
# for the quoted justification per area. Extended with a seventh, `route_444`, during this
# session's own simulation-data-alignment audit (docs/Admin_Tables_Plan.md): FIRE_002's own
# text names Route 444 twice as a real incident site (a small brush fire beside the highway,
# phase1.step6; thick smoke visible from the highway, phase2.step2), not narrative color, so
# the original "no other area may be added" constraint no longer matches what the profile's own
# simulation content actually requires -- confirmed with the user before overriding it.
AREAS = [
    "pine_ridge",
    "quarry_junction",
    "industrial_park",
    "ornim_street",
    "chemical_plant",
    "fire_station",
    "route_444",
]

API_PORT = 8906
# The simulation-mode bot process (docs/bot_simulation_mode_design.md) -- mirrors
# profiles/standby_squad.py's own SIMULATOR_PORT declaration exactly.
SIMULATOR_PORT = 8916
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

# -- Simulations (docs/Profile_Split_Plan.md) --------------------------------
#
# FIRE_002 series only. Migrated from profiles/unified_test.py (which had itself migrated it
# from the legacy fixtures/admin_scenarios/*.json bundled fixtures); offsets renumbered to start
# at 0 within this profile's own reserved-ID block (offsets are only required to be unique per
# profile, not globally -- profiles/simulation.py). Independent roster from Standby Squad's
# SEC_001 -- no shared persona/group keys.

from profiles.firefighting_agents import (
    AGENTS,
    FirefightingCrewStatusAgent,
    FirefightingExternalForcesAgent,
    FirefightingSurveillanceAgent,
)
from profiles.firefighting_protocols import PROTOCOLS
from profiles.firefighting_simulation import (
    OPERATIONAL_SEED,
    SIMULATION_GROUPS,
    SIMULATION_ROSTERS,
    SIMULATION_USERS,
    SIMULATIONS,
)
from profiles.firefighting_admin_tables import ADMIN_TABLES
