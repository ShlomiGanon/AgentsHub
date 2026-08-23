"""Shared error type for the Main Agent's decision modules.

Every judgment (risk, selection, formulation, judgment) sends a prompt
asking for a specific response format and parses it programmatically —
this is what's raised when a response doesn't match, whether because the
model reported the task unclear or because its output just didn't parse.
Each response format is an unverified prompt convention (same status as
agents.results's UNCLEAR_TASK: sentinel) — see docs/progress.md.
"""


class OrchestrationParseError(Exception):
    """A Main Agent response could not be parsed into the expected shape."""
