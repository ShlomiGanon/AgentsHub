"""CrewAI adapter: agent instances, exact-result capture, and the registry.

LLM cache and kickoff helpers live in runtime_llm / runtime_invoke and are
re-exported here so `import agents.runtime` stays stable.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
from typing import Callable

from agents.contracts import (
    AgentDescriptor,
    AgentInvocationError,
    InvocationPolicy,
    AgentResult,
    ToolInfo,
    ToolResult,
    exposed_tools_for,
    parse_agent_output,
    provider_capabilities,
    tool,
    tool_info_of,
)
from agents.invocation_context import (
    current_invocation_id, current_invocation_agent, invocation_scope,
    record_finished_invocation_id, record_invocation_tool,
)
from agents.runtime_invoke import (
    _build_crewai_tools,
    _checkin_crewai_agent,
    _checkout_crewai_agent,
    _clear_agent_cache,
    _run_crewai_kickoff,
    configure_invocation_limits,
    configure_provider_concurrency,
    initialize_agent_runtime,
    invoke,
    set_invocation_deadline,
)
from agents.runtime_llm import (
    _build_or_reuse_llm,
    _clear_llm_cache,
    _get_crewai,
    _import_crewai,
    _llm_cache,
    _llm_cache_key,
    _llm_options,
    configure_structured_output_mode,
)
from tools import get_trace_id, trace_context
from tools.log_events import (
    agent_invocation_finished,
    agent_invocation_started,
    tool_blocked,
    tool_call,
)

_REQUIRED_CLASS_ATTRS = ("name", "role", "system_prompt")
_USER_FACING_HEBREW_STYLE = (
    " When the task asks for text that will be shown to a user and the user's message is in Hebrew, "
    "write the entire user-facing answer in natural, everyday Hebrew. Use short, clear sentences and a "
    "simple, tidy layout. Keep technical identifiers such as camera, drone, mission, and event IDs unchanged, "
    "but explain statuses and other technical terms in Hebrew. Do not use English headings, internal field names, "
    "raw timestamps, dramatic wording, heavy Markdown, or recommendations that were not requested. "
    "This rule applies only to user-facing prose; preserve every exact JSON, schema, tool argument, and other "
    "machine-readable response format required by the task."
)
_current_allowed_tools: ContextVar[frozenset | None] = ContextVar("current_allowed_tools", default=None)
_authenticated_request_identity: ContextVar[str | None] = ContextVar(
    "authenticated_request_identity", default=None
)


def get_authenticated_request_identity() -> str | None:
    """Return the request identity bound for the current agent invocation, if any."""

    return _authenticated_request_identity.get()


@contextmanager
def authenticated_request_identity(identity: str):
    """Bind the authenticated caller identity for the duration of one request."""

    token = _authenticated_request_identity.set(identity)
    try:
        yield
    finally:
        _authenticated_request_identity.reset(token)


class ExactResultCapture:
    """Makes one tool's exact return value reach the caller unparaphrased.

    Some tool outputs (drone callsigns, mission IDs, ETAs, exact roster
    names) must never be replaced by the model's own paraphrase of them on
    the way back out. A tool method that computed one of these calls
    `.capture(text)` right before returning it; an agent's `process()`
    override runs the real work through `.run(...)` instead of calling
    `super().process(...)` directly. `.run(...)` returns the captured text
    verbatim (wrapped as a successful `AgentResult`) when `.capture(...)`
    was called during that invocation, and the model's own result
    otherwise — this is what lets a tool's formatted string reach the
    caller byte-for-byte even though the model is technically free to
    reword whatever text it was given.

    One instance is one namespace: construct one instance per agent (or
    per profile-defined agent subclass) that needs this, matching the
    convention `agents.surveillance_agent.SurveillanceAgent` established.
    Instances never share state — each owns its own `ContextVar`, results
    dict, and lock — so unrelated agents' captures can never collide even
    when they run concurrently in the same process.
    """

    def __init__(self, namespace: str):
        """Create an isolated capture namespace identified in traces by `namespace`."""

        self._context_var: ContextVar[str | None] = ContextVar(f"exact_result_capture[{namespace}]", default=None)
        self._results: dict[str, tuple[str, bool]] = {}
        self._lock = threading.Lock()

    def capture(self, output: str, *, selection_required: bool = False) -> None:
        """Call from inside a tool method, with the exact text that method is about to return."""

        key = self._context_var.get() or get_trace_id()
        if key:
            with self._lock:
                self._results[key] = (output, selection_required)

    def run(
        self,
        base_process: Callable[..., "AgentResult"],
        text: str,
        allowed_tools: list[str],
        *,
        invocation_policy: "InvocationPolicy | None" = None,
    ) -> "AgentResult":
        """Run `base_process` but return any `.capture(...)` text recorded during this call."""

        key = get_trace_id() or uuid.uuid4().hex
        token = self._context_var.set(key)
        with self._lock:
            self._results.pop(key, None)
        try:
            model_result = base_process(text, allowed_tools, invocation_policy=invocation_policy)
            with self._lock:
                exact = self._results.pop(key, None)
            if exact is not None:
                text, selection_required = exact
                return AgentResult(status="success", text=text, selection_required=selection_required)
            return model_result
        finally:
            with self._lock:
                self._results.pop(key, None)
            self._context_var.reset(token)


def make_exact_result_capture(namespace: str) -> ExactResultCapture:
    """Build one `ExactResultCapture` with a unique ContextVar name for debugging."""

    return ExactResultCapture(namespace)


def _wrap_tool(agent_name: str, bound_method: Callable, tool_info: ToolInfo) -> Callable:
    """Wrap a tool so disallowed calls are blocked and allowed calls are logged."""

    @wraps(bound_method)
    def _wrapped(*args, **kwargs):
        allowed = _current_allowed_tools.get()
        if allowed is None or tool_info.name not in allowed:
            tool_blocked(agent=agent_name, tool=tool_info.name, invocation_id=current_invocation_id())
            return f"Tool '{tool_info.name}' is not permitted for this task."

        started = time.monotonic()
        record_invocation_tool(current_invocation_id(), tool_info.name)
        try:
            tool_result = bound_method(*args, **kwargs)
        except Exception:
            tool_call(
                agent=agent_name,
                tool=tool_info.name,
                invocation_id=current_invocation_id(),
                side_effecting=bool(tool_info.side_effecting),
                status="error",
                duration_seconds=time.monotonic() - started,
                exc_info=True,
            )
            raise
        summary = str(tool_result)[:140] if tool_result is not None else ""
        tool_call(
            agent=agent_name,
            tool=tool_info.name,
            invocation_id=current_invocation_id(),
            side_effecting=bool(tool_info.side_effecting),
            status="success" if not isinstance(tool_result, ToolResult) or tool_result.ok else "error",
            duration_seconds=time.monotonic() - started,
            result_summary=summary,
        )
        return tool_result.text if isinstance(tool_result, ToolResult) else tool_result

    return _wrapped


class Agent:
    """One specialist: class-level role/prompt plus wrapped tools and `process()`."""

    name: str = ""
    role: str = ""
    system_prompt: str = ""
    timeout_seconds: int = 60

    def __init__(self, model: str, api_key: str | None = None):
        """Bind model credentials, wrap declared tools, and freeze the descriptor."""

        missing = [attribute for attribute in _REQUIRED_CLASS_ATTRS if not getattr(type(self), attribute, "")]
        if missing:
            raise TypeError(f"{type(self).__name__} must set class-level {', '.join(missing)}")

        self.model = model
        self.api_key = api_key
        self._wrapped_tools: dict[str, Callable] = {}
        self._resource_unavailable_signal: "tuple[str, str, str] | None" = None

        tool_infos = exposed_tools_for(self)
        for attribute_name in dir(type(self)):
            method = getattr(type(self), attribute_name, None)
            tool_info = tool_info_of(method)
            if tool_info is not None:
                self._wrapped_tools[tool_info.name] = _wrap_tool(self.name, getattr(self, attribute_name), tool_info)

        self.descriptor = AgentDescriptor(
            name=self.name,
            role=self.role,
            system_prompt=self.system_prompt + _USER_FACING_HEBREW_STYLE,
            tools=tool_infos,
            model=model,
            api_key=api_key,
        )

    def exposed_tools(self) -> tuple[ToolInfo, ...]:
        """Return the tools this agent advertised on its descriptor."""

        return self.descriptor.tools

    def signal_resource_unavailable(self, resource_kind: str, area: str, reason: str) -> None:
        """Record a dispatch shortage as instance state so a later tool thread can still see it.

        CrewAI may run the tool on a different thread than `process()`, so a
        ContextVar set inside the tool would not reliably reach the caller.
        """

        self._resource_unavailable_signal = (resource_kind, area, reason)

    def take_resource_unavailable_signal(self) -> "tuple[str, str, str] | None":
        """Read and clear the shortage recorded by the latest tool call on this instance."""

        value = self._resource_unavailable_signal
        if value is not None:
            self._resource_unavailable_signal = None
        return value

    def process(self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None) -> AgentResult:
        """Run one invocation with only the allowed tools visible to the model."""

        allowed = frozenset(allowed_tools)
        exposed_by_name = {tool_info.name: tool_info for tool_info in self.descriptor.tools}
        unknown = sorted(allowed - exposed_by_name.keys())
        if unknown:
            raise AgentInvocationError(
                self.name,
                f"task allows tools not exposed by this agent: {', '.join(unknown)}",
                trace_id=get_trace_id(),
            )

        # Build an invocation-scoped descriptor so disallowed tools are not sent
        # to the model at all. The ContextVar remains the enforcement boundary
        # for a tool call already in flight, while this removes irrelevant tool
        # schemas from the prompt and prevents accidental tool selection.
        invocation_descriptor = AgentDescriptor(
            name=self.descriptor.name,
            role=self.descriptor.role,
            system_prompt=self.descriptor.system_prompt,
            tools=tuple(tool_info for tool_info in self.descriptor.tools if tool_info.name in allowed),
            model=self.descriptor.model,
            api_key=self.descriptor.api_key,
        )
        invocation_id = uuid.uuid4().hex
        invocation_trace_id = get_trace_id()
        parent_invocation_id = current_invocation_id()
        invocation_tools = {}
        for name, wrapped in self._wrapped_tools.items():
            if name not in allowed:
                continue

            @wraps(wrapped)
            def _tracked_tool(*args, _wrapped=wrapped, _invocation_id=invocation_id, _trace_id=invocation_trace_id, **kwargs):
                # CrewAI may run the tool in a different thread. Capture the ID in
                # this wrapper instead of assuming ContextVar propagation.
                with (trace_context(_trace_id) if _trace_id else nullcontext()), invocation_scope(_invocation_id, agent_name=self.name, parent_agent=current_invocation_agent()):
                    return _wrapped(*args, **kwargs)

            invocation_tools[name] = _tracked_tool

        token = _current_allowed_tools.set(allowed)
        record_finished_invocation_id(None)
        started = time.monotonic()
        parent_agent = current_invocation_agent()
        agent_invocation_started(
            agent=self.name,
            invocation_id=invocation_id,
            allowed_tools=sorted(allowed),
            task_summary=text[:120],
            parent_agent=parent_agent,
            parent_invocation_id=parent_invocation_id,
        )
        try:
            with invocation_scope(invocation_id, agent_name=self.name, parent_agent=parent_agent, parent_invocation_id=parent_invocation_id):
                if invocation_policy is None:
                    raw_text = invoke(invocation_descriptor, invocation_tools, text, self.timeout_seconds)
                else:
                    raw_text = invoke(invocation_descriptor, invocation_tools, text, self.timeout_seconds, invocation_policy)
                result = parse_agent_output(raw_text)
            agent_invocation_finished(
                agent=self.name,
                invocation_id=invocation_id,
                status=result.status,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                result_chars=len(result.text),
                parent_agent=parent_agent,
                parent_invocation_id=parent_invocation_id,
            )
            record_finished_invocation_id(invocation_id)
            return result
        except Exception as exc:
            agent_invocation_finished(
                agent=self.name,
                invocation_id=invocation_id,
                status="error",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(exc).__name__,
                parent_agent=parent_agent,
                parent_invocation_id=parent_invocation_id,
            )
            record_finished_invocation_id(invocation_id)
            raise
        finally:
            _current_allowed_tools.reset(token)


class DuplicateAgentNameError(Exception):
    """Raised when two runtime agents share a registry name."""


class AgentRegistry:
    """Lookup table of named Agent instances for one loaded profile."""

    def __init__(self, agents: dict[str, Agent]):
        """Store the mapping used by `get`, `all`, and `restricted_to`."""

        self._agents = agents

    def get(self, name: str) -> Agent:
        """Return the registered agent or raise KeyError with a stable message."""

        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(f"no agent registered under '{name}'") from None

    def all(self) -> tuple[Agent, ...]:
        """Return every registered agent in insertion order."""

        return tuple(self._agents.values())

    def descriptor_for(self, name: str) -> AgentDescriptor:
        """Return the frozen descriptor for one registered agent."""

        return self.get(name).descriptor

    def restricted_to(self, names: "set[str] | frozenset[str]") -> "AgentRegistry":
        """Return a view over the named agents, sharing the same instances."""

        return AgentRegistry({name: agent for name, agent in self._agents.items() if name in names})


def build_agent_registry(core_agents: dict[str, Agent], profile_agents: list[Agent]) -> AgentRegistry:
    """Merge core and profile agents, rejecting a duplicate registry name."""

    agents: dict[str, Agent] = {}

    for agent in [*core_agents.values(), *profile_agents]:
        if agent.name in agents:
            raise DuplicateAgentNameError(f"agent name '{agent.name}' is registered more than once")
        agents[agent.name] = agent

    return AgentRegistry(agents)


__all__ = [
    "Agent",
    "AgentRegistry",
    "DuplicateAgentNameError",
    "ExactResultCapture",
    "authenticated_request_identity",
    "build_agent_registry",
    "configure_invocation_limits",
    "configure_provider_concurrency",
    "configure_structured_output_mode",
    "get_authenticated_request_identity",
    "initialize_agent_runtime",
    "invoke",
    "make_exact_result_capture",
    "provider_capabilities",
    "set_invocation_deadline",
    "tool",
]
