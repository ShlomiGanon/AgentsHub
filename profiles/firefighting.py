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

    apparatus_store = open_apparatus_store(FIREFIGHTING_APPARATUS_DB_PATH)
    for apparatus in APPARATUS:
        apparatus_store.ensure_apparatus(**apparatus)


OPERATIONAL_SEED = _ensure_operational_seed_data

# The dashboard runs exactly one profile at a time.  Both selectable profiles
# therefore use the deployment's single Telegram bot token; the supervisor
# fully stops the old bot before starting the newly selected profile.
BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []


class FirefightingSurveillanceAgent(SurveillanceAgent):
    """Binds the reusable visual-surveillance specialist to this profile's own DB --
    fire cameras and thermal sensors (docs/Profile_Split_Plan.md section 4.2).

    Opens the same already-generic `ResponseTeamSurveillanceStore`
    (`open_response_team_surveillance_store`, despite the module name -- see
    docs/Admin_Tables_Plan.md section 1) `profiles/response_team.py` uses, with this profile's
    own DB path and home area, instead of the shared base's default hardcoded-`'central_hub'`
    store -- so `return_drone_to_base` (recall, already declared on the shared
    `SurveillanceAgent` base) works for this profile exactly like it does for response_team,
    with no per-profile recall tool needed."""

    surveillance_db_path = FIREFIGHTING_SURVEILLANCE_DB_PATH

    def __init__(self, model: str, api_key: str | None = None):
        self.surveillance_store = open_response_team_surveillance_store(
            self.surveillance_db_path, home_area=FIREFIGHTING_DRONE_HOME
        )
        Agent.__init__(self, model, api_key)


class FirefightingCrewStatusAgent(TeamStatusAgent):
    """Binds the reusable readiness-status specialist to this profile's own DB -- the
    firefighting crew's shift roster and attendance (docs/Profile_Split_Plan.md section 4.2).

    Also owns station apparatus (engine/vehicle) status -- FIRE_002's own simulation text
    reports apparatus readiness in the exact same "station opening" messages as crew
    availability (docs/Admin_Tables_Plan.md's simulation-data-alignment audit), so it belongs
    on this same "station readiness" specialist rather than a new agent."""

    status_db_path = FIREFIGHTING_CREW_STATUS_DB_PATH
    timezone_name = "Asia/Jerusalem"
    attendance_check_hour = 8
    response_window_hours = 1

    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model, api_key)
        self.apparatus_store = open_apparatus_store(FIREFIGHTING_APPARATUS_DB_PATH)
        # Incident linkage is keyed against core `events`, which live in this profile's
        # `DB_PATH` -- a different file from the apparatus/crew-status stores above.
        self.incident_store = open_incident_responder_store(DB_PATH)

    @tool(
        "get_apparatus_status",
        "Returns the station's own engine/vehicle apparatus roster with each one's current "
        "status (operational, dispatched, unavailable, or maintenance) and area. Read-only.",
        side_effecting=False,
    )
    def get_apparatus_status(self) -> str:
        rows = self.apparatus_store.list_apparatus()
        if not rows:
            return "No apparatus is registered."
        lines = ["Station apparatus:"]
        for row in rows:
            area = f", area: {row['current_area']}" if row["current_area"] else ""
            lines.append(f"- {row['callsign']} ({row['apparatus_id']}): {row['status'].upper()}{area}")
        return "\n".join(lines)

    @tool(
        "update_apparatus_status",
        "Records one apparatus/engine's own operating status (operational, dispatched, "
        "unavailable, or maintenance), and its area if the source states one. identifier is the "
        "apparatus's callsign (e.g. 'Ashed 3') or apparatus_id. Side-effecting and idempotent -- "
        "recording the identical status for the same apparatus twice leaves one record. This "
        "alone does not link the apparatus to any incident -- call join_incident_response "
        "separately when the report indicates it is dispatched to one.",
        side_effecting=True,
        idempotent=True,
    )
    def update_apparatus_status(self, identifier: str, status: str, current_area: str = "") -> str:
        try:
            updated = self.apparatus_store.update_status(identifier, status.strip().lower(), current_area.strip() or None)
        except ApparatusStoreError as exc:
            return failed_tool_result(f"The apparatus status was not stored: {exc}")
        area = f", area: {updated['current_area']}" if updated["current_area"] else ""
        return f"{updated['callsign']} status recorded: {updated['status'].upper()}{area}."

    @tool(
        "join_incident_response",
        "Links one named apparatus to the one specific real incident currently on record for "
        "`area`, when exactly one exists -- use when a report clearly indicates that apparatus is "
        "dispatched to a specific incident there, not merely relocated or stationed. identifier is "
        "the apparatus's callsign (e.g. 'Ashed 3') or apparatus_id. Never links on area alone: if "
        "no recent incident is on record, or more than one is, nothing is linked and a plain "
        "explanation is returned instead of a guess. Automatically closes any other incident that "
        "apparatus was previously linked to (reassignment). Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def join_incident_response(self, identifier: str = "", area: str = "") -> str:
        apparatus = self.apparatus_store.get_apparatus(identifier)
        if apparatus is None:
            return f"Not linked: apparatus '{identifier}' not found."
        if not area.strip():
            return failed_tool_result("Clarification required: area is required.")

        event, clarification = self.incident_store.resolve_single_candidate(area.strip())
        if event is None:
            return f"Not linked: {clarification}"
        self.incident_store.join(event["event_id"], apparatus["apparatus_id"])
        return f"{apparatus['callsign']} linked to the incident currently on record for '{area.strip()}'."

    @tool(
        "leave_incident_response",
        "Closes one named apparatus's own current incident link, if any -- use when a report "
        "states that apparatus is no longer responding to an incident, with no new one stated. "
        "identifier is the apparatus's callsign or apparatus_id. Harmless (not an error) if it had "
        "no open link. Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def leave_incident_response(self, identifier: str = "") -> str:
        apparatus = self.apparatus_store.get_apparatus(identifier)
        if apparatus is None:
            return f"Not updated: apparatus '{identifier}' not found."
        closed = self.incident_store.leave(apparatus["apparatus_id"])
        if closed is None:
            return f"No open incident link was found to close for {apparatus['callsign']}."
        return f"{apparatus['callsign']}'s incident link was closed."

    @tool(
        "list_incident_responders",
        "Answers 'who/what else is responding' for the one specific real incident currently on "
        "record for `area`, when exactly one exists -- lists only personnel and apparatus "
        "actually linked to that incident (via join_incident_response), never anyone/anything "
        "merely recorded in the same area. If no recent incident is on record, or more than one "
        "is, says so plainly instead of guessing. Read-only.",
        side_effecting=False,
    )
    def list_incident_responders(self, area: str = "") -> str:
        if not area.strip():
            return failed_tool_result("Clarification required: area is required.")

        event, clarification = self.incident_store.resolve_single_candidate(area.strip())
        if event is None:
            return clarification

        links = self.incident_store.list_open_responders(event["event_id"])
        if not links:
            return f"No one is currently linked to the incident on record for '{area.strip()}'."

        names = []
        for link in links:
            apparatus = self.apparatus_store.get_apparatus(link["identity"])
            names.append(apparatus["callsign"] if apparatus is not None else link["identity"])
        return f"Currently linked to the incident on record for '{area.strip()}': {', '.join(names)}."

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
            return failed_tool_result("The crew shift status was not stored: authenticated requester identity is unavailable.")

        normalized = availability.strip().lower()
        if normalized not in {"available", "unavailable"}:
            return failed_tool_result("Clarification required: specify whether the crew is available or unavailable.")
        if normalized != "available":
            return failed_tool_result("Clarification required: bulk shift recording currently supports an explicit available declaration only.")

        approved_members = self.status_store.list_members(approved_only=True)
        if not approved_members:
            return failed_tool_result("The crew shift status was not stored: the approved roster is empty.")

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
                member = by_identity.get(token.casefold()) or by_name.get(token.casefold())
                if member is None:
                    unknown.append(token)
                elif member not in selected_members:
                    selected_members.append(member)
            if unknown:
                return failed_tool_result(f"The crew shift status was not stored: unknown approved member(s): {', '.join(unknown)}.")
            if not selected_members:
                return failed_tool_result("Clarification required: specify which approved crew members are included.")

        now_iso = received_at.strip() or datetime.now(timezone.utc).isoformat()
        source_base = source_message_id.strip() or f"crew-shift-{int(datetime.now(timezone.utc).timestamp())}"
        text = original_text.strip() or f"crew shift status: {normalized}"
        stored = 0
        try:
            for member in selected_members:
                self.status_store.record_response(
                    telegram_identity=member["telegram_identity"],
                    source_message_id=f"{source_base}:{member['telegram_identity']}",
                    availability=normalized,
                    original_text=text,
                    received_at=now_iso,
                )
                stored += 1
        except Exception as exc:
            return failed_tool_result(f"The crew shift status was not stored: {exc}")
        return f"Crew shift availability recorded for {stored} approved member(s)."


class FirefightingExternalForcesAgent(NeighboringForcesAgent):
    """Mutual-aid dispatch specialist -- a thin subclass of the shared
    `agents.neighboring_forces_agent.NeighboringForcesAgent` (docs/Admin_Tables_Plan.md sections
    3/3.2), giving this profile the exact same persisted dispatch-log + computed-remaining-
    capacity mechanism `response_team.py` has, instead of the previous in-memory
    `agents.friendly_forces_agent.FriendlyForcesAgent`-based stand-in. `dispatch_police`,
    `dispatch_ambulance`, `dispatch_water_tankers`, and `dispatch_aircraft` no longer exist as
    separate tools -- every kind now goes through the shared base's single
    `dispatch_neighboring_force(kind, target_area, unit_count, note)` tool, with `kind` selecting
    among `police|ambulance|water_tankers|aircraft`. No override of `_resolve_kind`/
    `_check_capacity` is needed -- this profile has no non-force kind like response_team's own
    "squad", so the shared base's defaults, driven by `FORCE_BASES`/`FORCE_POOL_SIZE` below, are
    exactly right as-is."""

    dispatch_db_path = FIREFIGHTING_FORCES_DB_PATH
    force_bases = FORCE_BASES
    force_pool_size = FORCE_POOL_SIZE


AGENTS = [
    AgentSpec(cls=FirefightingSurveillanceAgent, tier="sub"),
    AgentSpec(cls=FirefightingCrewStatusAgent, tier="sub"),
    AgentSpec(cls=FirefightingExternalForcesAgent, tier="sub"),
]

# A narrow, low-stakes judgment call (decide one apparatus's resulting status, and whether it also
# indicates incident response, then call up to three known tools) never needs the agent's default
# reasoning budget -- same mechanism SurveillanceAgent.process already uses for its own
# tool-turn-plus-summary calls.
_FAST_JUDGMENT_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")


def _as_aware_iso(value: str) -> str:
    """A persisted event timestamp is stored without an explicit offset but is always UTC
    — the crew-status tools reject a naive string, so make it explicit before a direct bind."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _bind_record_crew_availability(event: dict) -> tuple[Step, ...]:
    """Same tool and kwargs as response_team attendance: record_attendance_response from extracted fields."""

    absence_reason = (event.get("absence_reason") or "").strip()
    received_at = event.get("received_at") or ""
    base_kwargs = {
        "source_message_id": event.get("source_message_id") or "",
        "original_text": event.get("raw_text") or "",
        "received_at": _as_aware_iso(received_at) if received_at else "",
    }
    if not absence_reason:
        kwargs = {**base_kwargs, "availability": "available", "reason": "", "unavailable_days": 0}
        required: tuple[str, ...] = ()
    else:
        missing = tuple(name for name in ("availability_start", "availability_end") if not event.get(name))
        kwargs = {**base_kwargs, "availability": "unavailable", "reason": absence_reason}
        if missing:
            required = missing
        else:
            start = datetime.fromisoformat(event["availability_start"])
            end = datetime.fromisoformat(event["availability_end"])
            days = (end - start).total_seconds() / 86400
            kwargs["unavailable_days"] = max(1, int(days + 0.999999))
            required = ()
    return (
        Step(
            agent_name="team_status_agent",
            task_text="Record the reporter's own crew availability response, bound directly from the event's extracted fields.",
            allowed_tools=("record_attendance_response",),
            step_id="1",
            required_event_fields=required,
            kind="direct_tool",
            direct_tool_name="record_attendance_response",
            direct_tool_kwargs=kwargs,
        ),
    )


def _bind_record_crew_shift_status(event: dict) -> tuple[Step, ...]:
    """Commander shift declaration: record_crew_shift_status with member_identities='all', plus one
    apparatus-status step per extracted entity — the same tools a successful CrewAI formulation uses."""

    absence_reason = (event.get("absence_reason") or "").strip()
    received_at = event.get("received_at") or ""
    shift_kwargs = {
        "member_identities": "all",
        "availability": "unavailable" if absence_reason else "available",
        "source_message_id": event.get("source_message_id") or "",
        "original_text": event.get("raw_text") or "",
        "received_at": _as_aware_iso(received_at) if received_at else "",
    }
    steps = [
        Step(
            agent_name="team_status_agent",
            task_text="Record the commander's crew shift availability declaration for the entire approved crew.",
            allowed_tools=("record_crew_shift_status",),
            step_id="1",
            kind="direct_tool",
            direct_tool_name="record_crew_shift_status",
            direct_tool_kwargs=shift_kwargs,
        )
    ]
    entities = event.get("entities") or []
    description = (event.get("description") or "").strip()
    area = (event.get("area") or "").strip()
    for index, identifier in enumerate(entities):
        steps.append(
            Step(
                agent_name="team_status_agent",
                task_text=(
                    f"Apparatus {identifier} was named in the shift declaration"
                    f"{f' in area {area}' if area else ''}. Determine its resulting status "
                    f"(operational, dispatched, unavailable, or maintenance) from the report "
                    f"below, and call update_apparatus_status for {identifier} with that status.\n\n"
                    f"Report: {description}"
                ),
                allowed_tools=("update_apparatus_status",),
                step_id=str(index + 2),
                invocation_policy=_FAST_JUDGMENT_POLICY,
            )
        )
    return tuple(steps)


def _bind_apparatus_movement(event: dict) -> tuple[Step, ...]:
    """Mirrors response_team.py's own _bind_report_team_movement: a direct_tool_binder skips
    formulate_tasks entirely (no separate task-formulation model call), while the step(s) it
    returns still run through the normal agent turn -- status and incident-linking are genuine
    judgment calls from free text, the same class of decision _bind_update_camera_status makes
    for a camera's resulting status, so a keyword heuristic can never pre-decide them."""

    entities = event.get("entities") or []
    area = (event.get("area") or "").strip()
    description = (event.get("description") or "").strip()
    missing = tuple(name for name in ("entities", "description") if not event.get(name))
    if missing:
        return (
            Step(
                agent_name="team_status_agent",
                task_text="Record the reported apparatus status, bound directly from the event's extracted fields.",
                allowed_tools=("update_apparatus_status",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="update_apparatus_status",
                direct_tool_kwargs={},
            ),
        )
    return tuple(
        Step(
            agent_name="team_status_agent",
            task_text=(
                f"Apparatus {identifier} was reported on{f' in area {area}' if area else ''}. Determine its "
                f"resulting status (operational, dispatched, unavailable, or maintenance) from the report "
                f"below, and call update_apparatus_status for {identifier} with that status. Then decide: "
                f"does the report clearly say {identifier} is dispatched to a specific incident there (not "
                f"merely relocated)? If so, also call join_incident_response for the same area. If the "
                f"report also asks who/what else is responding, also call list_incident_responders for the "
                f"same area and include its answer in your reply.\n\nReport: {description}"
            ),
            allowed_tools=("update_apparatus_status", "join_incident_response", "list_incident_responders"),
            step_id=str(index + 1),
            invocation_policy=_FAST_JUDGMENT_POLICY,
        )
        for index, identifier in enumerate(entities)
    )


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
        needs_insight=False,
        direct_tool_binder=_bind_record_crew_availability,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="record_crew_shift_status",
        description=(
            "Applies when a commander explicitly declares the availability of multiple approved "
            "firefighting crew members for a shift -- for example, that the entire crew is available "
            "at opening; use record_crew_shift_status to persist that declaration. The same report "
            "commonly also states each engine/vehicle's own operating status (e.g. 'Ashed 3 and "
            "Carmel 1 are operational') -- call update_apparatus_status for each one named. Does "
            "not apply to a request for a read-only roster picture (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("record_crew_shift_status", "update_apparatus_status"),
        expected_success_output="Confirmation that the declared crew shift availability was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=True,
        needs_insight=False,
        direct_tool_binder=_bind_record_crew_shift_status,
    ),
    Protocol(
        name="report_apparatus_movement",
        description=(
            "Applies when any crew member (not only a commander) reports one named apparatus's "
            "own dispatch or movement -- e.g. an engine left the station en route to a call and "
            "is no longer available there -- use update_apparatus_status for that engine, and, "
            "if the report clearly states it is dispatched to a specific incident there (not "
            "merely relocated), also call join_incident_response for the same area. If the "
            "report also asks who/what else is responding, also call list_incident_responders. "
            "Does not apply to a commander's blanket declaration of multiple members' shift "
            "availability (use record_crew_shift_status for that), and does not apply to a "
            "read-only status question (use report_crew_status for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("update_apparatus_status", "join_incident_response", "list_incident_responders"),
        expected_success_output=(
            "Confirmation that the named apparatus's status/area was recorded; if it was "
            "dispatched to a specific incident, confirmation it was linked to it and, if asked, "
            "who/what else is currently linked to that same incident."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_apparatus_movement,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="report_crew_status",
        description=(
            "Applies when someone asks for a read-only picture of the firefighting crew's shift "
            "roster or engine/vehicle availability -- e.g. who is currently on duty, or whether "
            "the station's apparatus is available; does not apply to a commander declaring "
            "multiple members' availability (use "
            "record_crew_shift_status for that), and does not apply to a single member's own "
            "availability report (use "
            "record_crew_availability_response for that)."
        ),
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability", "get_apparatus_status"),
        expected_success_output="A read-only roster report covering crew headcount and availability.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        direct_lane_eligible=True,
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
            "with no risk to structures -- use log_fire_observation for that, never dispatch a "
            "drone for an already-handled report), and does not apply to a resource-dispatch "
            "decision itself (use dispatch_mutual_aid for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output="Confirmation of drone dispatch to confirm/monitor the reported fire.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=False,
        # A field/citizen fire report can arrive in any group, not only the
        # camera-ops channel this protocol's own agent (surveillance_agent) is bound
        # to -- keep it selectable everywhere (orchestrator/group_routing.py).
        safety_critical=True,
    ),
    Protocol(
        # Split from report_fire_incident (over-dispatch fix, parity with response_team's
        # report_security_incident split): an already-resolved report has no dispatch tool
        # available at all here, structurally, not merely a prompt instruction the agent could
        # still disregard.
        name="log_fire_observation",
        description=(
            "Applies when a report describes a fire-related observation that is explicitly "
            "already resolved, extinguished, or presents no further risk -- e.g. a small "
            "roadside fire already extinguished with no risk to structures. Purely informational: "
            "logs the observation; never dispatches a drone or any other resource. Does not apply "
            "to anything still active, escalating, or unconfirmed (use report_fire_incident for "
            "that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=(),
        expected_success_output="A plain acknowledgement that the observation was logged.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        safety_critical=True,
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
        participating_agents=("neighboring_forces_agent",),
        approved_tools=("dispatch_neighboring_force",),
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
            "location and crew status'. Also applies when a commander describes multiple "
            "critical hot spots or reports at once and asks to prioritize response or allocate "
            "crews/water -- pull the actual current records rather than trusting the "
            "commander's own recap of what was reported earlier. Does not apply when only one "
            "of the two domains is asked about."
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
                "step": 1, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander", "timestamp": "2026-09-09T07:00:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step1.text"),
            },
            {
                "step": 2, "chat": "fire_response_team", "sender_identity": "omri_firefighter", "timestamp": "2026-09-09T07:45:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.omri_firefighter"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step2.text"),
            },
            {
                "step": 3, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T08:30:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step3.text"),
            },
            {
                "step": 4, "chat": "fire_external_forces", "sender_identity": "kkl_mountains_sector", "timestamp": "2026-09-09T09:15:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step4.text"),
            },
            {
                "step": 5, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T10:00:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step5.text"),
            },
            {
                "step": 6, "chat": "fire_external_forces", "sender_identity": "police_hub_agam", "timestamp": "2026-09-09T11:00:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase1.step6.text"),
            },
            {
                "step": 7, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T11:30:00Z",
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
                "step": 1, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T12:15:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step1.text"),
            },
            {
                "step": 2, "chat": "fire_external_forces", "sender_identity": "police_hub_agam", "timestamp": "2026-09-09T12:22:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step2.text"),
            },
            {
                "step": 3, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander", "timestamp": "2026-09-09T12:30:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step3.text"),
            },
            {
                "step": 4, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T12:38:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step4.text"),
            },
            {
                "step": 5, "chat": "fire_response_team", "sender_identity": "yuval_ashed3_commander", "timestamp": "2026-09-09T12:42:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step5.text"),
            },
            {
                "step": 6, "chat": "fire_external_forces", "sender_identity": "kkl_mountains_sector", "timestamp": "2026-09-09T12:45:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.kkl_mountains_sector"),
                "text": _catalog_text("firefighting.simulation.fire002.phase2.step6.text"),
            },
            {
                "step": 7, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T12:50:00Z",
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
                "step": 1, "chat": "fire_response_team", "sender_identity": "yuval_ashed3_commander", "timestamp": "2026-09-09T13:00:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.yuval_ashed3_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step1.text"),
            },
            {
                "step": 2, "chat": "fire_external_forces", "sender_identity": "police_hub_agam", "timestamp": "2026-09-09T13:03:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.police_hub_agam"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step2.text"),
            },
            {
                "step": 3, "chat": "fire_response_team", "sender_identity": "citizen_reports_group", "timestamp": "2026-09-09T13:07:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.citizen_reports_group"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step3.text"),
            },
            {
                "step": 4, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T13:10:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.station_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step4.text"),
            },
            {
                "step": 5, "chat": "fire_external_forces", "sender_identity": "fire_police_patrol", "timestamp": "2026-09-09T13:14:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.fire_police_patrol"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step5.text"),
            },
            {
                "step": 6, "chat": "fire_cameras", "sender_identity": "roni_surveillance_operator", "timestamp": "2026-09-09T13:18:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.roni_surveillance_operator"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step6.text"),
            },
            {
                "step": 7, "chat": "fire_external_forces", "sender_identity": "district_fire_commander", "timestamp": "2026-09-09T13:25:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.district_fire_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step7.text"),
            },
            {
                "step": 8, "chat": "fire_response_team", "sender_identity": "lahav_avi_shift_commander", "timestamp": "2026-09-09T13:45:00Z",
                "sender_name": _catalog_text("firefighting.simulation.fire002.persona.lahav_avi_shift_commander"),
                "text": _catalog_text("firefighting.simulation.fire002.phase3.step8.text"),
            },
            {
                "step": 9, "chat": "fire_commander_dm", "sender_identity": "station_commander", "timestamp": "2026-09-09T14:15:00Z",
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


def _drones_list(deps) -> list:
    return deps.registry.get("surveillance_agent").surveillance_store.list_drones()


def _drones_get(deps, drone_id: str):
    return deps.registry.get("surveillance_agent").surveillance_store.get_drone(drone_id)


def _drones_write(deps, row: dict) -> None:
    store = deps.registry.get("surveillance_agent").surveillance_store
    store.admin_update_drone(row["drone_id"], **{k: v for k, v in row.items() if k != "drone_id"})


def _attendance_list(deps) -> list:
    return deps.registry.get("team_status_agent").status_store.list_responses()


def _attendance_get(deps, response_id: str):
    return deps.registry.get("team_status_agent").status_store.get_response(response_id)


def _attendance_write(deps, row: dict) -> None:
    store = deps.registry.get("team_status_agent").status_store
    store.admin_update_attendance_fields(row["response_id"], **{k: v for k, v in row.items() if k != "response_id"})


def _forces_list(deps) -> list:
    return deps.registry.get("neighboring_forces_agent").dispatch_store.list_dispatches()


def _forces_get(deps, request_id: str):
    return deps.registry.get("neighboring_forces_agent").dispatch_store.get_dispatch(request_id)


def _forces_write(deps, row: dict) -> None:
    store = deps.registry.get("neighboring_forces_agent").dispatch_store
    store.admin_update_dispatch(row["request_id"], **{k: v for k, v in row.items() if k != "request_id"})


ADMIN_TABLES = (
    AdminTable(
        key="drones",
        label="Drones",
        primary_key="drone_id",
        columns=(
            AdminColumn("drone_id", "Drone ID", editable=False),
            AdminColumn("callsign", "Callsign", required=True),
            AdminColumn("model", "Model"),
            AdminColumn(
                "status", "Status", kind="select",
                choices=("ready", "in_flight", "charging", "maintenance"), required=True,
            ),
            AdminColumn("battery_percent", "Battery %", kind="number"),
            AdminColumn("current_area", "Current area"),
            AdminColumn("assigned_mission_id", "Assigned mission ID"),
            AdminColumn("last_updated", "Last updated", editable=False),
        ),
        list_fn=_drones_list, get_fn=_drones_get, write_fn=_drones_write,
    ),
    AdminTable(
        key="attendance",
        label="Crew Shift Attendance",
        primary_key="response_id",
        columns=(
            AdminColumn("response_id", "Response ID", editable=False),
            AdminColumn("telegram_identity", "Member", editable=False),
            AdminColumn("availability", "Availability", kind="select", choices=("available", "unavailable")),
            AdminColumn("reason", "Reason"),
            AdminColumn("unavailable_until", "Unavailable until"),
            AdminColumn(
                "approval_status", "Approval status", kind="select",
                choices=("accepted", "pending", "rejected"),
            ),
            AdminColumn("reviewed_by", "Reviewed by", editable=False),
            AdminColumn("reviewed_at", "Reviewed at", editable=False),
            AdminColumn("original_text", "Original text", editable=False),
            AdminColumn("received_at", "Received at", editable=False),
        ),
        list_fn=_attendance_list, get_fn=_attendance_get, write_fn=_attendance_write,
    ),
    AdminTable(
        key="forces",
        label="Friendly Forces",
        primary_key="request_id",
        columns=(
            AdminColumn("request_id", "Request ID", editable=False),
            AdminColumn("force_kind", "Force kind", required=True),
            AdminColumn("unit_count", "Unit count", kind="number", required=True),
            AdminColumn("origin_area", "Origin area"),
            AdminColumn("target_area", "Target area"),
            AdminColumn("status", "Status", kind="select", choices=("en_route", "arrived")),
            AdminColumn("dispatched_at", "Dispatched at", editable=False),
            AdminColumn("eta_seconds", "ETA (seconds)", kind="number"),
            AdminColumn("arrived_at", "Arrived at"),
            AdminColumn("note", "Note"),
            AdminColumn("event_id", "Event ID", editable=False),
        ),
        list_fn=_forces_list, get_fn=_forces_get, write_fn=_forces_write,
    ),
)

