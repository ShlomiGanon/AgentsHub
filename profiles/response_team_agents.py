"""Response Team specialist agents and resource-unavailable copy."""

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

import profiles.response_team as _facade
globals().update({name: getattr(_facade, name) for name in dir(_facade) if not name.startswith("__")})

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
        self.roster_store = open_response_team_roster_store(_facade.DB_PATH)

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

    @tool(
        "dispatch_squad",
        "Dispatches this site's own response-team roster to a named target area. This is not a "
        "neighboring/external force -- ambulance, police, K9, and YASAM go through "
        "dispatch_neighboring_force instead. unit_count defaults to 1. Side-effecting and not "
        "idempotent.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_squad(self, target_area: str, unit_count: int = 1, note: str = "") -> str:
        return self.dispatch_neighboring_force(
            kind=SQUAD_KIND, target_area=target_area, unit_count=unit_count, note=note,
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


_ENGLISH_REASON_KEYS = {
    "No ready drones available in fleet for immediate dispatch.": "response_team.resource_unavailable.drone_reason",
    "no active camera covering the area": "response_team.resource_unavailable.camera_no_cover",
}


def _localize_unavailable_reason(reason: str) -> str:
    """Map known English persistence/tool reasons onto catalog copy so reporters never see them."""

    cleaned = (reason or "").strip()
    key = _ENGLISH_REASON_KEYS.get(cleaned)
    if key:
        return _catalog_text(key)
    if cleaned.startswith("Requested drone ") and "is not currently available" in cleaned:
        return _catalog_text("response_team.resource_unavailable.drone_specific_reason")
    return cleaned


def _describe_resource_unavailable(resource_kind: str, area: str, reason: str, registry) -> tuple[str, str]:
    resource_label = _RESOURCE_KIND_LABELS.get(resource_kind, resource_kind)
    area_label = _AREA_LABELS.get(area, area)
    fact = _catalog_text(
        "response_team.resource_unavailable.fact",
        resource=resource_label,
        area=area_label,
        reason=_localize_unavailable_reason(reason),
    )
    alternatives = _find_resource_alternatives(area, registry)
    return fact, alternatives


RESOURCE_UNAVAILABLE_DESCRIPTION = _describe_resource_unavailable


AGENTS = [
    AgentSpec(cls=ResponseTeamRosterAgent, tier="sub"),
    AgentSpec(cls=ResponseTeamSurveillanceAgent, tier="sub"),
    AgentSpec(cls=NeighboringForcesAgent, tier="sub"),
]
