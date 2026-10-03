"""Firefighting specialist agents and resource-unavailable copy."""

from datetime import datetime, timedelta, timezone

from agents import Agent, NeighboringForcesAgent, SurveillanceAgent, TeamStatusAgent, failed_tool_result, get_authenticated_request_identity, tool
from persistence import (
    ApparatusStoreError,
    FireStoreError,
    open_apparatus_store,
    open_fire_store,
    open_incident_responder_store,
    open_surveillance_store,
)
from profiles.contracts import AgentSpec
from profiles.firefighting import (
    FIREFIGHTING_CREW_STATUS_DB_PATH,
    FIREFIGHTING_FORCES_DB_PATH,
    FIREFIGHTING_SURVEILLANCE_DB_PATH,
    FORCE_BASES,
    FORCE_BUSY_SECONDS,
    FORCE_POOL_SIZE,
    _catalog_text,
)

class FirefightingSurveillanceAgent(SurveillanceAgent):
    """Surveillance specialist bound to this profile's camera/drone store and fire-station home."""

    surveillance_db_path = FIREFIGHTING_SURVEILLANCE_DB_PATH

    def __init__(self, model: str, api_key: str | None = None):
        """Open this profile's surveillance store, then finish the shared Agent setup."""

        from profiles import firefighting as profile
        self.surveillance_store = open_surveillance_store(
            self.surveillance_db_path, home_area=profile.FIREFIGHTING_DRONE_HOME
        )
        Agent.__init__(self, model, api_key)


class FirefightingCrewStatusAgent(TeamStatusAgent):
    """Crew-shift, apparatus, and fire-registry specialist for this station's own stores."""

    status_db_path = FIREFIGHTING_CREW_STATUS_DB_PATH
    timezone_name = "Asia/Jerusalem"
    response_window_hours = 1

    def __init__(self, model: str, api_key: str | None = None):
        """Open apparatus, fire, and incident stores after the shared team-status setup."""

        super().__init__(model, api_key)
        from profiles import firefighting as profile
        # Read paths from the profile module so test monkeypatches on that facade apply.
        self.apparatus_store = open_apparatus_store(profile.FIREFIGHTING_APPARATUS_DB_PATH)
        self.fire_store = open_fire_store(profile.FIREFIGHTING_FIRES_DB_PATH)
        # Incident links key off core events in DB_PATH, not the apparatus/crew files.
        self.incident_store = open_incident_responder_store(profile.DB_PATH)

    @tool(
        "record_fire_status",
        "Records one fire's current COP status in the fires registry: status='burning' "
        "for an active/escalating fire (creates the row or refreshes last_updated if that "
        "area already has a burning fire), or status='extinguished' for a fire that is "
        "already out or no longer a risk. area is one of this station's declared areas. "
        "Optional source_event_id links the row to the events journal. Side-effecting and "
        "idempotent. Does not dispatch any resource.",
        side_effecting=True,
        idempotent=True,
    )
    def record_fire_status(self, area: str = "", status: str = "", source_event_id: str = "") -> str:
        """Write one fire as burning or extinguished in this profile's fire registry."""

        cleaned_area = area.strip()
        cleaned_status = status.strip().lower()
        if not cleaned_area:
            return failed_tool_result("Clarification required: area is required.")
        if cleaned_status not in {"burning", "extinguished"}:
            return failed_tool_result("Clarification required: status must be burning or extinguished.")
        try:
            if cleaned_status == "burning":
                row = self.fire_store.upsert_burning(
                    area=cleaned_area, source_event_id=source_event_id.strip() or None,
                )
            else:
                row = self.fire_store.extinguish(cleaned_area, reason="reported")
        except FireStoreError as exc:
            return failed_tool_result(f"The fire status was not stored: {exc}")
        return (
            f"Fire {row['fire_id']} in {row['area']} recorded: {row['status'].upper()} "
            f"(updated {row['last_updated']})."
        )

    @tool(
        "touch_active_fire",
        "Refreshes last_updated on the burning fire currently on record for `area`, so the "
        "two-day stale-expiry clock restarts -- use after aerial recon or any other update "
        "that confirms that fire is still active. Does not create a fire if none is burning "
        "there, and does not dispatch. Harmless if no burning fire is on record. "
        "Side-effecting and idempotent.",
        side_effecting=True,
        idempotent=True,
    )
    def touch_active_fire(self, area: str = "") -> str:
        """Refresh last_updated on the burning fire for `area`, if one exists."""

        cleaned_area = area.strip()
        if not cleaned_area:
            return failed_tool_result("Clarification required: area is required.")
        try:
            row = self.fire_store.touch(cleaned_area)
        except FireStoreError as exc:
            return failed_tool_result(f"The fire was not updated: {exc}")
        if row is None:
            return f"No burning fire is currently on record for '{cleaned_area}'."
        return f"Fire {row['fire_id']} in {row['area']} still BURNING (updated {row['last_updated']})."

    @tool(
        "list_active_fires",
        "Returns currently burning fires from the fires registry after applying the two-day "
        "stale-expiry (a fire with no registry update for two days is treated as extinguished). "
        "Optional area filters to one declared area. This is the live COP of active fires -- "
        "not the events journal and not camera snapshots. Read-only.",
        side_effecting=False,
    )
    def list_active_fires(self, area: str = "") -> str:
        """Return currently burning fires after the two-day stale-expiry."""

        rows = self.fire_store.list_active(area=area.strip() or None)
        if not rows:
            scope = f" in '{area.strip()}'" if area.strip() else ""
            return f"No burning fires are currently on record{scope}."
        lines = ["Currently burning fires:"]
        for row in rows:
            lines.append(f"- {row['fire_id']}: {row['area']} (updated {row['last_updated']})")
        return "\n".join(lines)

    @tool(
        "get_apparatus_status",
        "Returns the station's own engine/vehicle apparatus roster with each one's current "
        "status (operational, dispatched, unavailable, or maintenance) and area. Read-only.",
        side_effecting=False,
    )
    def get_apparatus_status(self) -> str:
        """Return the station apparatus roster with each engine's current status."""

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
        """Record one apparatus status and optional area; does not link it to an incident."""

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
        """Link one named apparatus to the single incident currently on record for `area`."""

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
        """Close one named apparatus's current incident link, if it has one."""

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
        """List personnel and apparatus linked to the single incident on record for `area`."""

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
        """Record a commander-declared available shift for the named approved crew members."""

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
    """Mutual-aid dispatch specialist using this profile's force kinds and pool."""

    dispatch_db_path = FIREFIGHTING_FORCES_DB_PATH
    force_bases = FORCE_BASES
    force_pool_size = FORCE_POOL_SIZE
    force_busy_seconds = FORCE_BUSY_SECONDS

    def _capacity_shortage_text(self, kind_norm: str, remaining: int, unit_count: int) -> str:
        """Localized reason when the requested mutual-aid kind has too few units left."""

        return _catalog_text(
            "firefighting.resource_unavailable.force_reason",
            remaining=remaining,
            pool_size=self.force_pool_size,
            resource=_RESOURCE_KIND_LABELS.get(kind_norm, kind_norm),
            unit_count=unit_count,
        )


_RESOURCE_KIND_LABELS = {
    "drone": _catalog_text("firefighting.resource_kind.drone"),
    "camera": _catalog_text("firefighting.resource_kind.camera"),
    "police": _catalog_text("firefighting.resource_kind.police"),
    "ambulance": _catalog_text("firefighting.resource_kind.ambulance"),
    "water_tankers": _catalog_text("firefighting.resource_kind.water_tankers"),
    "aircraft": _catalog_text("firefighting.resource_kind.aircraft"),
    "apparatus": _catalog_text("firefighting.resource_kind.apparatus"),
}

_AREA_LABELS = {
    "pine_ridge": _catalog_text("firefighting.area.pine_ridge"),
    "quarry_junction": _catalog_text("firefighting.area.quarry_junction"),
    "industrial_park": _catalog_text("firefighting.area.industrial_park"),
    "ornim_street": _catalog_text("firefighting.area.ornim_street"),
    "chemical_plant": _catalog_text("firefighting.area.chemical_plant"),
    "fire_station": _catalog_text("firefighting.area.fire_station"),
    "route_444": _catalog_text("firefighting.area.route_444"),
}

_ENGLISH_REASON_KEYS = {
    "No ready drones available in fleet for immediate dispatch.": "firefighting.resource_unavailable.drone_reason",
    "no active camera covering the area": "firefighting.resource_unavailable.camera_no_cover",
}


def _localize_unavailable_reason(reason: str) -> str:
    """Map known English tool reasons onto this profile's catalog copy."""

    cleaned = (reason or "").strip()
    key = _ENGLISH_REASON_KEYS.get(cleaned)
    if key:
        return _catalog_text(key)
    return cleaned


def _force_remaining_capacity(neighboring_forces_agent) -> dict[str, int]:
    """Remaining units per force kind inside the same busy window the dispatch tool uses."""

    busy_since = (datetime.now(timezone.utc) - timedelta(seconds=FORCE_BUSY_SECONDS)).isoformat()
    busy_by_kind: dict[str, int] = {}
    for dispatch in neighboring_forces_agent.dispatch_store.list_dispatches():
        if dispatch["dispatched_at"] > busy_since:
            busy_by_kind[dispatch["force_kind"]] = busy_by_kind.get(dispatch["force_kind"], 0) + dispatch["unit_count"]
    return {kind: max(FORCE_POOL_SIZE - busy_by_kind.get(kind, 0), 0) for kind in FORCE_BASES}


def _find_resource_alternatives(area: str, registry) -> str:
    """Commander-facing list of cameras, drones, crew, forces, and apparatus that can cover `area`."""

    parts: list[str] = []
    area_label = _AREA_LABELS.get(area, area)

    surveillance_agent = registry.get("surveillance_agent")
    cameras = surveillance_agent.surveillance_store.list_cameras(area=area)
    if cameras:
        camera_text = ", ".join(f"{camera['camera_id']} ({camera['status']})" for camera in cameras)
        parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.cameras_covering", area=area_label, cameras=camera_text))
    else:
        parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.no_cameras", area=area_label))

    ready_drones = surveillance_agent.surveillance_store.list_drones(status="ready")
    parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.ready_drones", count=len(ready_drones)))

    crew_agent = registry.get("team_status_agent")
    now_iso = datetime.now(timezone.utc).isoformat()
    available_members = [
        entry["full_name"] for entry in crew_agent.status_store.availability_snapshot(now_iso)
        if entry["availability"] == "available"
    ]
    if available_members:
        parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.available_crew", members=", ".join(available_members)))
    else:
        parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.no_crew"))

    neighboring_forces_agent = registry.get("neighboring_forces_agent")
    remaining_by_kind = _force_remaining_capacity(neighboring_forces_agent)
    force_lines = [
        f"{_RESOURCE_KIND_LABELS[kind]} ({remaining_by_kind[kind]}/{FORCE_POOL_SIZE})" for kind in sorted(FORCE_BASES)
    ]
    parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.forces", forces=", ".join(force_lines)))

    apparatus_rows = crew_agent.apparatus_store.list_apparatus()
    if apparatus_rows:
        apparatus_text = ", ".join(
            f"{row['callsign']} ({row['status']})" for row in apparatus_rows
        )
        parts.append(_catalog_text("firefighting.resource_unavailable.alternatives.apparatus", apparatus=apparatus_text))

    return "; ".join(parts)


def _describe_resource_unavailable(resource_kind: str, area: str, reason: str, registry) -> tuple[str, str]:
    """Return the reporter-facing fact sentence and commander-facing alternatives."""

    resource_label = _RESOURCE_KIND_LABELS.get(resource_kind, resource_kind)
    area_label = _AREA_LABELS.get(area, area)
    fact = _catalog_text(
        "firefighting.resource_unavailable.fact",
        resource=resource_label,
        area=area_label,
        reason=_localize_unavailable_reason(reason),
    )
    alternatives = _find_resource_alternatives(area, registry)
    return fact, alternatives


RESOURCE_UNAVAILABLE_DESCRIPTION = _describe_resource_unavailable


AGENTS = [
    AgentSpec(cls=FirefightingSurveillanceAgent, tier="sub"),
    AgentSpec(cls=FirefightingCrewStatusAgent, tier="sub"),
    AgentSpec(cls=FirefightingExternalForcesAgent, tier="sub"),
]

__all__ = [
    "AGENTS",
    "FirefightingCrewStatusAgent",
    "FirefightingExternalForcesAgent",
    "FirefightingSurveillanceAgent",
    "RESOURCE_UNAVAILABLE_DESCRIPTION",
    "_AREA_LABELS",
    "_RESOURCE_KIND_LABELS",
    "_describe_resource_unavailable",
    "_find_resource_alternatives",
]
