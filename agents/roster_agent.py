"""RosterAgent — records a team or crew member's own reported availability.

Shared, reusable infrastructure (docs/bar_improves.md Stage 4): used unchanged by both
profiles/response_team.py and profiles/fire_station.py, the same way
agents/friendly_forces_agent.py is already shared across profiles/standby_squad.py and
profiles/firefighting.py. No external system integration — the tool only records what was
reported and returns a precise text result describing that recorded effect, never an
unobserved real-world outcome."""

from agents.runtime import Agent, tool


class RosterAgent(Agent):
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
        entry = f"{member}: {status}"
        if reason:
            entry += f" ({reason})"
        if availability_start or availability_end:
            entry += f" [{availability_start or '?'} to {availability_end or '?'}]"
        if entry not in self.availability_records:
            self.availability_records.append(entry)
        return f"availability recorded for '{member}'"
