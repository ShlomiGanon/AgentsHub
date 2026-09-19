"""The Friendly Forces dispatch-coordination agent (profiles/friendly_forces.py)."""

from __future__ import annotations

import re

from agents.contracts import ReportIngestionResult, project_report_facts
from agents.runtime import Agent, tool


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
        super().__init__(model, api_key)

    def extract_report(self, raw_text: str, *, received_at: str, scenario_time: str | None = None, **_) -> ExtractionResult | None:
        from history import ExtractionResult
        text = str(raw_text or "")
        normalized = text.casefold()
        if re.search(r"\u05d0\u05d9\u05e1\u05d5\u05e8 \u05d4\u05d3\u05dc\u05e7\u05ea|\u05d4\u05d9\u05e2\u05e8\u05d5\u05ea|\u05d9\u05e2\u05e8\u05e0\u05d9\u05dd|fire.?lighting|forests|rangers", normalized):
            fields = {
                "advisory_kind": "fire_lighting_prohibition", "applies_to": "forests in area",
                "patrols": "rangers", "status": "active", "active_due_to": "heatwave",
            }
            return ExtractionResult("friendly_forces_report", "trusted", "central_hub", (), text, "low", scenario_time or received_at, False, (), business_fields=fields)
        if re.search(r"\u05e9\u05e8\u05d9\u05e4\u05ea \u05e7\u05d5\u05e6\u05d9\u05dd|\u05db\u05d1\u05d9\u05e9\s*444|brush fire|route\s*444", normalized):
            fields = {
                "incident_kind": "brush_fire", "size": "small", "location": "Route 444",
                "possible_cause": "cigarette remains", "cause_status": "unverified",
                "responding_unit": "police patrol", "building_risk": "none",
            }
            return ExtractionResult("friendly_forces_report", "trusted", "central_hub", (), text, "low", scenario_time or received_at, False, (), business_fields=fields)
        return None

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
        self.dispatches_recorded.append(record)
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
        self.dispatches_recorded.append(record)
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
        self.dispatches_recorded.append(record)
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
        self.dispatches_recorded.append(record)
        return f"recorded military dispatch request for '{location}'"
