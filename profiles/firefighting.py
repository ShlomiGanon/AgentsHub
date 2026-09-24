"""Firefighting profile: fire-and-rescue command-and-control for crew status, visual
surveillance (fire cameras/thermal sensors), and mutual-aid dispatch (Profile Split Plan,
docs/Profile_Split_Plan.md)."""

from pathlib import Path

from agents import FriendlyForcesAgent, SurveillanceAgent, TeamStatusAgent, tool
from messages import get_catalog
from persistence import open_team_status_persistence
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol

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
FIREFIGHTING_SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_surveillance.db")
FIREFIGHTING_CREW_STATUS_DB_PATH = str(_PROFILE_DATA_DIR / "firefighting_crew_status.db")
RESETTABLE_DATABASES = (DB_PATH, FIREFIGHTING_SURVEILLANCE_DB_PATH, FIREFIGHTING_CREW_STATUS_DB_PATH)

# The dashboard runs exactly one profile at a time.  Both selectable profiles
# therefore use the deployment's single Telegram bot token; the supervisor
# fully stops the old bot before starting the newly selected profile.
BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []


class FirefightingSurveillanceAgent(SurveillanceAgent):
    """Binds the reusable visual-surveillance specialist to this profile's own DB --
    fire cameras and thermal sensors (docs/Profile_Split_Plan.md section 4.2)."""

    surveillance_db_path = FIREFIGHTING_SURVEILLANCE_DB_PATH


class FirefightingCrewStatusAgent(TeamStatusAgent):
    """Binds the reusable readiness-status specialist to this profile's own DB -- the
    firefighting crew's shift roster and attendance (docs/Profile_Split_Plan.md section 4.2)."""

    status_db_path = FIREFIGHTING_CREW_STATUS_DB_PATH
    timezone_name = "Asia/Jerusalem"
    attendance_check_hour = 8
    response_window_hours = 1


class FirefightingExternalForcesAgent(FriendlyForcesAgent):
    """Extends the reusable friendly-forces dispatch specialist with two fire-service-specific
    mutual-aid tools (docs/Profile_Split_Plan.md section 4.2) -- FIRE_002's phase 3 needs tanker-truck
    and firefighting-aircraft dispatch, actions the base class's four tools (ambulance/police/
    firefighters/military) do not cover. dispatch_police/dispatch_ambulance are inherited unchanged
    for the police-cordon and casualty-adjacent needs elsewhere in the scenario."""

    @tool(
        "dispatch_water_tankers",
        "Records a request to send water-tanker trucks (mutual aid from another station) to a "
        "named location. Side-effecting and not idempotent -- running it twice records two "
        "dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_water_tankers(
        self, location: str, tanker_count: int = 1, source_station: str = "", note: str = ""
    ) -> str:
        record = (
            f"water tanker dispatch requested for '{location}': tanker_count={tanker_count}"
            f"{f', source_station={source_station}' if source_station else ''}{f', note={note}' if note else ''}"
        )
        self.dispatches_recorded.append(record)
        return f"recorded water tanker dispatch request for '{location}'"

    @tool(
        "dispatch_aircraft",
        "Records a request to send firefighting aircraft to a named location. Side-effecting and "
        "not idempotent -- running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_aircraft(
        self, location: str, aircraft_count: int = 1, aircraft_type: str = "firefighting", note: str = ""
    ) -> str:
        record = (
            f"aircraft dispatch requested for '{location}': aircraft_count={aircraft_count}, "
            f"aircraft_type={aircraft_type}{f', note={note}' if note else ''}"
        )
        self.dispatches_recorded.append(record)
        return f"recorded aircraft dispatch request for '{location}'"


AGENTS = [
    AgentSpec(cls=FirefightingSurveillanceAgent, tier="sub"),
    AgentSpec(cls=FirefightingCrewStatusAgent, tier="sub"),
    AgentSpec(cls=FirefightingExternalForcesAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="record_crew_availability_response",
        description=(
            "Applies when a firefighting crew member reports their own availability for a "
            "shift -- e.g. leaving for a medical checkup, returning from one, or any other "
            "reason they will or will not be on duty; does not apply to a commander asking "
            "about the crew's overall roster (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="Confirmation that the crew member's availability response was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="report_crew_status",
        description=(
            "Applies when someone declares or asks about the firefighting crew's shift roster "
            "or engine/vehicle availability -- e.g. an opening-shift headcount, or who is on "
            "duty; does not apply to a single member's own availability report (use "
            "record_crew_availability_response for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output="A roster report covering crew headcount and vehicle/engine availability.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="update_camera_observation",
        description=(
            "Applies when an operator reports a fire camera's or thermal sensor's own operating "
            "condition -- a heat-alert reading, a lens paused for cleaning, a feed blinded by "
            "smoke/glare, or a similar equipment-status observation; does not apply to what a "
            "camera *shows* about an actual fire (use report_fire_incident for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("update_camera_observation",),
        expected_success_output="Confirmation that the camera's observation/status was recorded.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description=(
            "Applies when aerial drone recon is needed to confirm or monitor a reported fire or "
            "threat from the air -- e.g. confirming a smoke sighting, or checking fire proximity "
            "to a hazardous structure; does not apply to a routine camera/sensor status update "
            "with no active fire (use update_camera_observation for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output=(
            "Confirmation of drone dispatch to the reported location, with callsign, ETA, and mission ID."
        ),
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=True,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="report_fire_incident",
        description=(
            "Applies to a report of an active or escalating fire -- smoke or flame first "
            "detected, spread into new terrain (a tree line, a structure, a hazardous-materials "
            "site), or a reported casualty/trapped person; does not apply to a routine, "
            "already-resolved, no-risk report (e.g. a small roadside fire already extinguished "
            "with no risk to structures), and does not apply to a resource-dispatch decision "
            "itself (use dispatch_mutual_aid for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output="Confirmation of drone dispatch to confirm/monitor the reported fire.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_mutual_aid",
        description=(
            "Applies when a report requires dispatching a real external firefighting resource -- "
            "water-tanker trucks, firefighting aircraft, bulldozers/engines, or a police cordon "
            "for evacuation -- to a location; does not apply to a mere report or recon request "
            "with no dispatch decision yet (use report_fire_incident or "
            "overall_situational_picture first for those)."
        ),
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_water_tankers", "dispatch_aircraft", "dispatch_police"),
        expected_success_output="Confirmation that the requested mutual-aid resource(s) were dispatched.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="overall_situational_picture",
        description=(
            "Applies when a commander asks for a combined snapshot spanning both the crew's "
            "roster/vehicle availability and the camera/surveillance picture in one request -- "
            "e.g. 'what's our force and vehicle availability' or 'urgent picture: exact fire "
            "location and crew status'; does not apply when only one of the two domains is "
            "asked about."
        ),
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("get_surveillance_overview", "report_team_availability"),
        expected_success_output="One combined report covering both current camera status and crew availability.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_historical_incidents",
        description=(
            "Applies when a commander asks for a retrospective, end-to-end debrief of an "
            "incident already underway or closed -- a timeline, resource management, which "
            "reports turned out to be false alarms, or guidance for residents returning home; "
            "does not apply to a question about the current, live state of the crew or cameras."
        ),
        participating_agents=("history_agent",),
        approved_tools=(),
        expected_success_output="A faithful, chronological debrief drawn only from recorded events.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
]

EVENT_TYPES = [
    "crew_availability",
    "surveillance_report",
    "fire_incident",
    "mutual_aid_dispatch",
    "drone_dispatch",
    "historical_query",
]

EVENT_TYPE_REQUIRED_FIELDS = {
    "fire_incident": ("area",),
    "mutual_aid_dispatch": ("area",),
}

# Approved (decision 1, Profile Split Plan implementation prompt): exactly these six, derived
# only from the FIRE_002 simulation text -- see docs/Profile_Split_Plan.md section 5.2 for the
# quoted justification per area. No other area may be added.
AREAS = [
    "pine_ridge",
    "quarry_junction",
    "industrial_park",
    "ornim_street",
    "chemical_plant",
    "fire_station",
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
OPTIMIZATION_POLICY = OptimizationPolicy()

# -- Simulations (docs/Profile_Split_Plan.md) --------------------------------
#
# FIRE_002 series only. Migrated from profiles/unified_test.py (which had itself migrated it
# from the legacy fixtures/admin_scenarios/*.json bundled fixtures); offsets renumbered to start
# at 0 within this profile's own reserved-ID block (offsets are only required to be unique per
# profile, not globally -- profiles/simulation.py). Independent roster from Standby Squad's
# SEC_001 -- no shared persona/group keys.

SIMULATION_USERS = [
    SimulationPersona(key="lahav_avi_shift_commander", offset=0, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="omri_firefighter", offset=1, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.omri_firefighter"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="roni_surveillance_operator", offset=2, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator")),
    SimulationPersona(key="kkl_mountains_sector", offset=3, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector")),
    SimulationPersona(key="police_hub_agam", offset=4, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.police_hub_agam")),
    SimulationPersona(key="station_commander", offset=5, permission_level="commander", full_name=_catalog_text("firefighting.simulation.fire002.persona.station_commander")),
    SimulationPersona(key="yuval_ashed3_commander", offset=6, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"), pre_approved_rosters=("team_status",)),
    SimulationPersona(key="citizen_reports_group", offset=7, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.citizen_reports_group")),
    SimulationPersona(key="fire_police_patrol", offset=8, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.fire_police_patrol")),
    SimulationPersona(key="district_fire_commander", offset=9, permission_level="viewer", full_name=_catalog_text("firefighting.simulation.fire002.persona.district_fire_commander")),
]

SIMULATION_GROUPS = [
    SimulationGroup(key="fire_response_team", offset=0, agent_name="team_status_agent", label=_catalog_text("firefighting.simulation.fire002.group.fire_response_team.label")),
    SimulationGroup(key="fire_cameras", offset=1, agent_name="surveillance_agent", label=_catalog_text("firefighting.simulation.fire002.group.fire_cameras.label")),
    SimulationGroup(key="fire_external_forces", offset=2, agent_name="friendly_forces_agent", label=_catalog_text("firefighting.simulation.fire002.group.fire_external_forces.label")),
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

