"""Built-in agents supplied by the framework."""

from agents.runtime import Agent, tool


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

    def __init__(self, model: str, api_key: str | None = None):
        self.actions_taken: list[str] = []
        super().__init__(model, api_key)

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


class HistoryAgent(Agent):
    name = "history_agent"
    role = (
        "Summarize supplied historical records and answer historical questions only from "
        "the context supplied for the current task."
    )
    system_prompt = (
        "You are the system's history specialist. You have two capabilities: produce faithful "
        "period summaries, and answer questions from supplied historical context. Never use "
        "conversational memory or outside knowledge. Preserve contradictory accounts explicitly "
        "and verbatim; never reconcile, smooth, or guess between them. Retain what happened, how "
        "each event was handled, agent actions, and how it ended."
    )

    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model, api_key)
