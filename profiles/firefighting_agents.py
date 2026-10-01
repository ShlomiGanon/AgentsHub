"""Firefighting specialist agents."""

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

import profiles.firefighting as _facade
globals().update({name: getattr(_facade, name) for name in dir(_facade) if not name.startswith("__")})

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
