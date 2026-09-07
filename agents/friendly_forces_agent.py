"""The Friendly Forces dispatch-coordination agent (profiles/friendly_forces.py)."""

from agents.runtime import Agent, tool


class FriendlyForcesAgent(Agent):
    name = "friendly_forces_agent"
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
