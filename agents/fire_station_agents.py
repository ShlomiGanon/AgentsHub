"""Shared fire-station dispatch and hazmat agents; tools only record requested actions."""

from agents.runtime import Agent, tool


class DispatchAgent(Agent):
    """Records station-crew and mutual-aid requests; a profile supplies recognized resources."""

    name = "dispatch_agent"
    role = (
        "The fire station's dispatch specialist: records requests to dispatch the station's "
        "own crew to a named area, and requests for a named mutual-aid resource. Every tool "
        "here only records what was requested — it never contacts a real crew or mutual-aid "
        "station, and never confirms a unit actually arrived."
    )
    system_prompt = (
        "You are the dispatch agent. You have two tools: dispatch_station_crew records a "
        "request to dispatch the station's own crew to a named area; request_mutual_aid records "
        "a request for a named mutual-aid resource — only a recognized resource name is "
        "accepted, anything else is refused with no record made. Neither tool contacts a real "
        "crew or mutual-aid station — each only logs that the request was recorded. Report back "
        "plainly what was recorded, never a claim that a unit arrived or a resource was "
        "deployed."
    )

    # Empty here so the shared class accepts no resource until a profile subclass fills this.
    MUTUAL_AID_RESOURCES: tuple[str, ...] = ()

    def __init__(self, model: str, api_key: str | None = None):
        """Initialize in-memory request logs, then finish the shared Agent setup."""

        self.crew_dispatches: list[str] = []
        self.mutual_aid_requests: list[str] = []
        super().__init__(model, api_key)

    @tool(
        "dispatch_station_crew",
        "Records a request to dispatch the station's own crew to a named area. Side-effecting "
        "and not idempotent — running it twice records two dispatch requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_station_crew(self, area: str, note: str = "") -> str:
        """Record a request to send this station's own crew to `area`."""

        record = f"{area}: {note}" if note else area
        self.crew_dispatches.append(record)
        return f"station crew dispatch request recorded for '{area}'"

    @tool(
        "request_mutual_aid",
        "Records a request for a named mutual-aid resource. Only a name from this deployment's "
        "recognized mutual-aid resource list is accepted; anything else is refused and no "
        "record is made. Side-effecting and not idempotent — running it twice records two "
        "requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def request_mutual_aid(self, resource_name: str, area: str = "", note: str = "") -> str:
        """Record a mutual-aid request only when `resource_name` is on the profile list."""

        recognized = type(self).MUTUAL_AID_RESOURCES
        normalized = resource_name.strip().upper()
        if normalized not in recognized:
            recognized_text = ", ".join(recognized) if recognized else "(none configured)"
            return (
                f"mutual-aid request refused: '{resource_name}' is not a recognized mutual-aid "
                f"resource (recognized: {recognized_text})"
            )
        record = normalized
        if area:
            record += f" to {area}"
        if note:
            record += f": {note}"
        self.mutual_aid_requests.append(record)
        return f"mutual-aid request recorded for '{normalized}'"


class HazmatAgent(Agent):
    """Records hazmat-assessment requests; it never claims a real assessment ran."""

    name = "hazmat_agent"
    role = (
        "The fire station's hazardous-materials specialist: records requests for a hazmat "
        "assessment at a named area. Never performs or claims to have performed a real "
        "assessment — it only records that one was requested."
    )
    system_prompt = (
        "You are the hazmat agent. You have one tool: request_hazmat_assessment, which records "
        "a request for a hazardous-materials assessment at a named area. It never performs or "
        "claims to have performed a real assessment — it only records that one was requested. "
        "Report back plainly what was recorded."
    )

    def __init__(self, model: str, api_key: str | None = None):
        """Initialize the in-memory assessment log, then finish Agent setup."""

        self.assessment_requests: list[str] = []
        super().__init__(model, api_key)

    @tool(
        "request_hazmat_assessment",
        "Records a request for a hazardous-materials assessment at a named area. Side-effecting "
        "and not idempotent — running it twice records two requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def request_hazmat_assessment(self, area: str, note: str = "") -> str:
        """Record that a hazmat assessment was requested at `area`."""

        record = f"{area}: {note}" if note else area
        self.assessment_requests.append(record)
        return f"hazmat assessment request recorded for '{area}'"
