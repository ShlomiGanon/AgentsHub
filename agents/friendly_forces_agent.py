"""The Friendly Forces dispatch-coordination agent (profiles/friendly_forces.py)."""

from __future__ import annotations

import re

from agents.contracts import ReportIngestionResult, project_report_facts
from agents.runtime import (
    Agent,
    get_active_operational_profile,
    get_authenticated_request_identity,
    get_tool_execution_correlation,
    get_trusted_event_metadata,
    get_trusted_operational_scope,
    tool,
)
from messages import get_catalog


def _catalog_pattern(key: str) -> str:
    return get_catalog("en").text(key)


# Domain vocabulary for the trusted group-owned extraction path — how an
# external force states an advisory or an incident, in either language. It
# carries no scenario, road number or unit name, so a fixture exercises this
# path without defining it.
_FIRE_BAN = re.compile(
    _catalog_pattern("extraction.friendly_forces.fire_ban"),
    re.IGNORECASE,
)
_FORESTS = re.compile(_catalog_pattern("extraction.friendly_forces.forests"), re.IGNORECASE)
_RANGERS = re.compile(_catalog_pattern("extraction.friendly_forces.rangers"), re.IGNORECASE)
_HEATWAVE = re.compile(_catalog_pattern("extraction.friendly_forces.heatwave"), re.IGNORECASE)

_FIRE_INCIDENT = re.compile(_catalog_pattern("extraction.friendly_forces.fire_incident"), re.IGNORECASE)
_BRUSH_FIRE = re.compile(_catalog_pattern("extraction.friendly_forces.brush_fire"), re.IGNORECASE)
_SIZE_SMALL = re.compile(_catalog_pattern("extraction.friendly_forces.size_small"), re.IGNORECASE)
_SIZE_LARGE = re.compile(_catalog_pattern("extraction.friendly_forces.size_large"), re.IGNORECASE)
_ROUTE_NUMBER = re.compile(_catalog_pattern("extraction.friendly_forces.route_number"), re.IGNORECASE)
_CIGARETTE = re.compile(_catalog_pattern("extraction.friendly_forces.cigarette"), re.IGNORECASE)
_HEDGED = re.compile(_catalog_pattern("extraction.friendly_forces.hedged"), re.IGNORECASE)
_POLICE_PATROL = re.compile(_catalog_pattern("extraction.friendly_forces.police_patrol"), re.IGNORECASE)
_FIREFIGHTERS = re.compile(_catalog_pattern("extraction.friendly_forces.firefighters"), re.IGNORECASE)
_NO_BUILDING_RISK = re.compile(
    _catalog_pattern("extraction.friendly_forces.no_building_risk"),
    re.IGNORECASE,
)
_VEHICLE_OBSERVATION = re.compile(
    "(?:\u05e8\u05db\u05d1\\s+\u05de\u05e1\u05d7\u05e8\u05d9|\u05de\u05e1\u05d7\u05e8\u05d9\u05ea|commercial\\s+van|vehicle).*(?:\u05e0\u05e2|\u05e0\u05e2\u05d4|\u05e0\u05e8\u05d0\u05d4|moving|seen)"
    "|(?:\u05e0\u05e2|\u05e0\u05e2\u05d4|\u05e0\u05e8\u05d0\u05d4|moving|seen).*(?:\u05e8\u05db\u05d1\\s+\u05de\u05e1\u05d7\u05e8\u05d9|\u05de\u05e1\u05d7\u05e8\u05d9\u05ea|commercial\\s+van|vehicle)",
    re.IGNORECASE,
)


class FriendlyForcesAgent(Agent):
    name = "friendly_forces_agent"
    # Friendly-forces groups own intelligence reports.  The event itself is
    # the authoritative persisted report; this hook makes that terminal
    # outcome explicit without fabricating an action/tool receipt.
    owned_report_types = ("friendly_forces_report",)
    default_report_type = "friendly_forces_report"
    role = (
        "A dispatch-coordination specialist agent that records requests to send ambulance, police, "
        "firefighter, or military response units to a named location. Every dispatch is recorded as "
        "a logged request only — this agent does not contact any real ambulance, police, fire, or "
        "military system; it is a coordination and audit record, not a live dispatch integration."
    )
    system_prompt = (
        "You are the friendly forces dispatch agent. You have four tools: dispatch_ambulance, "
        "dispatch_police, dispatch_firefighters, and dispatch_military, each of which records a "
        "request to send that kind of response unit to a named location and returns a confirmation "
        "of what was recorded. None of these tools contacts a real ambulance, police, fire, or "
        "military service — each one only logs that a dispatch was requested, for this system's own "
        "record-keeping. Use only the tool the task actually asks for, include every relevant detail "
        "you were given as the tool's parameters, and report back plainly what you recorded."
    )

    def __init__(self, model: str, api_key: str | None = None):
        self.dispatches_recorded: list[str] = []
        self.dispatch_store = None
        super().__init__(model, api_key)

    def bind_dispatch_store(self, dispatch_store) -> None:
        """Bind the profile database-backed dispatch store at runtime startup."""

        self.dispatch_store = dispatch_store

    def _persist_dispatch(
        self,
        *,
        force_type: str,
        quantity: int,
        location: str,
        note: str,
        extra: str,
    ) -> dict | None:
        scope = get_trusted_operational_scope()
        if self.dispatch_store is None:
            self.dispatches_recorded.append(extra)
            return None
        if scope is None:
            raise RuntimeError("trusted operational scope is required for dispatch execution")
        event_id, _step_id = get_tool_execution_correlation()
        record = self.dispatch_store.create_dispatch(
            force_type=force_type,
            quantity=quantity,
            target=location,
            requested_by=get_authenticated_request_identity() or "system:runtime",
            event_id=event_id,
            protocol_name=get_trusted_event_metadata().get("protocol_name"),
            operational_profile=getattr(get_active_operational_profile(), "profile_id", None),
            scope=scope,
            metadata=note,
        )
        if self.dispatch_store.fetch_dispatch(record["dispatch_id"], scope=scope) is None:
            raise RuntimeError("persisted dispatch could not be verified")
        return record

    def extract_report(self, raw_text: str, *, received_at: str, scenario_time: str | None = None, **_) -> ExtractionResult | None:
        """Extract only the advisory or incident facts the message itself states.

        A field the message does not support is omitted, and a message whose
        subject cannot be read is declined so it travels the ordinary intake
        path rather than committing an invented operational fact.
        """

        from history import ExtractionResult
        text = str(raw_text or "")
        occurrence = scenario_time or received_at

        fields = self._advisory_fields(text) or self._incident_fields(text)
        if fields is None:
            return None

        return ExtractionResult(
            "friendly_forces_report", "trusted", "central_hub", (), text, "low", occurrence, False, (),
            business_fields=fields,
        )

    @staticmethod
    def _advisory_fields(text: str) -> dict | None:
        if not _FIRE_BAN.search(text):
            return None

        fields = {"advisory_kind": "fire_lighting_prohibition", "status": "active"}
        if _FORESTS.search(text):
            fields["applies_to"] = "forests in area"
        if _RANGERS.search(text):
            fields["patrols"] = "rangers"
        if _HEATWAVE.search(text):
            fields["active_due_to"] = "heatwave"
        return fields

    @staticmethod
    def _incident_fields(text: str) -> dict | None:
        if not _FIRE_INCIDENT.search(text):
            if _VEHICLE_OBSERVATION.search(text):
                return {"incident_kind": "security_observation"}
            return None

        fields = {"incident_kind": "brush_fire" if _BRUSH_FIRE.search(text) else "fire"}
        if _SIZE_SMALL.search(text):
            fields["size"] = "small"
        elif _SIZE_LARGE.search(text):
            fields["size"] = "large"

        route = _ROUTE_NUMBER.search(text)
        if route:
            fields["location"] = f"Route {route.group(1)}"

        if _CIGARETTE.search(text):
            fields["possible_cause"] = "cigarette remains"
        if fields.get("possible_cause") and _HEDGED.search(text):
            fields["cause_status"] = "unverified"

        if _POLICE_PATROL.search(text):
            fields["responding_unit"] = "police patrol"
        elif _FIREFIGHTERS.search(text):
            fields["responding_unit"] = "firefighters"

        if _NO_BUILDING_RISK.search(text):
            fields["building_risk"] = "none"
        return fields

    def ingest_report(self, event: dict, *, scope=None) -> ReportIngestionResult:
        if event.get("classification") != self.default_report_type:
            return ReportIngestionResult("not_applicable")
        if not str(event.get("description") or "").strip():
            return ReportIngestionResult("rejected", "friendly-forces report has no description")
        allowed = {
            "advisory_kind", "applies_to", "patrols", "status", "active_due_to",
            "incident_kind", "size", "location", "possible_cause", "cause_status",
            "responding_unit", "building_risk", "force_source", "reported_status",
            "uncertainty", "resource_mention",
        }
        fields = event.get("business_fields") or {}
        if set(fields) - allowed:
            return ReportIngestionResult("rejected", "friendly-forces report contains unsupported domain fields")
        if any(
            value is not None and type(value) not in {str, int, float, bool}
            for value in fields.values()
        ):
            return ReportIngestionResult("rejected", "friendly-forces report fields must be scalar")
        # Generic intelligence facts are already durably stored as the event
        # before this hook runs.  No dispatch is implied by a report.
        projection = project_report_facts(
            event, domain="friendly_forces", projection_kind="operational_fact"
        )
        return ReportIngestionResult(
            "committed", "friendly-forces report committed", projection=projection
        )

    @tool(
        "dispatch_ambulance",
        "Records a request to send ambulance/medical response units to a named location. "
        "Side-effecting and not idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_ambulance(self, location: str, patient_count: int = 1, severity: str = "", note: str = "") -> str:
        record = (
            f"ambulance dispatch requested for '{location}': patient_count={patient_count}"
            f"{f', severity={severity}' if severity else ''}{f', note={note}' if note else ''}"
        )
        self._persist_dispatch(
            force_type="ambulance", quantity=patient_count, location=location,
            note=note or severity, extra=record,
        )
        return f"recorded ambulance dispatch request for '{location}'"

    @tool(
        "dispatch_police",
        "Records a request to send police response units to a named location. Side-effecting and "
        "not idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_police(self, location: str, unit_count: int = 1, incident_type: str = "", note: str = "") -> str:
        record = (
            f"police dispatch requested for '{location}': unit_count={unit_count}"
            f"{f', incident_type={incident_type}' if incident_type else ''}{f', note={note}' if note else ''}"
        )
        self._persist_dispatch(
            force_type="police", quantity=unit_count, location=location,
            note=note or incident_type, extra=record,
        )
        return f"recorded police dispatch request for '{location}'"

    @tool(
        "dispatch_firefighters",
        "Records a request to send firefighter response units to a named location. Side-effecting "
        "and not idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_firefighters(self, location: str, truck_count: int = 1, incident_type: str = "", note: str = "") -> str:
        record = (
            f"firefighter dispatch requested for '{location}': truck_count={truck_count}"
            f"{f', incident_type={incident_type}' if incident_type else ''}{f', note={note}' if note else ''}"
        )
        self._persist_dispatch(
            force_type="firefighters", quantity=truck_count, location=location,
            note=note or incident_type, extra=record,
        )
        return f"recorded firefighter dispatch request for '{location}'"

    @tool(
        "dispatch_military",
        "Records a request to send general military response units to a named location. "
        "Side-effecting and not idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_military(self, location: str, unit_type: str = "", force_size: int = 0, note: str = "") -> str:
        record = (
            f"military dispatch requested for '{location}': force_size={force_size}"
            f"{f', unit_type={unit_type}' if unit_type else ''}{f', note={note}' if note else ''}"
        )
        self._persist_dispatch(
            force_type="military", quantity=force_size or 1, location=location,
            note=note or unit_type, extra=record,
        )
        return f"recorded military dispatch request for '{location}'"

    # Fire-service mutual aid. Adopted from feat/FinalProfiles, which added these
    # two to a fire-only agent subclass; here they live beside the other four
    # dispatch tools instead, because one deployment of this core hosts both
    # organization types and a second forces agent would be a parallel
    # abstraction. Which organization may actually run them is decided where
    # every other capability is decided — the protocol that lists them in its
    # `approved_tools`, exposed only to the profiles that declare that protocol.
    @tool(
        "dispatch_water_tankers",
        "Records a request to send water-tanker trucks, as mutual aid from another station, to a "
        "named location. Side-effecting and not idempotent — running it twice records two dispatch "
        "requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_water_tankers(self, location: str, tanker_count: int = 1, source_station: str = "", note: str = "") -> str:
        record = (
            f"water tanker dispatch requested for '{location}': tanker_count={tanker_count}"
            f"{f', source_station={source_station}' if source_station else ''}{f', note={note}' if note else ''}"
        )
        self._persist_dispatch(
            force_type="water_tankers", quantity=tanker_count, location=location,
            note=note or source_station, extra=record,
        )
        return f"recorded water tanker dispatch request for '{location}'"

    @tool(
        "dispatch_aircraft",
        "Records a request to send firefighting aircraft to a named location. Side-effecting and "
        "not idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_aircraft(self, location: str, aircraft_count: int = 1, aircraft_type: str = "firefighting", note: str = "") -> str:
        record = (
            f"aircraft dispatch requested for '{location}': aircraft_count={aircraft_count}"
            f", aircraft_type={aircraft_type}{f', note={note}' if note else ''}"
        )
        self._persist_dispatch(
            force_type="aircraft", quantity=aircraft_count, location=location,
            note=note or aircraft_type, extra=record,
        )
        return f"recorded aircraft dispatch request for '{location}'"
