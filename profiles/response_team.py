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
OPTIMIZATION_POLICY = OptimizationPolicy(operational_decision_mode="merged", final_assessment_mode="low_risk_merged")


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
        self.incident_store = open_incident_responder_store(self.status_db_path)
        Agent.__init__(self, model, api_key)

    @tool(
        "report_team_movement",
        "Records a team member's own current area -- e.g. travelling to or arriving at an area "
        "while still on duty. Side-effecting and idempotent -- recording the same area twice for "
        "the same member leaves one current value. This alone does not link the member to any "
        "incident -- call join_incident_response separately when the report also indicates they "
        "are responding to one.",
        side_effecting=True,
        idempotent=True,
    )
    def report_team_movement(self, area: str = "", member_identity: str = "") -> str:
        identity = (member_identity or get_authenticated_request_identity() or "").strip()
        if not identity:
            return failed_tool_result("The movement report was not stored: authenticated requester identity is unavailable.")
        if not area.strip():
            return failed_tool_result("Clarification required: area is required.")

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(member["telegram_identity"] == identity for member in approved_members):
            return failed_tool_result("The movement report was not stored: requester is not an approved roster member.")

        try:
            updated = self.status_store.set_current_area(identity, area.strip())
        except TeamStatusPersistenceError as exc:
            return failed_tool_result(f"The movement report was not stored: {exc}")

        return f"{updated['full_name']}'s current area was recorded as '{updated['current_area']}'."

    @tool(
        "join_incident_response",
        "Links the caller to the one specific real incident currently on record for `area`, when "
        "exactly one exists -- use when a member's report clearly indicates they are responding "
        "to, heading to, or dispatched to an incident there, not merely stationed or passing "
        "through the area. Never links on area alone: if no recent incident is on record, or more "
        "than one is, nothing is linked and a plain explanation is returned instead of a guess. "
        "Automatically closes any other incident the caller was previously linked to "
        "(reassignment). Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def join_incident_response(self, area: str = "", member_identity: str = "") -> str:
        identity = (member_identity or get_authenticated_request_identity() or "").strip()
        if not identity:
            return "Not linked: authenticated requester identity is unavailable."
        if not area.strip():
            return failed_tool_result("Clarification required: area is required.")

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(member["telegram_identity"] == identity for member in approved_members):
            return "Not linked: requester is not an approved roster member."

        event, clarification = self.incident_store.resolve_single_candidate(area.strip())
        if event is None:
            return f"Not linked: {clarification}"
        self.incident_store.join(event["event_id"], identity)
        return f"Linked to the incident currently on record for '{area.strip()}'."

    @tool(
        "leave_incident_response",
        "Closes the caller's own current incident link, if any -- use when a member reports "
        "leaving an incident or being reassigned away with no new incident stated. Harmless (not "
        "an error) if the caller had no open link. Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def leave_incident_response(self, member_identity: str = "") -> str:
        identity = (member_identity or get_authenticated_request_identity() or "").strip()
        if not identity:
            return "Not updated: authenticated requester identity is unavailable."
        closed = self.incident_store.leave(identity)
        if closed is None:
            return "No open incident link was found to close."
        return "The caller's incident link was closed."

    @tool(
        "list_incident_responders",
        "Answers 'who else is with me' / 'who is responding' for the one specific real incident "
        "currently on record for `area`, when exactly one exists -- lists only members actually "
        "linked to that incident (via join_incident_response), never members merely recorded in "
        "the same area. If no recent incident is on record, or more than one is, says so plainly "
        "instead of guessing. Read-only.",
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

        members_by_identity = {
            member["telegram_identity"]: member["full_name"]
            for member in self.status_store.list_members(approved_only=False)
        }
        names = [members_by_identity.get(link["identity"], link["identity"]) for link in links]
        return f"Currently linked to the incident on record for '{area.strip()}': {', '.join(names)}."


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
    def update_camera_status(
        self,
        camera_id: str = "",
        observation: str = "",
        status: str = "",
        camera_identifier: str = "",
    ) -> str:
        # Some model/tool adapters use the prose-level name `camera_identifier`
        # even though the public protocol field is `camera_id`. Accept both so
        # that a harmless naming variation cannot fail the operational step.
        camera_id = camera_id.strip() or camera_identifier.strip()
        if not camera_id.strip():
            return failed_tool_result("Clarification required: camera_id is required.")
        if not observation.strip():
            return failed_tool_result("Clarification required: observation is required.")
        try:
            updated = self.surveillance_store.update_camera_feed(
                camera_id=camera_id.strip(),
                feed_summary=observation.strip(),
                status=status.strip().lower() or None,
            )
        except SurveillancePersistenceError as exc:
            return failed_tool_result(f"Camera status update failed: {exc}")
        return (
            f"Camera '{updated['camera_id']}' status recorded.\n"
            f"- Status: {updated['status'].upper()}\n"
            f"- Observation: {updated['feed_summary']}\n"
            f"- Last updated: {updated['last_updated']}"
        )

    @tool(
        "recall_drone",
        f"Recalls one active drone to {DRONES_WAREHOUSE}. "
        "drone_or_mission_id is optional only when exactly one mission is active. "
        "To recall every active drone, call return_all_drones_to_base.",
        side_effecting=True,
        idempotent=True,
    )
    def recall_drone(self, drone_or_mission_id: str = "") -> str:
        return self.return_drone_to_base(drone_or_mission_id)


class NeighboringForcesAgent(_NeighboringForcesAgentBase):
    """Thin profile subclass of the shared `agents.neighboring_forces_agent.NeighboringForcesAgent`
    (docs/Admin_Tables_Plan.md section 3.3) -- own DB, own force kinds/pool/busy-window, own ETA
    matrix, plus one addition the shared base doesn't know about: `kind="squad"` dispatches the
    response team's own roster (not a real external force) through the same tool, checked
    against the roster's live availability instead of `FORCE_POOL_SIZE`. `_resolve_kind`/
    `_check_capacity`/`_capacity_shortage_text` are overridden only for that one extra kind;
    every other kind uses the shared base's own default behavior unchanged."""

    dispatch_db_path = DB_PATH
    force_bases = FORCE_BASES
    force_pool_size = FORCE_POOL_SIZE
    force_busy_seconds = FORCE_BUSY_SECONDS
    eta_fn = staticmethod(eta_seconds)

    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model, api_key)
        self.roster_store = open_response_team_roster_store(DB_PATH)

    def _valid_kinds(self) -> "tuple[str, ...]":
        return tuple(sorted((*self.force_bases, SQUAD_KIND)))

    def _resolve_kind(self, kind_norm: str) -> "tuple[str, str] | None":
        if kind_norm == SQUAD_KIND:
            return SQUAD_ORIGIN_AREA, "squad_member"
        return super()._resolve_kind(kind_norm)

    def _check_capacity(self, kind_norm: str, unit_count: int) -> "tuple[bool, int]":
        if kind_norm == SQUAD_KIND:
            now_iso = datetime.now(timezone.utc).isoformat()
            available = sum(
                1 for entry in self.roster_store.availability_snapshot(now_iso)
                if entry["availability"] == "available"
            )
            return available >= unit_count, available
        return super()._check_capacity(kind_norm, unit_count)

    def _capacity_shortage_text(self, kind_norm: str, remaining: int, unit_count: int) -> str:
        if kind_norm == SQUAD_KIND:
            return _catalog_text(
                "response_team.resource_unavailable.squad_reason", available=remaining, unit_count=unit_count,
            )
        return _catalog_text(
            "response_team.resource_unavailable.force_reason",
            remaining=remaining, pool_size=self.force_pool_size,
            resource=_RESOURCE_KIND_LABELS.get(kind_norm, kind_norm), unit_count=unit_count,
        )


# == Resource-unavailable description (orchestrator/flows.py's shared mechanism) ============
#
# Registered below as RESOURCE_UNAVAILABLE_DESCRIPTION. Two responsibilities, both localized
# here (core never composes resource/area names itself, per this module's own Hebrew-only-in-
# messages rule -- _catalog_text is still the one place this module reads Hebrew):
#   - the reporter-facing fact sentence (resource + area + reason, translated);
#   - the commander-facing alternatives: the SAME full picture of everything else that could
#     cover the area right now -- cameras, ready drones, available roster members, and force
#     kinds with remaining capacity -- not just alternatives within one resource's own category.

_RESOURCE_KIND_LABELS = {
    "drone": _catalog_text("response_team.resource_kind.drone"),
    "camera": _catalog_text("response_team.resource_kind.camera"),
    "squad_member": _catalog_text("response_team.resource_kind.squad_member"),
    "police": _catalog_text("response_team.resource_kind.police"),
    "ambulance": _catalog_text("response_team.resource_kind.ambulance"),
    "k9": _catalog_text("response_team.resource_kind.k9"),
    "yasam": _catalog_text("response_team.resource_kind.yasam"),
}

_AREA_LABELS = {
    "west_gate": _catalog_text("response_team.area.west_gate"),
    "east_gate": _catalog_text("response_team.area.east_gate"),
    "east_fence": _catalog_text("response_team.area.east_fence"),
    "east_orchards": _catalog_text("response_team.area.east_orchards"),
    "expansion_neighborhood": _catalog_text("response_team.area.expansion_neighborhood"),
    "old_public_building": _catalog_text("response_team.area.old_public_building"),
    "south_corner": _catalog_text("response_team.area.south_corner"),
    "access_road": _catalog_text("response_team.area.access_road"),
    "drones_warehouse": _catalog_text("response_team.area.drones_warehouse"),
}


def _force_remaining_capacity(neighboring_forces_agent) -> dict[str, int]:
    """Remaining capacity per external force kind, using the SAME busy-window definition
    `dispatch_neighboring_force`'s own capacity check uses (fix d) -- a dispatched unit stays
    busy for FORCE_BUSY_SECONDS regardless of en_route/arrived status, so this must never be
    computed from status="en_route" alone or the two would disagree."""

    busy_since = (datetime.now(timezone.utc) - timedelta(seconds=FORCE_BUSY_SECONDS)).isoformat()
    busy_by_kind: dict[str, int] = {}
    for dispatch in neighboring_forces_agent.dispatch_store.list_dispatches():
        if dispatch["dispatched_at"] > busy_since:
            busy_by_kind[dispatch["force_kind"]] = busy_by_kind.get(dispatch["force_kind"], 0) + dispatch["unit_count"]
    return {kind: max(FORCE_POOL_SIZE - busy_by_kind.get(kind, 0), 0) for kind in FORCE_BASES}


def _find_resource_alternatives(area: str, registry) -> str:
    parts: list[str] = []

    surveillance_agent = registry.get("surveillance_agent")
    cameras = surveillance_agent.surveillance_store.list_cameras(area=area)
    area_label = _AREA_LABELS.get(area, area)
    if cameras:
        camera_text = ", ".join(f"{camera['camera_id']} ({camera['status']})" for camera in cameras)
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.cameras_covering", area=area_label, cameras=camera_text))
    else:
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.no_cameras", area=area_label))

    ready_drones = surveillance_agent.surveillance_store.list_drones(status="ready")
    parts.append(_catalog_text("response_team.resource_unavailable.alternatives.ready_drones", count=len(ready_drones)))

    roster_agent = registry.get("roster_agent")
    now_iso = datetime.now(timezone.utc).isoformat()
    available_members = [
        entry["full_name"] for entry in roster_agent.status_store.availability_snapshot(now_iso)
        if entry["availability"] == "available"
    ]
    if available_members:
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.available_members", members=", ".join(available_members)))
    else:
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.no_members"))

    neighboring_forces_agent = registry.get("neighboring_forces_agent")
    remaining_by_kind = _force_remaining_capacity(neighboring_forces_agent)
    force_lines = [
        f"{_RESOURCE_KIND_LABELS[kind]} ({remaining_by_kind[kind]}/{FORCE_POOL_SIZE})" for kind in sorted(FORCE_BASES)
    ]
    parts.append(_catalog_text("response_team.resource_unavailable.alternatives.forces", forces=", ".join(force_lines)))

    return "; ".join(parts)


def _describe_resource_unavailable(resource_kind: str, area: str, reason: str, registry) -> tuple[str, str]:
    resource_label = _RESOURCE_KIND_LABELS.get(resource_kind, resource_kind)
    area_label = _AREA_LABELS.get(area, area)
    fact = _catalog_text("response_team.resource_unavailable.fact", resource=resource_label, area=area_label, reason=reason)
    alternatives = _find_resource_alternatives(area, registry)
    return fact, alternatives


RESOURCE_UNAVAILABLE_DESCRIPTION = _describe_resource_unavailable


AGENTS = [
    AgentSpec(cls=ResponseTeamRosterAgent, tier="sub"),
    AgentSpec(cls=ResponseTeamSurveillanceAgent, tier="sub"),
    AgentSpec(cls=NeighboringForcesAgent, tier="sub"),
]


# == Direct-tool step binders (Phase A, docs/responce_improve.md) ===========
#
# Each skips formulate_tasks/task_rewrite by binding a protocol's step(s) straight from the
# event's own extracted fields, which are always already model-produced (classify_intent's
# extraction), never re-derived from raw text by a local heuristic. A binder that cannot
# confidently produce every parameter leaves the corresponding EVENT_DATA_FIELDS name(s) in
# `required_event_fields` instead of guessing -- the ordinary missing-fields check
# (protocols/executor.py::_missing_event_fields) then raises the same event_data hold any
# other protocol would, before this step ever executes.
#
# Most of these bind a `kind="direct_tool"` step (protocols/executor.py::_execute_direct_tool_step):
# no specialist-agent LLM turn for the tool call itself, since every parameter is already known.
# `_bind_update_camera_status` instead binds a normal `kind="agent"` step once entities/description
# are present: camera_id is deterministic (from `entities`), but the resulting status is a genuine
# judgment call from the free-text report, so the specialist agent decides and calls the tool
# itself rather than a keyword heuristic pre-deciding it.

def _as_aware_iso(value: str) -> str:
    """A persisted event timestamp is stored without an explicit offset but is always UTC
    (config/environment.py's own timestamp convention) — agents/team_status_agent.py's
    `_aware_datetime` rejects a naive string outright, so make it explicit before handing it
    to a tool, the same way a real model call would when it reformats a timestamp itself."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _bind_record_attendance(event: dict) -> tuple[Step, ...]:
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
            agent_name="roster_agent",
            task_text="Record the reporter's own attendance/availability response, bound directly from the event's extracted fields.",
            allowed_tools=("record_attendance_response",),
            step_id="1",
            required_event_fields=required,
            kind="direct_tool",
            direct_tool_name="record_attendance_response",
            direct_tool_kwargs=kwargs,
        ),
    )


def _bind_update_camera_status(event: dict) -> tuple[Step, ...]:
    entities = event.get("entities") or []
    description = (event.get("description") or "").strip()
    missing = tuple(name for name in ("entities", "description") if not event.get(name))
    if missing:
        return (
            Step(
                agent_name="surveillance_agent",
                task_text="Record the reported camera(s) status, bound directly from the event's extracted fields.",
                allowed_tools=("update_camera_status",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="update_camera_status",
                direct_tool_kwargs={},
            ),
        )
    return tuple(
        Step(
            agent_name="surveillance_agent",
            task_text=(
                f"Camera {camera_id} was reported on. Determine its resulting status (active, offline, "
                f"or degraded) from the report below, and call update_camera_status for {camera_id} with "
                f"that status and a short observation.\n\nReport: {description}"
            ),
            allowed_tools=("update_camera_status",),
            step_id=str(index + 1),
        )
        for index, camera_id in enumerate(entities)
    )


# A narrow, low-stakes judgment call (decide whether a movement report also indicates incident
# response, then call up to three known tools) never needs the agent's default reasoning budget --
# same mechanism SurveillanceAgent.process already uses for its own tool-turn-plus-summary calls.
_FAST_JUDGMENT_POLICY = InvocationPolicy(max_output_tokens=400, reasoning_effort="none")


def _bind_report_team_movement(event: dict) -> tuple[Step, ...]:
    area = (event.get("area") or "").strip()
    description = (event.get("description") or "").strip()
    missing = tuple(name for name in ("area",) if not event.get(name))
    if missing:
        return (
            Step(
                agent_name="roster_agent",
                task_text="Record the reporter's own current area, bound directly from the event's extracted fields.",
                allowed_tools=("report_team_movement",),
                step_id="1",
                required_event_fields=missing,
                kind="direct_tool",
                direct_tool_name="report_team_movement",
                direct_tool_kwargs={},
            ),
        )
    # Whether this movement is a response to a specific incident is a genuine judgment call
    # from the free-text report, the same class of decision `_bind_update_camera_status` makes
    # for a camera's resulting status -- so the specialist agent decides and calls the relevant
    # tool(s) itself rather than a keyword heuristic pre-deciding it.
    return (
        Step(
            agent_name="roster_agent",
            task_text=(
                f"The reporter's own current area was reported as '{area}'. Call report_team_movement "
                f"with that area. Then, from the report below, decide: does it clearly say the reporter "
                f"is responding to, heading to, or dispatched to a specific incident at that area (not "
                f"merely stationed or passing through)? If so, also call join_incident_response for the "
                f"same area. If the report also asks who else is with/responding, also call "
                f"list_incident_responders for the same area and include its answer in your reply.\n\n"
                f"Report: {description}"
            ),
            allowed_tools=("report_team_movement", "join_incident_response", "list_incident_responders"),
            step_id="1",
            invocation_policy=_FAST_JUDGMENT_POLICY,
        ),
    )


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
        # Phase A: parameters bound straight from extracted fields, no model call for the
        # step itself. Recording a report exactly as given is correct behavior, not something
        # that needs a model's insight/judgment.
        needs_insight=False,
        direct_tool_binder=_bind_record_attendance,
        direct_lane_eligible=True,
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
        needs_insight=False,
        direct_tool_binder=_bind_update_camera_status,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="report_security_incident",
        description=(
            "Applies to a report of an unconfirmed hostile, suspicious, or still-relevant "
            "security event -- a suspicious vehicle or person, gunfire, a sighted armed suspect, "
            "an intrusion, or a breach in the perimeter fence -- confirmed or monitored by "
            "tasking a drone to the reported area for recon. Does not apply when the report "
            "itself says the situation is already handled, resolved, or presents no further risk "
            "(use log_security_observation for that -- never dispatch a drone for an "
            "already-handled report). Does not apply to a plain camera/sensor equipment-status "
            "observation with no security implication (use update_camera_status for that), and "
            "does not apply to a request to actually send an external force (use "
            "dispatch_neighboring_force for that)."
        ),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output="Confirmation of drone dispatch to the reported area (callsign, ETA, mission ID).",
        criticality=CriticalityLevel.HIGH,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        # A field/civilian security report can arrive in any group, not only the
        # camera-ops channel this protocol's own agent (surveillance_agent) is bound
        # to -- keep it selectable everywhere (orchestrator/group_routing.py).
        safety_critical=True,
    ),
    Protocol(
        # Split from report_security_incident (over-dispatch fix): an already-handled report has
        # no dispatch tool available at all here, structurally, not merely a prompt instruction
        # the agent could still disregard -- e.g. a small fire that is already out, with no
        # firefighter kind involved, or a suspicious situation already resolved/cleared.
        name="log_security_observation",
        description=(
            "Applies when a report describes a security-relevant observation that is explicitly "
            "already handled, resolved, or presents no further risk -- e.g. a small fire that is "
            "already out, with no firefighter kind involved, or a suspicious situation that has "
            "already been resolved or cleared. Purely informational: logs the observation: never "
            "dispatches a drone or any other resource. Does not apply to anything still active, "
            "ongoing, or unconfirmed (use report_security_incident for that)."
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
        approved_tools=("report_team_movement", "join_incident_response", "list_incident_responders"),
        expected_success_output=(
            "Confirmation that the team member's current area was recorded; if the report also "
            "indicated they are responding to a specific incident, confirmation they were linked "
            "to it and, if asked, who else is currently linked to that same incident."
        ),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
        needs_insight=False,
        direct_tool_binder=_bind_report_team_movement,
        direct_lane_eligible=True,
    ),
    Protocol(
        name="query_situational_picture",
        description=(
            "Applies when someone asks for a combined, current snapshot spanning any of the "
            "team's roster/attendance, the camera picture, or neighboring-force dispatch status "
            "-- e.g. 'who's missing tonight and what's the camera status', or 'anything moving, "
            "and where are the responding forces'. Also applies when a confused or urgent "
            "message recaps several recent reports (possibly from different chats) and asks to "
            "make sense of them and/or decide where to send the available force -- pull the "
            "actual records rather than trusting the requester's own recap. Does not apply to a "
            "retrospective, end-to-end summary of a closed or ongoing incident (use "
            "query_incident_summary for that)."
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


# == Admin-panel tables (docs/Admin_Tables_Plan.md) ==========================
#
# Every write_fn is a thin wrapper around a store method on this profile's own already-shared
# store classes (persistence/response_team_store.py's ResponseTeamSurveillanceStore/
# NeighboringForceStore, persistence/team_status_store.py's SQLiteTeamStatusPersistence) --
# never a direct SQL statement in this module. Every cascade an edit implies (a drone leaving
# an active mission, an attendance approval stamp) happens inside those store methods, so it's
# identical whether the edit came from the admin panel or (where a live tool exists) a real
# report.


def _drones_list(deps) -> list:
    return deps.registry.get("surveillance_agent").surveillance_store.list_drones()


def _drones_get(deps, drone_id: str):
    return deps.registry.get("surveillance_agent").surveillance_store.get_drone(drone_id)


def _drones_write(deps, row: dict) -> None:
    store = deps.registry.get("surveillance_agent").surveillance_store
    store.admin_update_drone(row["drone_id"], **{k: v for k, v in row.items() if k != "drone_id"})


def _attendance_list(deps) -> list:
    return deps.registry.get("roster_agent").status_store.list_responses()


def _attendance_get(deps, response_id: str):
    return deps.registry.get("roster_agent").status_store.get_response(response_id)


def _attendance_write(deps, row: dict) -> None:
    store = deps.registry.get("roster_agent").status_store
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
        label="Standby Squad Attendance",
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
