"""Core History Agent for faithful summarization and historical Q&A."""

from agents.base import Agent


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
