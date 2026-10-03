"""In-memory roster agent that records one member's own reported availability."""

from agents.runtime import Agent, tool


class RosterAgent(Agent):
    """Records one member's available/unavailable report; does not project roster state."""

    name = "roster_agent"
    role = (
        "Records a team or crew member's own reported availability status — available, or "
        "unavailable with a reason and, once known, a start/end interval. Does not track or "
        "project overall roster state; it only records what one member reported."
    )
    system_prompt = (
        "You are the roster agent. You have one tool: record_availability, which records that a "
        "member reported their own availability status. Never guess a missing interval or reason "
        "— record only what was actually reported, and leave anything not stated out. Report back "
        "plainly what was recorded."
    )

    def __init__(self, model: str, api_key: str | None = None):
        """Initialize the in-memory availability log, then finish Agent setup."""

        self.availability_records: list[str] = []
        super().__init__(model, api_key)

    @tool(
        "record_availability",
        "Records a member's own reported availability status, including an absence reason and/or "
        "a start/end interval when given. Side-effecting and idempotent — recording the identical "
        "report twice leaves one record.",
        side_effecting=True,
        idempotent=True,
    )
    def record_availability(
        self,
        member: str,
        status: str,
        reason: str = "",
        availability_start: str = "",
        availability_end: str = "",
    ) -> str:
        """Record one member's availability, reason, and optional interval."""

        entry = f"{member}: {status}"
        if reason:
            entry += f" ({reason})"
        if availability_start or availability_end:
            entry += f" [{availability_start or '?'} to {availability_end or '?'}]"
        if entry not in self.availability_records:
            self.availability_records.append(entry)
        return f"availability recorded for '{member}'"
