"""Response Team specialist agents and resource-unavailable copy."""

from datetime import datetime, timedelta, timezone

from agents import (
    Agent,
    NeighboringForcesAgent as _NeighboringForcesAgentBase,
    SurveillanceAgent,
    TeamStatusAgent,
    failed_tool_result,
    get_authenticated_request_identity,
    tool,
)
from messages import get_current_catalog
from persistence import (
    NeighboringForceStoreError,
    SurveillancePersistenceError,
    TeamStatusPersistenceError,
    open_incident_responder_store,
    open_response_team_roster_store,
    open_response_team_surveillance_store,
)
from profiles.contracts import AgentSpec
from profiles.response_team import (
    AREAS,
    DB_PATH,
    DRONE_LOITER_SECONDS,
    DRONES_WAREHOUSE,
    FORCE_BASES,
    FORCE_BUSY_SECONDS,
    FORCE_POOL_SIZE,
    SQUAD_KIND,
    SQUAD_ORIGIN_AREA,
    TIMEZONE,
    _catalog_text,
    eta_seconds,
)

# A notice quotes the corrected report's description; a long free-text report would bury the
# update itself, so it is shortened to one readable clause.
_NOTICE_DESCRIPTION_LIMIT = 120


def _current_area_label(area: str) -> str:
    """Area label in the request's own catalog language, or the raw value when undeclared."""

    key = f"response_team.area.{area}"
    catalog = get_current_catalog()
    return catalog.text(key) if key in catalog.messages else area


def _release_reference_missing(event_id: str, incident_id: str) -> bool:
    """True when neither id was given, so a release tool must refuse rather than guess."""

    return not ((event_id or "").strip() or (incident_id or "").strip())

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
    response_window_hours = 1

    def __init__(self, model: str, api_key: str | None = None):
        """Open this profile's roster and incident stores, then finish Agent setup."""

        if not self.status_db_path:
            raise TypeError("ResponseTeamRosterAgent requires a class-level status_db_path")
        self.status_store = open_response_team_roster_store(self.status_db_path)
        self.incident_store = open_incident_responder_store(self.status_db_path)
        Agent.__init__(self, model, api_key)

    def _notice_context(self, about_event_id: str, area: str, description: str) -> tuple[str, str]:
        """Prefer the area and description of the event the notice is about, labeled for readers."""

        summary = self.incident_store.event_summary(about_event_id) if about_event_id else None
        if summary:
            area = (summary.get("area") or "").strip() or area
            description = (summary.get("description") or "").strip() or description
        description = " ".join(description.split())
        if len(description) > _NOTICE_DESCRIPTION_LIMIT:
            description = description[: _NOTICE_DESCRIPTION_LIMIT - 1].rstrip() + "…"
        return (_current_area_label(area) if area else ""), description

    def _field_presence(self, entry: dict) -> str | None:
        """Area label for a member linked to an open incident or with a reported current area."""

        area = ""
        link = self.incident_store.find_open_link(entry["telegram_identity"])
        if link is not None:
            # Read-only: the linked incident's area, falling back to the member's own report.
            summary = self.incident_store.event_summary(link["event_id"]) or {}
            area = (summary.get("area") or "").strip()
        area = area or (entry.get("current_area") or "").strip()
        return _current_area_label(area) if area else None

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
        """Record the caller's current area without linking them to an incident."""

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
        "(reassignment). When the task names the incident id this report belongs to, pass it as "
        "incident_id and that exact incident is used. Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def join_incident_response(self, area: str = "", member_identity: str = "", incident_id: str = "") -> str:
        """Link the caller to the given incident, or to the single incident on record for `area`."""

        identity = (member_identity or get_authenticated_request_identity() or "").strip()
        if not identity:
            return "Not linked: authenticated requester identity is unavailable."
        if not area.strip() and not incident_id.strip():
            return failed_tool_result("Clarification required: area is required.")

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(member["telegram_identity"] == identity for member in approved_members):
            return "Not linked: requester is not an approved roster member."

        event, clarification = self._incident_for(area, incident_id)
        if event is None:
            return f"Not linked: {clarification}"
        self.incident_store.join(event["event_id"], identity)
        return f"Linked to the incident on record for '{(event.get('area') or area).strip()}'."

    def _incident_for(self, area: str, incident_id: str) -> tuple[dict | None, str | None]:
        """The incident a report was already linked to, else the single one on record for `area`.

        A report the orchestrator tied to a specific incident must not be re-resolved by area,
        where two open incidents in one sector would make the answer 'unclear'."""

        if incident_id.strip():
            summary = self.incident_store.event_summary(incident_id.strip())
            if summary is not None:
                return summary, None
        return self.incident_store.resolve_single_candidate(area.strip())

    @tool(
        "leave_incident_response",
        "Closes the caller's own current incident link, if any -- use when a member reports "
        "leaving an incident or being reassigned away with no new incident stated. Harmless (not "
        "an error) if the caller had no open link. Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def leave_incident_response(self, member_identity: str = "") -> str:
        """Close the caller's current incident link, if they have one."""

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
        "instead of guessing. When the task names the incident id this report belongs to, pass it "
        "as incident_id. Read-only.",
        side_effecting=False,
    )
    def list_incident_responders(self, area: str = "", incident_id: str = "") -> str:
        """List members linked to the given incident, or to the single incident on record for `area`."""

        if not area.strip() and not incident_id.strip():
            return failed_tool_result("Clarification required: area is required.")

        event, clarification = self._incident_for(area, incident_id)
        if event is None:
            return clarification
        area = (event.get("area") or area).strip()

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
    `get_surveillance_overview` unchanged; adds `update_camera_status`,
    `recall_drone` (recalling to `DRONES_WAREHOUSE`), and `end_drone_missions`
    (releasing the missions of one event or incident)."""

    camera_catalog_stem = "response_team"
    area_catalog_stem = "response_team"
    declared_areas = tuple(AREAS)
    surveillance_db_path = DB_PATH
    system_prompt = SurveillanceAgent.system_prompt + (
        " When the user writes in Hebrew, the entire answer must be in simple, natural Hebrew. "
        "Translate operational statuses into everyday Hebrew: "
        f"active means {_catalog_text('response_team.camera_status.active')}, "
        f"degraded means {_catalog_text('response_team.camera_status.degraded')}, "
        f"and offline means {_catalog_text('response_team.camera_status.offline')}. "
        "Keep camera IDs unchanged. Do not use English headings, English status words, "
        "asterisk emphasis, dramatic alerts, or unsolicited recommendations. Start with one plain summary sentence, "
        "then give one short Hebrew line for each camera. "
        "A mast camera or drone camera is temporary coverage, never a fence camera. "
        "A drone shown in flight with a Mission id is on that assignment, not an anomaly."
    )

    def __init__(self, model: str, api_key: str | None = None):
        """Open this profile's surveillance store with its ETA matrix, warehouse home, and loiter window."""

        if not self.surveillance_db_path:
            raise TypeError("ResponseTeamSurveillanceAgent requires a class-level surveillance_db_path")
        self.surveillance_store = open_response_team_surveillance_store(
            self.surveillance_db_path,
            eta_fn=eta_seconds,
            home_area=DRONES_WAREHOUSE,
            mission_loiter_seconds=DRONE_LOITER_SECONDS,
        )
        Agent.__init__(self, model, api_key)

    @tool(
        "end_drone_missions",
        "Ends every active drone mission linked to one event or incident -- for example after its "
        "report was corrected as false or the incident was closed -- and records those drones as "
        f"ready at {DRONES_WAREHOUSE}. Pass event_id and/or incident_id; nothing is ended without one. "
        "outcome is 'completed' (default) or 'aborted'. Side-effecting and idempotent -- a second "
        "call finds nothing left to end.",
        side_effecting=True,
        idempotent=True,
    )
    def end_drone_missions(
        self, event_id: str = "", incident_id: str = "", outcome: str = "completed", reason: str = ""
    ) -> str:
        """End the drone missions linked to an event or incident and return a catalog confirmation."""

        if _release_reference_missing(event_id, incident_id):
            return failed_tool_result(_catalog_text("response_team.release.missing_reference"))
        cleaned_outcome = (outcome or "").strip().lower()
        if cleaned_outcome not in {"completed", "aborted"}:
            cleaned_outcome = "completed"
        try:
            ended = self.surveillance_store.end_missions(
                event_id=(event_id or "").strip() or None,
                incident_id=(incident_id or "").strip() or None,
                outcome=cleaned_outcome,
                reason=reason,
            )
        except SurveillancePersistenceError as exc:
            return failed_tool_result(f"Ending drone missions failed: {exc}")
        if not ended:
            return _catalog_text("response_team.release.no_missions")
        missions = ", ".join(
            f"{mission['callsign']} ({mission['mission_id']}, {_AREA_LABELS.get(mission['target_area'], mission['target_area'])})"
            for mission in ended
        )
        return _catalog_text(
            "response_team.release.missions_ended",
            count=len(ended), missions=missions, home=_AREA_LABELS[DRONES_WAREHOUSE],
        )

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
        """Record one camera's observation and optional status."""

        # Some model/tool adapters use the prose-level name `camera_identifier`

        # even though the public protocol field is `camera_id`. Accept both so
        # that a harmless naming variation cannot fail the operational step.
        from messages.camera_names import resolve_camera_id

        camera_id = resolve_camera_id(
            self.surveillance_store,
            camera_id.strip() or camera_identifier.strip(),
            getattr(self, "camera_catalog_stem", ""),
        )
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
        "log_security_observation",
        "Records an already-handled security observation. Never dispatches a drone or any other resource.",
        side_effecting=False,
    )
    def log_security_observation(self, note: str = "") -> str:
        """Acknowledge an already-handled observation without dispatching anything."""

        return "Observation logged. No resource was dispatched."

    @tool(
        "recall_drone",
        f"Recalls one active drone to {DRONES_WAREHOUSE}. "
        "drone_or_mission_id is optional only when exactly one mission is active. "
        "To recall every active drone, call return_all_drones_to_base.",
        side_effecting=True,
        idempotent=True,
    )
    def recall_drone(self, drone_or_mission_id: str = "") -> str:
        """Recall one active drone to the warehouse via the shared return-to-base tool."""

        return self.return_drone_to_base(drone_or_mission_id)


class NeighboringForcesAgent(_NeighboringForcesAgentBase):
    """Dispatch specialist with this profile's kinds, own-roster `squad` capacity, and event-scoped release."""

    dispatch_db_path = DB_PATH
    force_bases = FORCE_BASES
    force_pool_size = FORCE_POOL_SIZE
    force_busy_seconds = FORCE_BUSY_SECONDS
    eta_fn = staticmethod(eta_seconds)
    # Several reports about one incident must not each send another police or YASAM unit to
    # the same place while the first one is still there or on its way.
    reuse_active_dispatches = True

    def __init__(self, model: str, api_key: str | None = None):
        """Open the roster store used only for the squad-capacity check."""

        super().__init__(model, api_key)
        from profiles import response_team as profile
        # Read DB_PATH from the profile module so test monkeypatches on that facade apply.
        self.roster_store = open_response_team_roster_store(profile.DB_PATH)

    def _valid_kinds(self) -> "tuple[str, ...]":
        """Include the site's own squad kind alongside real external forces."""

        return tuple(sorted((*self.force_bases, SQUAD_KIND)))

    def _resolve_kind(self, kind_norm: str) -> "tuple[str, str] | None":
        """Map squad to its origin area; every other kind uses the shared default."""

        if kind_norm == SQUAD_KIND:
            return SQUAD_ORIGIN_AREA, "squad_member"
        return super()._resolve_kind(kind_norm)

    def _check_capacity(self, kind_norm: str, unit_count: int) -> "tuple[bool, int]":
        """Count live available roster members for squad; use the pool for other kinds."""

        if kind_norm == SQUAD_KIND:
            # A squad call-out reaches every approved member who has not said they are away --
            # the same rule the attendance check uses for who is required -- so members who
            # simply have not answered today's check are called, not counted as missing.
            now_iso = datetime.now(timezone.utc).isoformat()
            available = sum(
                1 for entry in self.roster_store.availability_snapshot(now_iso)
                if entry["availability"] != "unavailable"
            )
            return available >= unit_count, available
        return super()._check_capacity(kind_norm, unit_count)

    def _capacity_shortage_text(self, kind_norm: str, remaining: int, unit_count: int) -> str:
        """Localized shortage text for squad availability or an external-force pool."""

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
        "dispatch_neighboring_force instead. unit_count defaults to 1. event_id and incident_id "
        "are optional links to the report being handled. Side-effecting and not idempotent.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_squad(
        self, target_area: str, unit_count: int = 1, note: str = "", event_id: str = "", incident_id: str = ""
    ) -> str:
        """Dispatch this site's own roster through the shared neighboring-force tool."""

        return self.dispatch_neighboring_force(
            kind=SQUAD_KIND, target_area=target_area, unit_count=unit_count, note=note,
            event_id=event_id, incident_id=incident_id,
        )

    @tool(
        "release_force_dispatches",
        "Releases (stands down) every force dispatch -- neighboring forces and this site's own "
        "squad -- linked to one event or incident, for example after its report was corrected as "
        "false or the incident was closed. Released dispatches stay in the log as RELEASED and no "
        "longer count against capacity. Pass event_id and/or incident_id; nothing is released "
        "without one. Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def release_force_dispatches(self, event_id: str = "", incident_id: str = "", reason: str = "") -> str:
        """Release the dispatches linked to an event or incident and return a catalog confirmation."""

        if _release_reference_missing(event_id, incident_id):
            return failed_tool_result(_catalog_text("response_team.release.missing_reference"))
        try:
            released = self.dispatch_store.release_dispatches(
                event_id=(event_id or "").strip() or None,
                incident_id=(incident_id or "").strip() or None,
                reason=reason,
            )
        except NeighboringForceStoreError as exc:
            return failed_tool_result(f"Releasing force dispatches failed: {exc}")
        if not released:
            return _catalog_text("response_team.release.no_dispatches")
        dispatches = ", ".join(
            f"{_force_kind_label(row['force_kind'])} x{row['unit_count']} -> "
            f"{_AREA_LABELS.get(row['target_area'], row['target_area'])} ({row['request_id']})"
            for row in released
        )
        return _catalog_text("response_team.release.dispatches_released", count=len(released), dispatches=dispatches)


# -- Resource-unavailable copy ------------------------------------------------
# Core never composes resource or area names; this module localizes both the
# reporter-facing fact and the commander-facing alternatives list.

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


def _force_kind_label(force_kind: str) -> str:
    """Display label for a dispatch's force kind; the own squad is labeled as squad members."""

    return _RESOURCE_KIND_LABELS.get("squad_member" if force_kind == SQUAD_KIND else force_kind, force_kind)


def _force_remaining_capacity(neighboring_forces_agent) -> dict[str, int]:
    """Remaining units per force kind inside the same busy window the dispatch tool uses."""

    busy_since = (datetime.now(timezone.utc) - timedelta(seconds=FORCE_BUSY_SECONDS)).isoformat()
    busy_by_kind: dict[str, int] = {}
    for dispatch in neighboring_forces_agent.dispatch_store.list_dispatches():
        # A released dispatch no longer occupies its units, exactly as in the dispatch tool.
        if dispatch["dispatched_at"] > busy_since and not dispatch.get("released_at"):
            busy_by_kind[dispatch["force_kind"]] = busy_by_kind.get(dispatch["force_kind"], 0) + dispatch["unit_count"]
    return {kind: max(FORCE_POOL_SIZE - busy_by_kind.get(kind, 0), 0) for kind in FORCE_BASES}


def _find_resource_alternatives(area: str, registry) -> str:
    """Commander-facing list of cameras, drones, roster, and forces that can cover `area`."""

    parts: list[str] = []

    surveillance_agent = registry.get("surveillance_agent")
    cameras = surveillance_agent.surveillance_store.list_cameras(area=area)
    area_label = _AREA_LABELS.get(area, area)
    if cameras:
        camera_text = ", ".join(f"{camera['camera_id']} ({camera['status']})" for camera in cameras)
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.cameras_covering", area=area_label, cameras=camera_text))
    else:
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.no_cameras", area=area_label))

    # A drone already over the area is coverage the commander can use without a new launch.
    over_area = [
        mission for mission in surveillance_agent.surveillance_store.get_active_missions()
        if (mission.get("target_area") or "").strip().lower() == (area or "").strip().lower()
    ]
    if over_area:
        drones_text = ", ".join(f"{mission['callsign']} ({mission['mission_id']}, {mission['status']})" for mission in over_area)
        parts.append(_catalog_text("response_team.resource_unavailable.alternatives.drones_over_area", area=area_label, drones=drones_text))

    ready_drones = surveillance_agent.surveillance_store.list_drones(status="ready")
    parts.append(_catalog_text("response_team.resource_unavailable.alternatives.ready_drones", count=len(ready_drones)))

    roster_agent = registry.get("roster_agent")
    now_iso = datetime.now(timezone.utc).isoformat()
    available_members = [
        entry["full_name"] for entry in roster_agent.status_store.availability_snapshot(now_iso)
        if entry["availability"] != "unavailable"
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
    """Return the reporter-facing fact sentence and commander-facing alternatives."""

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

__all__ = [
    "AGENTS",
    "NeighboringForcesAgent",
    "RESOURCE_UNAVAILABLE_DESCRIPTION",
    "ResponseTeamRosterAgent",
    "ResponseTeamSurveillanceAgent",
    "_AREA_LABELS",
    "_RESOURCE_KIND_LABELS",
    "_describe_resource_unavailable",
    "_find_resource_alternatives",
]
