"""Agent invocation errors (work_plan.md §3.10).

Model errors, timeouts, and unparseable output are each surfaced as a
distinct outcome the executor (§4, later) can branch on — distinct from a
call that returned successfully with a poor result, which is the success
judgment's problem, not a retry candidate. Every subclass carries enough
context (the agent, the trace ID, the underlying cause) to log usefully
without the caller having to reconstruct it.

One base class, not a separate hierarchy per failure kind, so an executor
that just wants "did the call fail" can catch one type; code that cares
about *why* catches a specific subclass.
"""


class AgentInvocationError(Exception):
    def __init__(self, agent_name: str, message: str, *, trace_id: str = "", cause: Exception | None = None):
        self.agent_name = agent_name
        self.trace_id = trace_id
        self.cause = cause
        super().__init__(f"[{agent_name}] {message}" + (f" (trace={trace_id})" if trace_id else ""))


class AgentTimeoutError(AgentInvocationError):
    """The invocation did not complete within its timeout."""


class AgentModelError(AgentInvocationError):
    """The underlying model call itself failed (network, API, auth, ...)."""


class AgentOutputParseError(AgentInvocationError):
    """The model responded, but the output could not be parsed into a result."""


class AgentToolConstructionError(AgentInvocationError):
    """A tool could not be translated into a CrewAI tool object.

    Raised by agents.adapter._build_crewai_tools when dynamically
    subclassing crewai.tools.BaseTool fails (e.g. pydantic rejects the
    dynamic type() construction) — a real, live-verified risk, not a
    hypothetical: see docs/progress.md's §3.10 entry.
    """


class AgentFrameworkNotReadyError(AgentInvocationError):
    """crewai is not installed in this environment yet.

    Raised by agents.adapter instead of a bare ImportError, so callers
    that catch AgentInvocationError uniformly still catch this too — see
    agents/adapter.py's module docstring.
    """
