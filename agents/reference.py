"""The reference agent (work_plan.md §3.11).

The template a real agent is copied from, and what makes the pipeline
runnable before any real agent exists. Two stub tools, both needed for a
reason: `check_status` (read-only) exercises the question flow's
restriction to read-only tools (§6.12); `record_action` (side-effecting,
non-idempotent) is the only way to test that a retry does not repeat an
action (§4.5) — it genuinely records each call it receives rather than
just returning a canned string, so a test can tell one call from two.
"""

from agents.base import Agent
from agents.tooling import tool


class ReferenceAgent(Agent):
    name = "reference_agent"
    role = (
        "A minimal specialist agent that checks the status of a named location and can record that "
        "an action was taken there. Good for demonstrating the pipeline end to end and as the "
        "starting point for a real agent — not a substitute for a domain specialist."
    )
    system_prompt = (
        "You are the reference agent. You have two tools: check_status, which reports the current "
        "status of a location, and record_action, which records that an action was taken at a "
        "location. Use only the tool the task actually asks for, and report back plainly what you "
        "found or did."
    )

    def __init__(self, model: str):
        self.actions_taken: list[str] = []
        super().__init__(model)

    @tool("check_status", "Returns the current status of a named location. Read-only — never changes anything.", side_effecting=False)
    def check_status(self, location: str) -> str:
        return f"status for '{location}': nominal, no anomalies detected"

    @tool(
        "record_action",
        "Records that an action was taken at a named location. Side-effecting and not idempotent — "
        "running it twice records two actions, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def record_action(self, location: str, note: str = "") -> str:
        self.actions_taken.append(f"{location}: {note}" if note else location)
        return f"recorded action at '{location}'"
