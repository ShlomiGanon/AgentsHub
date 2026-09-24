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
  - The three agents below are profile-owned subclasses (two extend the
    shared `TeamStatusAgent`/`SurveillanceAgent` with this profile's own
    tools and store; the third, `NeighboringForcesAgent`, is wholly new).
    None of their tools are added to the shared `agents/friendly_forces_
    agent.py`, `agents/surveillance_agent.py`, `agents/team_status_agent.py`,
    or `agents/roster_agent.py` modules, which stay untouched.
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
    SurveillanceAgent,
    TeamStatusAgent,
    get_authenticated_request_identity,
    tool,
)
from messages import get_catalog
from persistence import (
    SurveillancePersistenceError,
    TeamStatusPersistenceError,
    open_neighboring_force_store,
    open_response_team_roster_store,
    open_response_team_surveillance_store,
)
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol

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

BOT_TOKEN_ENV = "RESPONSE_TEAM_BOT_TOKEN"
MODEL_CREDENTIAL_ENVS: list[str] = []

RETRY_COUNT = 2
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()


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
    "police": "east_gate",
    "k9": "east_orchards",
    "yasam": "east_gate",
}

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


class ResponseTeamRosterAgent(TeamStatusAgent):
    """Roster/attendance specialist -- a profile-owned subclass of the
    shared `TeamStatusAgent` (agents/team_status_agent.py stays untouched),
    backed by this profile's own `ResponseTeamRosterStore` instead of the
    shared `SQLiteTeamStatusPersistence`: same `DB_PATH` as the core tables,
    plus the `current_area` column the shared store doesn't have. Inherits
    `record_attendance_response`, `report_team_availability`, and
    `start_daily_attendance_check` unchanged; adds `report_team_movement`.
    """

    name = "roster_agent"
    status_db_path = DB_PATH
    timezone_name = TIMEZONE
    attendance_check_hour = 8
    response_window_hours = 1

    def __init__(self, model: str, api_key: str | None = None):
        if not self.status_db_path:
            raise TypeError("ResponseTeamRosterAgent requires a class-level status_db_path")
        self.status_store = open_response_team_roster_store(self.status_db_path)
        Agent.__init__(self, model, api_key)

    @tool(
        "report_team_movement",
        "Records a team member's own current area -- e.g. travelling to or arriving at an area "
        "while still on duty. Side-effecting and idempotent -- recording the same area twice for "
        "the same member leaves one current value.",
        side_effecting=True,
        idempotent=True,
    )
    def report_team_movement(self, area: str = "", member_identity: str = "") -> str:
        identity = (member_identity or get_authenticated_request_identity() or "").strip()
        if not identity:
            return "The movement report was not stored: authenticated requester identity is unavailable."
        if not area.strip():
            return "Clarification required: area is required."

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(member["telegram_identity"] == identity for member in approved_members):
            return "The movement report was not stored: requester is not an approved roster member."

        try:
            updated = self.status_store.set_current_area(identity, area.strip())
        except TeamStatusPersistenceError as exc:
            return f"The movement report was not stored: {exc}"
        return f"{updated['full_name']}'s current area was recorded as '{updated['current_area']}'."


class ResponseTeamSurveillanceAgent(SurveillanceAgent):
    """Cameras + drones specialist -- a profile-owned subclass of the shared
    `SurveillanceAgent` (agents/surveillance_agent.py stays untouched),
    backed by this profile's own `ResponseTeamSurveillanceStore`: same
    `DB_PATH` as the core tables, this profile's own ETA matrix instead of
    the shared store's standby-sector table, and no hardcoded demo-data
    seed (cameras/drones come from this profile's own `CAMERAS`/`DRONES`
    via `OPERATIONAL_SEED`, below). Inherits `get_drone_fleet_status`,
    `dispatch_drone_to_area`, `get_active_missions`, and
    `get_surveillance_overview` unchanged; adds `update_camera_status` and
    `recall_drone` (recalling to `DRONES_WAREHOUSE`)."""

    surveillance_db_path = DB_PATH

    def __init__(self, model: str, api_key: str | None = None):
        if not self.surveillance_db_path:
            raise TypeError("ResponseTeamSurveillanceAgent requires a class-level surveillance_db_path")
        self.surveillance_store = open_response_team_surveillance_store(
            self.surveillance_db_path, eta_fn=eta_seconds, home_area=DRONES_WAREHOUSE
        )
        Agent.__init__(self, model, api_key)

    @tool(
        "update_camera_status",
        "Records a camera's own operating-condition observation and/or status (active, offline, "
        "or degraded) for one named camera identifier -- call it once per camera identifier when "
        "a report names more than one. Side-effecting and idempotent -- recording the identical "
        "observation for the same camera twice leaves one record.",
        side_effecting=True,
        idempotent=True,
    )
    def update_camera_status(self, camera_id: str, observation: str, status: str = "") -> str:
        if not camera_id.strip():
            return "Clarification required: camera_id is required."
        if not observation.strip():
            return "Clarification required: observation is required."
        try:
            updated = self.surveillance_store.update_camera_feed(
                camera_id=camera_id.strip(),
                feed_summary=observation.strip(),
                status=status.strip().lower() or None,
            )
        except SurveillancePersistenceError as exc:
            return f"Camera status update failed: {exc}"
        return (
            f"Camera '{updated['camera_id']}' status recorded.\n"
            f"- Status: {updated['status'].upper()}\n"
            f"- Observation: {updated['feed_summary']}\n"
            f"- Last updated: {updated['last_updated']}"
        )

    @tool(
        "recall_drone",
        f"Recalls one active drone (or, given 'all', every active drone) to {DRONES_WAREHOUSE}. "
        "drone_or_mission_id is optional only when exactly one mission is active.",
        side_effecting=True,
        idempotent=True,
    )
    def recall_drone(self, drone_or_mission_id: str = "") -> str:
        return self.return_drone_to_base(drone_or_mission_id)


class NeighboringForcesAgent(Agent):
    """Neighboring/external-force dispatch-log specialist -- wholly new,
    profile-only (not the shared `agents/friendly_forces_agent.py`
    `FriendlyForcesAgent`, which this profile no longer uses). Owns exactly
    one table, `neighboring_force_dispatches`: force kinds and home bases
    stay profile constants (`FORCE_BASES`, above), never a standing-units
    table. Dispatch text always says "recorded, en route, ETA=..." -- the
    later `en_route -> arrived` transition, once the ETA has elapsed, is
    computed automatically (docs/responce_improve.md's one documented
    exception to "a tool result proves only the tool's own effect"), never
    reported by this agent."""

    name = "neighboring_forces_agent"
    role = (
        "Records requests to dispatch a neighboring/external force (ambulance, police, K9, or "
        "YASAM) into one of this site's areas, and answers read-only questions about the current "
        "dispatch log. A dispatch's status advances from en_route to arrived automatically once "
        "its computed ETA has elapsed -- never from a human report."
    )
    system_prompt = (
        "You are the neighboring-forces dispatch specialist. You have two tools: "
        "dispatch_neighboring_force records a dispatch request for one force kind (ambulance, "
        "police, k9, or yasam) to a named target area, with the unit count and any note given; "
        "list_neighboring_force_dispatches returns the current dispatch log, optionally filtered "
        "by status ('en_route' or 'arrived'). Neither tool contacts a real ambulance, police unit, "
        "K9 team, or YASAM unit -- each only logs the request and its computed ETA. Report back "
        "plainly what was recorded; never claim a dispatched force has arrived on scene yourself "
        "-- that transition is computed automatically from elapsed time, not something you report."
    )

    def __init__(self, model: str, api_key: str | None = None):
        self.dispatch_store = open_neighboring_force_store(DB_PATH)
        super().__init__(model, api_key)

    @tool(
        "dispatch_neighboring_force",
        "Records a request to dispatch a neighboring/external force (ambulance, police, k9, or "
        "yasam) to a named target area, with the unit count and an optional note. Returns the "
        "recorded request, its en_route status, and computed ETA. Side-effecting and not "
        "idempotent -- running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_neighboring_force(self, kind: str, target_area: str, unit_count: int = 1, note: str = "") -> str:
        kind_norm = kind.strip().lower()
        if kind_norm not in FORCE_BASES:
            return (
                f"Clarification required: unknown force kind '{kind}'. "
                f"Valid kinds: {', '.join(sorted(FORCE_BASES))}."
            )
        if not target_area.strip():
            return "Clarification required: target_area is required."
        if unit_count < 1:
            return "Clarification required: unit_count must be at least 1."

        origin_area = FORCE_BASES[kind_norm]
        eta = eta_seconds(origin_area, target_area.strip())
        record = self.dispatch_store.dispatch(
            force_kind=kind_norm,
            origin_area=origin_area,
            target_area=target_area.strip(),
            unit_count=unit_count,
            eta_seconds=eta,
            note=note.strip(),
        )
        return (
            f"{kind_norm} dispatch recorded, en route to {record['target_area']}, "
            f"ETA={record['eta_seconds']}s (request {record['request_id']})."
        )

    @tool(
        "list_neighboring_force_dispatches",
        "Returns the current neighboring-force dispatch log (request id, kind, unit count, "
        "origin/target area, status, ETA, dispatched-at), optionally filtered to one status "
        "('en_route' or 'arrived'). A dispatch already shows 'arrived' once its ETA has elapsed, "
        "with no separate report needed for that transition.",
        side_effecting=False,
    )
    def list_neighboring_force_dispatches(self, status: str = "") -> str:
        cleaned = status.strip().lower()
        rows = self.dispatch_store.list_dispatches(status=cleaned or None)
        if not rows:
            return "No neighboring-force dispatches recorded."
        lines = [f"Neighboring-force dispatches ({len(rows)}):"]
        for row in rows:
            lines.append(
                f"- [{row['request_id']}] {row['force_kind']} x{row['unit_count']}: "
                f"{row['origin_area']} -> {row['target_area']} ({row['status'].upper()}, "
                f"ETA {row['eta_seconds']}s, dispatched {row['dispatched_at']})"
            )
        return "\n".join(lines)


AGENTS = [
    AgentSpec(cls=ResponseTeamRosterAgent, tier="sub"),
    AgentSpec(cls=ResponseTeamSurveillanceAgent, tier="sub"),
    AgentSpec(cls=NeighboringForcesAgent, tier="sub"),
]


# == Protocols (authored for SEC_001; docs/responce_improve.md) ==============
#
# All seven: approval_flag=False, commander_only=False, requires_confirmation=False.

PROTOCOLS = [
    Protocol(
        name="record_attendance",
        description=(
            "Applies when a response-team member reports their own attendance/availability "
            "status for the current or an upcoming period -- available, or unavailable with a "
            "reason and, once known, a day count. Does not apply to a member reporting their "
            "current location while still on duty (use report_team_movement for that), and does "
            "not apply to a commander asking about the team's overall roster (use "
            "query_situational_picture for that)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="Confirmation that the member's attendance response was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="update_camera_status",
        description=(
            "Applies when a technician or operator reports a camera's own operating condition -- "
            "offline, degraded, back online, or a physically cut communications cable -- for one "
            "or more named camera identifiers. Does not apply to what a camera shows about a "
            "hostile or suspicious event (use report_security_incident for that). Record the "
            "physical observation reported (what was seen); any stated cause or suspicion from "
            "the reporter is the reporter's own claim, never recorded as fact."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("update_camera_status",),
        expected_success_output="Confirmation that each reported camera's status/observation was recorded.",
        criticality=CriticalityLevel.MEDIUM,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="report_security_incident",
        description=(
            "Applies to a report of an unconfirmed hostile, suspicious, or security-relevant "
            "event -- a suspicious vehicle or person, gunfire, a sighted armed suspect, an "
            "intrusion, or a breach in the perimeter fence -- confirmed or monitored, when "
            "useful, by tasking a drone to the reported area for recon. A drone dispatch is not "
            "always required: an already-handled, no-further-risk report (e.g. a small fire "
            "that is already out, with no firefighter kind involved) is an information event "
            "only, with no dispatch. Does not apply to a plain camera/sensor equipment-status "
            "observation with no security implication (use update_camera_status for that), and "
            "does not apply to a request to actually send an external force (use "
            "dispatch_neighboring_force for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output=(
            "Confirmation of drone dispatch to the reported area (callsign, ETA, mission ID) "
            "when a dispatch was needed, or a plain acknowledgement that the report was logged "
            "when it was not."
        ),
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_neighboring_force",
        description=(
            "Applies when a report requires dispatching a real neighboring/external force -- "
            "ambulance, police, K9, or YASAM (YAMAG folds into YASAM) -- to an area, most "
            "commonly a casualty needing medical response, a confirmed threat needing a "
            "police/YASAM response, or a search needing a K9 unit. Does not apply to a mere "
            "report or recon request with no dispatch decision yet (use "
            "report_security_incident first for that)."
        ),
        participating_agents=("neighboring_forces_agent",),
        approved_tools=("dispatch_neighboring_force",),
        expected_success_output="Confirmation that the requested neighboring force was dispatched, en route, with its ETA.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="report_team_movement",
        description=(
            "Applies when a team member reports their own movement or current position -- e.g. "
            "travelling to or arriving at an area -- while still on duty. Does not apply to a "
            "member reporting they will be unavailable (use record_attendance for that)."
        ),
        participating_agents=("roster_agent",),
        approved_tools=("report_team_movement",),
        expected_success_output="Confirmation that the team member's current area was recorded.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_situational_picture",
        description=(
            "Applies when someone asks for a combined, current snapshot spanning any of the "
            "team's roster/attendance, the camera picture, or neighboring-force dispatch status "
            "-- e.g. 'who's missing tonight and what's the camera status', or 'anything moving, "
            "and where are the responding forces'. Does not apply to a retrospective, "
            "end-to-end summary of a closed or ongoing incident (use query_incident_summary for "
            "that)."
        ),
        participating_agents=("roster_agent", "surveillance_agent", "neighboring_forces_agent"),
        approved_tools=("report_team_availability", "get_surveillance_overview", "list_neighboring_force_dispatches"),
        expected_success_output="One combined report covering whichever of roster, camera, and dispatch status was asked about.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_incident_summary",
        description=(
            "Applies when a commander asks for a retrospective, end-to-end summary of an "
            "incident already underway or closed -- a timeline, which reports turned out to be "
            "false alarms, casualty/roster status, or a message to relay to residents. Does not "
            "apply to a question about the current, live state of the team, cameras, or "
            "dispatches (use query_situational_picture for that)."
        ),
        participating_agents=("history_agent",),
        approved_tools=(),
        expected_success_output="A faithful, chronological summary of the incident drawn only from recorded events.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
]


EVENT_TYPES = [
    "attendance",
    "camera_status",
    "security_incident",
    "force_dispatch",
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
        "YASAM) to an area."
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
    "team_movement": ("area",),
}


# == Profile-load provisioning (docs/responce_improve.md) ====================


def _ensure_operational_seed_data() -> None:
    """Create-if-missing cameras/drones; open today's attendance cycle if
    none exists yet (otherwise SEC_001's absence reports fail the
    daily-cycle rule). Never overwrites an existing row -- the same "create
    if missing, never touch if present" idiom
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
        deadline = now + timedelta(hours=1)
        roster.open_cycle(now.date().isoformat(), now.isoformat(), deadline.isoformat())


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
                "step": 1, "chat": "response_team", "sender_identity": "eli_response_team", "timestamp": "2026-09-06T07:30:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.eli_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step1.text"),
            },
            {
                "step": 2, "chat": "cameras", "sender_identity": "yossi_technician", "timestamp": "2026-09-06T08:15:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step2.text"),
            },
            {
                "step": 3, "chat": "external_forces", "sender_identity": "sdemot_security_coordinator", "timestamp": "2026-09-06T12:00:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.sdemot_security_coordinator"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step3.text"),
            },
            {
                "step": 4, "chat": "response_team", "sender_identity": "danny_response_team", "timestamp": "2026-09-06T16:45:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.danny_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step4.text"),
            },
            {
                "step": 5, "chat": "commander_dm", "sender_identity": "site_security_officer", "timestamp": "2026-09-06T19:00:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step5.text"),
            },
            {
                "step": 6, "chat": "response_team", "sender_identity": "michael_response_team", "timestamp": "2026-09-07T06:30:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.michael_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step6.text"),
            },
            {
                "step": 7, "chat": "cameras", "sender_identity": "yossi_technician", "timestamp": "2026-09-07T08:00:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step7.text"),
            },
            {
                "step": 8, "chat": "external_forces", "sender_identity": "sdemot_security_coordinator", "timestamp": "2026-09-07T14:20:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.sdemot_security_coordinator"),
                "text": _catalog_text("response_team.simulation.sec001.phase1.step8.text"),
            },
            {
                "step": 9, "chat": "commander_dm", "sender_identity": "site_security_officer", "timestamp": "2026-09-07T21:00:00Z",
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
                "step": 1, "chat": "cameras", "sender_identity": "yossi_technician", "timestamp": "2026-09-08T07:15:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step1.text"),
            },
            {
                "step": 2, "chat": "external_forces", "sender_identity": "police_duty_officer", "timestamp": "2026-09-08T07:45:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.police_duty_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step2.text"),
            },
            {
                "step": 3, "chat": "response_team", "sender_identity": "yuval_response_team", "timestamp": "2026-09-08T08:00:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yuval_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step3.text"),
            },
            {
                "step": 4, "chat": "commander_dm", "sender_identity": "site_security_officer", "timestamp": "2026-09-08T08:10:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step4.text"),
            },
            {
                "step": 5, "chat": "cameras", "sender_identity": "yossi_technician", "timestamp": "2026-09-08T08:25:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step5.text"),
            },
            {
                "step": 6, "chat": "external_forces", "sender_identity": "patrol_unit_40", "timestamp": "2026-09-08T08:30:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.patrol_unit_40"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step6.text"),
            },
            {
                "step": 7, "chat": "commander_dm", "sender_identity": "site_security_officer", "timestamp": "2026-09-08T08:32:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step7.text"),
            },
            {
                "step": 8, "chat": "response_team", "sender_identity": "gil_response_team", "timestamp": "2026-09-08T08:40:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.gil_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase2.step8.text"),
            },
            {
                "step": 9, "chat": "response_team", "sender_identity": "gil_response_team", "timestamp": "2026-09-08T08:45:00Z",
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
                "step": 1, "chat": "response_team", "sender_identity": "resident_avraham", "timestamp": "2026-09-08T08:50:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.resident_avraham"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step1.text"),
            },
            {
                "step": 2, "chat": "response_team", "sender_identity": "dan_response_team", "timestamp": "2026-09-08T08:52:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.dan_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step2.text"),
            },
            {
                "step": 3, "chat": "external_forces", "sender_identity": "mda_dispatch", "timestamp": "2026-09-08T08:53:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.mda_dispatch"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step3.text"),
            },
            {
                "step": 4, "chat": "commander_dm", "sender_identity": "site_security_officer", "timestamp": "2026-09-08T08:55:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step4.text"),
            },
            {
                "step": 5, "chat": "external_forces", "sender_identity": "police_patrol", "timestamp": "2026-09-08T09:00:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.police_patrol"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step5.text"),
            },
            {
                "step": 6, "chat": "response_team", "sender_identity": "gil_response_team", "timestamp": "2026-09-08T09:05:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.gil_response_team"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step6.text"),
            },
            {
                "step": 7, "chat": "cameras", "sender_identity": "yossi_technician", "timestamp": "2026-09-08T09:12:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yossi_technician"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step7.text"),
            },
            {
                "step": 8, "chat": "external_forces", "sender_identity": "yasam_commander", "timestamp": "2026-09-08T09:15:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yasam_commander"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step8.text"),
            },
            {
                "step": 9, "chat": "external_forces", "sender_identity": "yasam_commander", "timestamp": "2026-09-08T09:25:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.yasam_commander"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step9.text"),
            },
            {
                "step": 10, "chat": "commander_dm", "sender_identity": "site_security_officer", "timestamp": "2026-09-08T09:40:00Z",
                "sender_name": _catalog_text("response_team.simulation.sec001.persona.site_security_officer"),
                "text": _catalog_text("response_team.simulation.sec001.phase3.step10.text"),
            },
        ],
    },
    ),
]
