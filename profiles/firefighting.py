"""Firefighting profile: crew status, fire cameras, and mutual-aid dispatch."""

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
# The fire station itself is the natural drone home, matching response_team's warehouse.
FIREFIGHTING_DRONE_HOME = "fire_station"
FIREFIGHTING_SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_surveillance.db")
FIREFIGHTING_CREW_STATUS_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_crew_status.db")
FIREFIGHTING_FORCES_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_forces.db")
FIREFIGHTING_APPARATUS_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_apparatus.db")
FIREFIGHTING_FIRES_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_fires.db")
RESETTABLE_DATABASES = (
    DB_PATH, FIREFIGHTING_SURVEILLANCE_DB_PATH, FIREFIGHTING_CREW_STATUS_DB_PATH, FIREFIGHTING_FORCES_DB_PATH,
    FIREFIGHTING_APPARATUS_DB_PATH, FIREFIGHTING_FIRES_DB_PATH,
)

# Mutual-aid kinds staged in declared areas named by FIRE_002; ambulance has no simulation quote.
FORCE_BASES = {
    "police": "ornim_street",
    "water_tankers": "chemical_plant",
    "aircraft": "pine_ridge",
    "ambulance": "ornim_street",
}
FORCE_POOL_SIZE = 2
FORCE_BUSY_SECONDS = 2 * 60 * 60

# Cameras are create-if-missing only; IDs match how FIRE_002 names camera 02 and 03.
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

# Aerial recon fleet for report_fire_incident / dispatch_drone_to_incident -- home is the
# station, matching FIREFIGHTING_DRONE_HOME. Create-if-missing only, same idiom as CAMERAS.
DRONES = (
    {
        "drone_id": "DRONE-FF-01",
        "callsign": "Lookout-1",
        "model": "Matrice 350 RTK",
        "status": "ready",
        "battery_percent": 100,
        "current_area": FIREFIGHTING_DRONE_HOME,
    },
    {
        "drone_id": "DRONE-FF-02",
        "callsign": "Lookout-2",
        "model": "Matrice 350 RTK",
        "status": "ready",
        "battery_percent": 100,
        "current_area": FIREFIGHTING_DRONE_HOME,
    },
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
    "drone_dispatch": ("area",),
    "surveillance_report": ("entities",),
    "apparatus_movement": ("entities",),
}

EVENT_TYPE_DESCRIPTIONS = {
    "crew_availability": (
        "A firefighting crew member reporting their own shift availability, or a commander "
        "declaring the crew's opening-shift status including named station apparatus."
    ),
    "surveillance_report": (
        "A fire camera or thermal sensor's own operating condition -- heat-alert, lens cleaning, "
        "smoke/glare, thermal confusion, or a frozen feed. Camera identifiers go into entities."
    ),
    "fire_incident": (
        "A first report of an active or escalating fire -- smoke or flame newly detected, spread "
        "into new terrain, or a casualty/trapped person. Not an already-extinguished no-risk brush fire."
    ),
    "mutual_aid_dispatch": (
        "A request to dispatch an external mutual-aid resource -- water tankers, firefighting "
        "aircraft, police, or ambulance -- never this station's own Ashed 3 or Carmel 1 engines."
    ),
    "drone_dispatch": (
        "An explicit request for aerial drone recon of a fire already known or being monitored, "
        "not the first report of a new fire."
    ),
    "historical_query": (
        "A request for a retrospective, end-to-end debrief of an incident already underway or closed."
    ),
    "apparatus_movement": (
        "A report that a named station apparatus (Ashed 3 or Carmel 1) left the station, is en "
        "route, or changed operating status."
    ),
}

# FIRE_002 names Route 444 as a real incident site, so it is a seventh declared area.
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
# Dedicated port for the simulation-mode bot process of this profile.
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

# -- Simulations --------------------------------------------------------------
# FIRE_002 only. Offsets start at 0 inside this profile's reserved-ID block.

from profiles.firefighting_agents import (
    AGENTS,
    RESOURCE_UNAVAILABLE_DESCRIPTION,
    FirefightingCrewStatusAgent,
    FirefightingExternalForcesAgent,
    FirefightingSurveillanceAgent,
    _AREA_LABELS,
    _RESOURCE_KIND_LABELS,
    _describe_resource_unavailable,
    _find_resource_alternatives,
)
from profiles.firefighting_protocols import (
    PROTOCOLS,
    _as_aware_iso,
    _bind_apparatus_movement,
    _bind_dispatch_drone,
    _bind_dispatch_drone_to_incident,
    _bind_dispatch_mutual_aid,
    _bind_log_fire_observation,
    _bind_record_crew_availability,
    _bind_record_crew_shift_status,
    _bind_report_active_fires,
    _bind_report_fire_incident,
    _bind_update_camera_observation,
)
from profiles.firefighting_simulation import (
    OPERATIONAL_SEED,
    SIMULATION_GROUPS,
    SIMULATION_ROSTERS,
    SIMULATION_USERS,
    SIMULATIONS,
)
from profiles.firefighting_admin_tables import ADMIN_TABLES
