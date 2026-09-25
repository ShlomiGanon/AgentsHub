"""Firefighting profile: fire-and-rescue command-and-control for crew status, visual
surveillance (fire cameras/thermal sensors), and mutual-aid dispatch (Profile Split Plan,
docs/Profile_Split_Plan.md)."""

from datetime import datetime, timezone, timedelta
from pathlib import Path

from agents import FriendlyForcesAgent, SurveillanceAgent, TeamStatusAgent, get_authenticated_request_identity, tool
from messages import get_catalog
from persistence import open_persistence, open_surveillance_persistence, open_team_status_persistence
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
OPTIMIZATION_POLICY = OptimizationPolicy(
    auto_approve_simulations=True,
    fast_simple_reports=True,
)


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

    @tool(
        "report_team_availability",
        "Returns the FIRE readiness roster and the persisted Ashed 3/Carmel 1 vehicle status.",
        side_effecting=False,
    )
    def report_team_availability(self, as_of_iso: str = "") -> str:
        report = super().report_team_availability(as_of_iso)
        vehicles = self.status_store.list_vehicles()
        if not vehicles:
            return report
        vehicle_lines = [
            "",
            "Vehicle status:",
            *(
                f"- {vehicle['display_name']}: {vehicle['status']}; location: {vehicle['current_location']}"
                for vehicle in vehicles
            ),
        ]
        return report + "\n" + "\n".join(vehicle_lines)

    @tool(
        "record_crew_shift_status",
        "Records a commander-confirmed availability declaration for multiple approved crew members. "
        "Use member_identities='all' only when the source explicitly says the entire approved crew "
        "has the same status; otherwise pass comma-separated approved member identities or full names. "
        "This is for a shift declaration, not a single member's own response.",
        side_effecting=True,
        idempotent=True,
    )
    def record_crew_shift_status(
        self,
        member_identities: str = "",
        availability: str = "",
        source_message_id: str = "",
        original_text: str = "",
        received_at: str = "",
    ) -> str:
        if not get_authenticated_request_identity():
            return "The crew shift status was not stored: authenticated requester identity is unavailable."

        normalized = availability.strip().lower()
        if normalized not in {"available", "unavailable"}:
            return "Clarification required: specify whether the crew is available or unavailable."
        if normalized != "available":
            return "Clarification required: bulk shift recording currently supports an explicit available declaration only."

        approved_members = self.status_store.list_members(approved_only=True)
        if not approved_members:
            return "The crew shift status was not stored: the approved roster is empty."

        requested = member_identities.strip()
        all_tokens = {"all", "everyone", "entire crew", "all crew"}
        if requested.casefold() in {token.casefold() for token in all_tokens}:
            selected_members = approved_members
        else:
            tokens = [token.strip() for token in requested.replace(";", ",").split(",") if token.strip()]
            by_identity = {member["telegram_identity"].casefold(): member for member in approved_members}
            by_name = {member["full_name"].casefold(): member for member in approved_members}
            selected_members = []
            unknown = []
            for token in tokens:
                token_clean = token.casefold()
                member = by_identity.get(token_clean) or by_name.get(token_clean)
                if not member:
                    for name, m in by_name.items():
                        if token_clean in name or name in token_clean:
                            member = m
                            break
                if member is None:
                    unknown.append(token)
                elif member not in selected_members:
                    selected_members.append(member)
            if unknown:
                return f"The crew shift status was not stored: unknown approved member(s): {', '.join(unknown)}."
            if not selected_members:
                return "Clarification required: specify which approved crew members are included."

        now_iso = received_at.strip() or datetime.now(timezone.utc).isoformat()
        source_base = source_message_id.strip() or f"crew-shift-{int(datetime.now(timezone.utc).timestamp())}"
        text = original_text.strip() or f"crew shift status: {normalized}"
        stored = 0
        try:
            # Ensure an active cycle exists for this shift
            active_cycle = self.status_store.latest_cycle()
            if not active_cycle:
                self.status_store.open_cycle(
                    cycle_key=f"shift-{now_iso.split('T')[0]}",
                    opened_at=now_iso,
                    deadline_at=(datetime.fromisoformat(now_iso) + timedelta(hours=12)).isoformat(),
                )
                
            for member in selected_members:
                self.status_store.record_response(
                    telegram_identity=member["telegram_identity"],
                    source_message_id=f"{source_base}:{member['telegram_identity']}",
                    availability=normalized,
                    original_text=text,
                    received_at=now_iso,
                )
                stored += 1
            for vehicle_id, display_name in (("ASHED-3", "Ashed 3"), ("CARMEL-1", "Carmel 1")):
                self.status_store.register_vehicle(
                    vehicle_id, display_name, status="available", current_location="fire_station", last_updated=now_iso
                )
        except Exception as exc:
            return f"The crew shift status was not stored: {exc}"
        return f"Crew shift availability recorded for {stored} approved member(s)."


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
        name="record_crew_shift_status",
        description=(
            "Applies when a commander explicitly declares the availability of multiple approved "
            "firefighting crew members for a shift -- for example, that the entire crew is available "
            "at opening; use record_crew_shift_status to persist that declaration. It does not apply "
            "to a request for a read-only roster picture (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_crew_shift_status",),
        expected_success_output="Confirmation that the declared crew shift availability was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=True,
    ),
    Protocol(
        name="report_crew_status",
        description=(
            "Applies when someone asks for a read-only picture of the firefighting crew's shift "
            "roster or engine/vehicle availability -- e.g. who is currently on duty; does not "
            "apply to a commander declaring multiple members' availability (use "
            "record_crew_shift_status for that), and does not apply to a single member's own "
            "availability report (use "
            "record_crew_availability_response for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output="A read-only roster report covering crew headcount and availability.",
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
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
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
        approved_tools=(),
        expected_success_output="Confirmation that the fire report was recorded and linked to the active incident.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="record_incident_update",
        description=(
            "Applies to passive informational updates or requests from external forces (e.g. Police reporting "
            "road closures, KKL reporting forest patrols, citizen reports that don't require immediate dispatch). "
            "Does not apply to an initial report of a new active fire (use report_fire_incident for that), "
            "and does not apply if an active resource dispatch is required (use dispatch_mutual_aid for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=(),
        expected_success_output="Confirmation that the incident update was recorded in the operational log.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
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
    # A road or landmark is sufficient for the FIRE demo; do not block a
    # report waiting for coordinates that the scenario never supplied.
}

EVENT_TYPE_DESCRIPTIONS = {
    "crew_availability": "crew availability, planned absence, or an operational staffing update",
    "surveillance_report": "camera, heat, smoke, drone, or other visual observation",
    "fire_incident": "an initial or continuing fire report, including spread or hazardous-materials risk",
    "mutual_aid_dispatch": "an external-force dispatch, arrival, evacuation, or support update",
    "drone_dispatch": "a commander-authorized simulated drone mission",
    "historical_query": "a request for a timeline, incident review, or preliminary debrief",
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
# Do not overwrite the FIRE simulator policy with a default policy here.

# -- Simulations (docs/Profile_Split_Plan.md) --------------------------------
#
# FIRE_002 series only. Migrated from profiles/unified_test.py (which had itself migrated it
# from the legacy fixtures/admin_scenarios/*.json bundled fixtures); offsets renumbered to start
# at 0 within this profile's own reserved-ID block (offsets are only required to be unique per
# profile, not globally -- profiles/simulation.py). Independent roster from Standby Squad's
# SEC_001 -- no shared persona/group keys.

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
            "run_id": "FIRE_002_RUN_20260909",
            "title": _catalog_text("firefighting.simulation.fire002.phase1.title"),
            "description": _catalog_text("firefighting.simulation.fire002.phase1.description"),
            "tags": ["fire002", "phase1"],
        },
        "chats": list(FIRE002_CHATS),
        "steps": [
            {
                "step": 1, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander", "timestamp": "2026-09-09T07:00:00Z", "protocol_hint": "record_crew_shift_status",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step1.text"),
            },
            {
                "step": 2, "chat": "fire_response_team", "sender_identity": "omri_firefighter", "timestamp": "2026-09-09T07:45:00Z", "protocol_hint": "record_crew_availability_response",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.omri_firefighter"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step2.text"),
            },
            {
                "step": 3, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T08:30:00Z", "protocol_hint": "update_camera_observation",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step3.text"),
            },
            {
                "step": 4, "chat": "fire_external_forces", "sender_identity": "kkl_mountains_sector", "timestamp": "2026-09-09T09:15:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step4.text"),
            },
            {
                "step": 5, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T10:00:00Z", "protocol_hint": "update_camera_observation",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step5.text"),
            },
            {
                "step": 6, "chat": "fire_external_forces", "sender_identity": "police_hub_agam", "timestamp": "2026-09-09T11:00:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step6.text"),
            },
            {
                "step": 7, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T11:30:00Z", "protocol_hint": "overall_situational_picture",
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
            "run_id": "FIRE_002_RUN_20260909",
            "title": _catalog_text("firefighting.simulation.fire002.phase2.title"),
            "description": _catalog_text("firefighting.simulation.fire002.phase2.description"),
            "tags": ["fire002", "phase2"],
        },
        "chats": list(FIRE002_CHATS),
        "steps": [
            {
                "step": 1, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T12:15:00Z", "protocol_hint": "report_fire_incident",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step1.text"),
            },
            {
                "step": 2, "chat": "fire_external_forces", "sender_identity": "police_hub_agam", "timestamp": "2026-09-09T12:22:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step2.text"),
            },
            {
                "step": 3, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander", "timestamp": "2026-09-09T12:30:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step3.text"),
            },
            {
                "step": 4, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T12:38:00Z", "protocol_hint": "update_camera_observation",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step4.text"),
            },
            {
                "step": 5, "chat": "fire_response_team", "sender_identity": "yuval_ashed3_commander", "timestamp": "2026-09-09T12:42:00Z", "protocol_hint": "report_fire_incident",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step5.text"),
            },
            {
                "step": 6, "chat": "fire_external_forces", "sender_identity": "kkl_mountains_sector", "timestamp": "2026-09-09T12:45:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step6.text"),
            },
            {
                "step": 7, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T12:48:00Z", "protocol_hint": "dispatch_drone_to_incident",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step7.text"),
            },
            {
                "step": 8, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T12:50:00Z", "protocol_hint": "overall_situational_picture",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step8.text"),
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
            "run_id": "FIRE_002_RUN_20260909",
            "title": _catalog_text("firefighting.simulation.fire002.phase3.title"),
            "description": _catalog_text("firefighting.simulation.fire002.phase3.description"),
            "tags": ["fire002", "phase3"],
        },
        "chats": list(FIRE002_CHATS),
        "steps": [
            {
                "step": 1, "chat": "fire_response_team", "sender_identity": "yuval_ashed3_commander", "timestamp": "2026-09-09T13:00:00Z", "protocol_hint": "report_fire_incident",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step1.text"),
            },
            {
                "step": 2, "chat": "fire_external_forces", "sender_identity": "police_hub_agam", "timestamp": "2026-09-09T13:03:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step2.text"),
            },
            {
                "step": 3, "chat": "fire_response_team", "sender_identity": "citizen_reports_group", "timestamp": "2026-09-09T13:07:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.citizen_reports_group"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step3.text"),
            },
            {
                "step": 4, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T13:10:00Z", "protocol_hint": "overall_situational_picture",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step4.text"),
            },
            {
                "step": 5, "chat": "fire_external_forces", "sender_identity": "fire_police_patrol", "timestamp": "2026-09-09T13:14:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.fire_police_patrol"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step5.text"),
            },
            {
                "step": 6, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T13:18:00Z", "protocol_hint": "report_fire_incident",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step6.text"),
            },
            {
                "step": 7, "chat": "fire_external_forces", "sender_identity": "district_fire_commander", "timestamp": "2026-09-09T13:25:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.district_fire_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step7.text"),
            },
            {
                "step": 8, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander", "timestamp": "2026-09-09T13:45:00Z", "protocol_hint": "record_incident_update",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step8.text"),
            },
            {
                "step": 9, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T14:15:00Z", "protocol_hint": "query_historical_incidents",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step9.text"),
            },
        ],
    },
    ),
]


def _seed_fire_surveillance() -> None:
    """Replace generic demo cameras/drones with FIRE-specific assets.

    The base `SQLiteSurveillancePersistence.__init__` already calls
    `_seed_demo_data_if_empty` (5 generic cameras, 3 generic drones). For FIRE
    we need exactly the 3 cameras and 2 drones defined in the canonical asset
    block. This function is idempotent: it only rewrites when the current
    camera set does not match the expected FIRE layout.
    """

    import sqlite3
    from datetime import datetime, timezone as tz

    now = datetime.now(tz.utc).isoformat()
    db_path = FIREFIGHTING_SURVEILLANCE_DB_PATH

    # Ensure tables exist by opening through the store once
    open_surveillance_persistence(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # Check if FIRE layout is already present
    camera_ids = {
        row["camera_id"]
        for row in conn.execute("SELECT camera_id FROM cameras WHERE camera_id IN ('CAM-01', 'CAM-02', 'CAM-03')")
    }
    drone_ids = {
        row["drone_id"]
        for row in conn.execute("SELECT drone_id FROM drones WHERE drone_id IN ('DRONE-01', 'DRONE-02')")
    }
    first = conn.execute("SELECT name FROM cameras WHERE camera_id = 'CAM-01'").fetchone()
    if camera_ids == {"CAM-01", "CAM-02", "CAM-03"} and drone_ids == {"DRONE-01", "DRONE-02"} and first and "\u05d0\u05d5\u05e8\u05e0\u05d9\u05dd" in first["name"]:
        conn.close()
        return

    # Clear and re-seed with FIRE-specific data
    conn.execute("DELETE FROM drone_missions")
    conn.execute("DELETE FROM drones")
    conn.execute("DELETE FROM cameras")

    fire_cameras = [
        ("CAM-01", "\u05de\u05e6\u05dc\u05de\u05d4 \u05ea\u05e8\u05de\u05d9\u05ea \u05de\u05d2\u05d3\u05dc \u05ea\u05e6\u05e4\u05d9\u05ea \u05d0\u05d5\u05e8\u05e0\u05d9\u05dd",
         "pine_ridge", "active", 0,
         "\u05de\u05e6\u05dc\u05de\u05d4 \u05ea\u05e8\u05de\u05d9\u05ea \u05e2\u05dd \u05d7\u05d9\u05d9\u05e9\u05df \u05d8\u05de\u05e4\u05e8\u05d8\u05d5\u05e8\u05d4 \u05d1\u05de\u05d2\u05d3\u05dc \u05ea\u05e6\u05e4\u05d9\u05ea \u05d0\u05d5\u05e8\u05e0\u05d9\u05dd. \u05de\u05e6\u05d1 \u05e9\u05d2\u05e8\u05d4.",
         now),
        ("CAM-02", "\u05de\u05e6\u05dc\u05de\u05ea \u05e6\u05d5\u05de\u05ea \u05d4\u05de\u05d7\u05e6\u05d1\u05d4",
         "quarry_junction", "active", 90,
         "\u05de\u05e6\u05dc\u05de\u05d4 \u05d0\u05d5\u05e4\u05d8\u05d9\u05ea \u05d1\u05e6\u05d5\u05de\u05ea \u05d4\u05de\u05d7\u05e6\u05d1\u05d4. \u05de\u05e6\u05d1 \u05e9\u05d2\u05e8\u05d4.",
         now),
        ("CAM-03", "\u05de\u05e6\u05dc\u05de\u05ea \u05e8\u05db\u05e1 \u05d0\u05d5\u05e8\u05e0\u05d9\u05dd",
         "pine_ridge", "active", 180,
         "\u05de\u05e6\u05dc\u05de\u05d4 \u05d0\u05d5\u05e4\u05d8\u05d9\u05ea \u05d1\u05e8\u05db\u05e1 \u05d0\u05d5\u05e8\u05e0\u05d9\u05dd. \u05de\u05e6\u05d1 \u05e9\u05d2\u05e8\u05d4.",
         now),
    ]
    conn.executemany(
        "INSERT INTO cameras (camera_id, name, area, status, azimuth_degrees, feed_summary, last_updated) VALUES (?,?,?,?,?,?,?)",
        fire_cameras,
    )

    fire_drones = [
        ("DRONE-01", "\u05ea\u05e6\u05e4\u05d9\u05ea-01", "DJI Matrice 350 RTK", "ready", 95, "fire_station", None, now),
        ("DRONE-02", "\u05d2\u05d9\u05d1\u05d5\u05d9-02", "DJI Mavic 3T", "ready", 88, "fire_station", None, now),
    ]
    conn.executemany(
        "INSERT INTO drones (drone_id, callsign, model, status, battery_percent, current_area, assigned_mission_id, last_updated) VALUES (?,?,?,?,?,?,?,?)",
        fire_drones,
    )
    conn.commit()
    conn.close()


def ensure_seed_data() -> None:
    """Explicit, idempotent entry point for FIRE profile seed data.

    Seeds the surveillance DB with the 3 FIRE cameras and 2 FIRE drones,
    and ensures the bot-service identity exists in the history DB.
    Mirrors `profiles.standby_squad.ensure_seed_data` in purpose.
    """

    hist_store = open_persistence(DB_PATH)
    try:
        if hist_store.read_user("bot-service") is None:
            hist_store.write_user("bot-service", "commander")
    finally:
        hist_store.close()
        
    # Seed exactly the six declared FIRE simulation personas.  Do not create a
    # second roster with hand-written identities: the bot simulator authenticates
    # using the deterministic IDs from SIMULATION_USERS.
    from persistence import open_team_status_persistence
    from profiles.simulation import simulation_user_telegram_id
    team_store = open_team_status_persistence(FIREFIGHTING_CREW_STATUS_DB_PATH)
    try:
        crew_keys = {
            "lahav_avi_shift_commander", "omri_firefighter", "yuval_ashed3_commander",
            "firefighter_team_a_4", "firefighter_team_a_5", "firefighter_team_a_6",
        }
        for persona in SIMULATION_USERS:
            if persona.key in crew_keys:
                team_store.register_member(
                    simulation_user_telegram_id(persona.offset), persona.full_name,
                )
        if not team_store.roster_is_approved():
            team_store.approve_roster("simulation-provisioning")
        team_store.register_vehicle("ASHED-3", "Ashed 3", status="available", current_location="fire_station")
        team_store.register_vehicle("CARMEL-1", "Carmel 1", status="available", current_location="fire_station")
    finally:
        pass

    _seed_fire_surveillance()
