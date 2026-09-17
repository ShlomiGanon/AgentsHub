"""Immutable agent descriptors and tool declaration primitives."""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal
import uuid


@dataclass(frozen=True)
class InvocationPolicy:
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    strict_json_schema: bool = False
    usage_metrics: bool = False
    streaming: bool = False
    reasoning_effort: bool = False
    thread_safe_client: bool = False


def provider_capabilities(model: str) -> ProviderCapabilities:
    provider = model.split("/", 1)[0].lower()
    if provider == "openai":
        return ProviderCapabilities(
            strict_json_schema=True,
            usage_metrics=True,
            streaming=True,
            reasoning_effort=True,
            thread_safe_client=False,
        )
    return ProviderCapabilities()


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    side_effecting: bool
    idempotent: bool | None


_TOOL_META_ATTR = "_agent_tool_info"


def tool(name: str, description: str, *, side_effecting: bool, idempotent: bool | None = None):
    if side_effecting and idempotent is None:
        raise ValueError(f"tool '{name}': side_effecting=True requires idempotent to be explicitly True or False")

    if not side_effecting and idempotent is not None:
        raise ValueError(f"tool '{name}': idempotent has no meaning for a read-only tool (side_effecting=False)")

    tool_info = ToolInfo(name=name, description=description, side_effecting=side_effecting, idempotent=idempotent)

    def _decorator(func: Callable) -> Callable:
        setattr(func, _TOOL_META_ATTR, tool_info)
        return func

    return _decorator


def tool_info_of(method: Callable) -> ToolInfo | None:
    return getattr(method, _TOOL_META_ATTR, None)


def exposed_tools_for(agent_instance) -> tuple[ToolInfo, ...]:
    tools = []
    for attr_name in dir(type(agent_instance)):
        method = getattr(type(agent_instance), attr_name, None)
        tool_info = tool_info_of(method)
        if tool_info is not None:
            tools.append(tool_info)

    return tuple(tools)


@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    role: str
    system_prompt: str
    tools: tuple[ToolInfo, ...]
    model: str
    api_key: str | None = None


UNCLEAR_TASK_PREFIX = "UNCLEAR_TASK:"
UNCLEAR_TASK_PROMPT_INSTRUCTION = (
    f'If the task you are given is unclear, ambiguous, or you lack what you need to act on it, '
    f'respond with exactly one line starting with "{UNCLEAR_TASK_PREFIX}" followed by a specific '
    f"statement of what is missing — which parameter, which context, which ambiguity. "
    f"Do not attempt a partial or guessed answer in that case."
)


@dataclass(frozen=True)
class AgentResult:
    status: Literal["success", "unclear_task"]
    text: str
    tool_receipts: tuple["ToolReceipt", ...] = ()


ReportIngestionStatus = Literal["committed", "rejected", "not_applicable", "failed"]


@dataclass(frozen=True, init=False)
class ReportIngestionResult:
    """Typed outcome of a domain report-ingestion attempt.

    The first positional argument intentionally remains compatible with the
    historical ``ReportIngestionResult(True/False, detail)`` constructor while
    allowing domain agents to report the previously ambiguous ``not_applicable``
    and ``failed`` outcomes explicitly.  ``committed`` is a derived property,
    true only after authoritative persistence succeeds; reports never receive a
    synthetic :class:`ToolReceipt`.
    """

    status: ReportIngestionStatus
    detail: str = ""

    def __init__(
        self,
        committed_or_status: bool | str | None = None,
        detail: str = "",
        *,
        status: ReportIngestionStatus | None = None,
        committed: bool | None = None,
    ):
        if committed is not None:
            if committed_or_status is not None:
                raise TypeError("specify either positional status or committed, not both")
            committed_or_status = committed
        if committed_or_status is None and status is None:
            raise TypeError("report ingestion result requires a status")
        if status is None:
            if isinstance(committed_or_status, bool):
                resolved: ReportIngestionStatus = "committed" if committed_or_status else "rejected"
            elif isinstance(committed_or_status, str):
                resolved = committed_or_status  # type: ignore[assignment]
            else:
                raise TypeError("report ingestion status must be bool or a supported status string")
        else:
            resolved = status
        if resolved not in {"committed", "rejected", "not_applicable", "failed"}:
            raise ValueError(f"invalid report ingestion status: {resolved!r}")
        object.__setattr__(self, "status", resolved)
        object.__setattr__(self, "detail", detail)

    @property
    def committed(self) -> bool:
        return self.status == "committed"


@dataclass(frozen=True)
class ToolReceipt:
    """Safe, runtime-authenticated evidence of one tool invocation.

    Arguments and model text are deliberately absent.  ``state_verified`` is
    tri-state: ``True`` means a caller supplied a postcondition verifier,
    ``False`` means verification failed, and ``None`` means no verifier exists.
    """

    tool_name: str
    status: Literal["succeeded", "failed"]
    success: bool
    started_at: str
    completed_at: str
    event_id: str | None = None
    step_id: str | None = None
    side_effecting: bool = False
    failure_kind: str | None = None
    state_verified: bool | None = None
    verification_source: str | None = None
    receipt_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def parse_agent_output(raw_text: str) -> AgentResult:
    stripped = raw_text.strip()
    if stripped.startswith(UNCLEAR_TASK_PREFIX):
        return AgentResult(status="unclear_task", text=stripped[len(UNCLEAR_TASK_PREFIX):].strip())
    return AgentResult(status="success", text=raw_text)


class AgentInvocationError(Exception):
    def __init__(self, agent_name: str, message: str, *, trace_id: str = "", cause: Exception | None = None):
        self.agent_name = agent_name
        self.trace_id = trace_id
        self.cause = cause
        self.tool_receipts: tuple[ToolReceipt, ...] = ()
        super().__init__(f"[{agent_name}] {message}" + (f" (trace={trace_id})" if trace_id else ""))


class AgentTimeoutError(AgentInvocationError):
    pass


class AgentModelError(AgentInvocationError):
    pass


class AgentOutputParseError(AgentInvocationError):
    pass


class AgentToolConstructionError(AgentInvocationError):
    pass


class AgentFrameworkNotReadyError(AgentInvocationError):
    pass


class AgentWarmupError(AgentInvocationError):
    """A configured provider/model failed its startup verification call."""
