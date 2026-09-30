"""Immutable agent descriptors and tool declaration primitives."""

from dataclasses import dataclass
import json
from typing import Any, Callable, Literal


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


UNCLEAR_TASK_PROMPT_INSTRUCTION = (
    "If the task you are given is unclear, ambiguous, or you lack what you need to act on it, "
    'respond with exactly one JSON object {"status": "unclear_task", "text": "<what is missing>"} '
    "naming the specific parameter, context, or ambiguity. "
    "Do not attempt a partial or guessed answer in that case. "
    'Otherwise respond with the answer as plain text, or {"status": "success", "text": "<answer>"}.'
)


@dataclass(frozen=True)
class AgentResult:
    status: Literal["success", "unclear_task"]
    text: str
    selection_required: bool = False


@dataclass(frozen=True)
class ToolResult:
    """Structured return from a Python tool method. Callers check `ok` / `selection_required`, never the text."""

    text: str
    ok: bool = True
    selection_required: bool = False

    def __str__(self) -> str:
        return self.text


def failed_tool_result(text: str) -> ToolResult:
    return ToolResult(text=text, ok=False)


def parse_agent_output(raw_text: str) -> AgentResult:
    stripped = raw_text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        status = payload.get("status")
        text = payload.get("text")
        if status in {"success", "unclear_task"} and isinstance(text, str):
            return AgentResult(status=status, text=text)
    return AgentResult(status="success", text=raw_text)


class AgentInvocationError(Exception):
    def __init__(self, agent_name: str, message: str, *, trace_id: str = "", cause: Exception | None = None):
        self.agent_name = agent_name
        self.trace_id = trace_id
        self.cause = cause
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


def is_retryable_invocation_error(error: AgentInvocationError) -> bool:
    """Retry transport/timeouts, but never replay an unchanged permanent provider request."""

    if isinstance(error, AgentTimeoutError):
        return True
    if isinstance(error, (AgentOutputParseError, AgentToolConstructionError, AgentFrameworkNotReadyError)):
        return False
    if not isinstance(error, AgentModelError):
        return True

    cause = error.cause or error.__cause__
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        response = getattr(cause, "response", None)
        status = getattr(cause, "status_code", None) or getattr(response, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 425, 429}:
            return False
        detail = str(cause).casefold()
        if "assistant prefill" in detail or ("prefill" in detail and "not support" in detail):
            return False
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
    return True


class AgentWarmupError(AgentInvocationError):
    """A configured provider/model failed its startup verification call."""
