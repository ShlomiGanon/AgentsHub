"""The CrewAI adapter (work_plan.md §3.5, §3.6, §3.10)."""

import hashlib
import inspect
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache, wraps
from typing import Callable

from agents.contracts import (
    AgentDescriptor,
    AgentFrameworkNotReadyError,
    AgentInvocationError,
    InvocationPolicy,
    provider_capabilities,
    AgentModelError,
    AgentOutputParseError,
    AgentResult,
    ReportIngestionResult,
    AgentTimeoutError,
    AgentToolConstructionError,
    AgentWarmupError,
    ToolInfo,
    ToolReceipt,
    UNCLEAR_TASK_PROMPT_INSTRUCTION,
    exposed_tools_for,
    parse_agent_output,
    tool,
    tool_info_of,
)
from agents.diagnostics import (
    get_active_provider_diagnostic_trace,
    install_provider_client_diagnostics,
)
from tools import deep_debug_enabled, get_current_stage, get_trace_id, log_ai_interaction, stage_context, trace_context

logger = logging.getLogger(__name__)

_REQUIRED_CLASS_ATTRS = ("name", "role", "system_prompt")
_current_allowed_tools: ContextVar[frozenset | None] = ContextVar("current_allowed_tools", default=None)
_invocation_deadline: ContextVar[float | None] = ContextVar("invocation_deadline", default=None)
_authenticated_request_identity: ContextVar[str | None] = ContextVar(
    "authenticated_request_identity", default=None
)
_trusted_event_metadata: ContextVar[dict[str, object] | None] = ContextVar(
    "trusted_event_metadata", default=None
)
_tool_receipt_buffer: ContextVar[list[ToolReceipt] | None] = ContextVar("tool_receipt_buffer", default=None)
_tool_execution_correlation: ContextVar[tuple[str | None, str | None] | None] = ContextVar(
    "tool_execution_correlation", default=None
)
_cross_thread_receipts: dict[str, list[ToolReceipt]] = {}
_cross_thread_receipts_lock = threading.Lock()
_tool_class_cache: dict[tuple[type, str, str, int], type] = {}
_tool_class_cache_lock = threading.Lock()
_llm_cache: "OrderedDict[tuple[str, str, str], object]" = OrderedDict()
_llm_cache_lock = threading.Lock()
_LLM_CACHE_MAX_SIZE = 32
_provider_semaphore = threading.BoundedSemaphore(8)
_structured_output_mode = "off"
_max_iter = 8
_model_timeout_seconds = 30.0


def get_authenticated_request_identity() -> str | None:
    return _authenticated_request_identity.get()


@contextmanager
def authenticated_request_identity(identity: str):
    token = _authenticated_request_identity.set(identity)
    try:
        yield
    finally:
        _authenticated_request_identity.reset(token)


@contextmanager
def trusted_event_metadata(metadata: dict[str, object]):
    """Make persisted event metadata available to trusted tool bindings.

    The metadata is deliberately kept out of the model's task contract.  A
    worker installs it around protocol execution, and the runtime injects it
    into the attendance tool call after the model has supplied only business
    fields.
    """

    token = _trusted_event_metadata.set(dict(metadata))
    try:
        yield
    finally:
        _trusted_event_metadata.reset(token)


def get_trusted_operational_scope():
    """Return the server-derived operational scope for the active event/tool."""

    metadata = _trusted_event_metadata.get() or {}
    scope = metadata.get("operational_scope")
    return scope


@contextmanager
def tool_execution_context(event_id: str | None = None, step_id: str | None = None):
    """Capture runtime receipts and correlate them to a persisted event step."""

    receipts_token = _tool_receipt_buffer.set([])
    correlation_token = _tool_execution_correlation.set((event_id, step_id))
    try:
        yield
    finally:
        _tool_execution_correlation.reset(correlation_token)
        _tool_receipt_buffer.reset(receipts_token)


def _record_tool_receipt(receipt: ToolReceipt) -> None:
    buffer = _tool_receipt_buffer.get()
    if buffer is not None:
        buffer.append(receipt)
    trace_id = get_trace_id()
    if trace_id:
        with _cross_thread_receipts_lock:
            _cross_thread_receipts.setdefault(trace_id, []).append(receipt)


def _consume_tool_receipts() -> tuple[ToolReceipt, ...]:
    buffer = _tool_receipt_buffer.get()
    return tuple(buffer or ())


def _take_cross_thread_receipts(trace_id: str | None) -> tuple[ToolReceipt, ...]:
    if not trace_id:
        return ()
    with _cross_thread_receipts_lock:
        return tuple(_cross_thread_receipts.pop(trace_id, ()))


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
        self._context_var: ContextVar[str | None] = ContextVar(f"exact_result_capture[{namespace}]", default=None)
        self._results: dict[str, str] = {}
        self._lock = threading.Lock()

    def capture(self, output: str) -> None:
        """Call from inside a tool method, with the exact text that method is about to return."""

        key = self._context_var.get() or get_trace_id()
        if key:
            with self._lock:
                self._results[key] = output

    def run(
        self,
        base_process: Callable[..., "AgentResult"],
        text: str,
        allowed_tools: list[str],
        *,
        invocation_policy: "InvocationPolicy | None" = None,
    ) -> "AgentResult":
        """Call from a `process()` override in place of calling `base_process` (typically
        `super().process`) directly — returns whatever a `.capture(...)` call recorded during
        this invocation instead of `base_process`'s own result, when one was recorded."""

        key = get_trace_id() or uuid.uuid4().hex
        token = self._context_var.set(key)
        with self._lock:
            self._results.pop(key, None)
        try:
            model_result = base_process(text, allowed_tools, invocation_policy=invocation_policy)
            with self._lock:
                exact = self._results.pop(key, None)
            if exact is not None:
                return AgentResult(status="success", text=exact, tool_receipts=model_result.tool_receipts)
            return model_result
        finally:
            with self._lock:
                self._results.pop(key, None)
            self._context_var.reset(token)


def make_exact_result_capture(namespace: str) -> ExactResultCapture:
    """Build one `ExactResultCapture`, namespaced so its internal `ContextVar` name is unique
    and identifiable in a debugger/traceback even though every instance's shape is identical."""

    return ExactResultCapture(namespace)


def configure_provider_concurrency(limit: int) -> None:
    global _provider_semaphore
    if not 1 <= limit <= 64:
        raise ValueError("provider concurrency must be between 1 and 64")
    _provider_semaphore = threading.BoundedSemaphore(limit)


def configure_structured_output_mode(mode: str) -> None:
    global _structured_output_mode
    if mode not in {"off", "auto", "required"}:
        raise ValueError("structured output mode must be off, auto, or required")
    _structured_output_mode = mode


def configure_invocation_limits(max_iter: int, model_timeout_seconds: float) -> None:
    """Configure the profile-owned CrewAI iteration and provider timeout limits."""

    global _max_iter, _model_timeout_seconds
    if type(max_iter) is not int or not 1 <= max_iter <= 100:
        raise ValueError("max_iter must be an integer between 1 and 100")
    if not 0 < float(model_timeout_seconds) <= 600:
        raise ValueError("model_timeout_seconds must be between 0 and 600")
    _max_iter = max_iter
    _model_timeout_seconds = float(model_timeout_seconds)


def set_invocation_deadline(deadline_monotonic: float | None) -> None:
    _invocation_deadline.set(deadline_monotonic)


@contextmanager
def invocation_deadline(deadline_monotonic: float | None):
    """Install an event deadline for the duration of one worker invocation.

    The explicit context is used by queue workers so the deadline travels with
    the WorkItem instead of depending on the request thread's ContextVar.
    """

    token = _invocation_deadline.set(deadline_monotonic)
    try:
        yield
    finally:
        _invocation_deadline.reset(token)


def _wrap_tool(agent_name: str, bound_method: Callable, tool_info: ToolInfo) -> Callable:
    trusted_fields = {
        "source_message_id",
        "original_text",
        "received_at",
        "availability_start",
        "availability_end",
    }
    hidden_metadata_signature = None
    if tool_info.name == "record_attendance_response":
        original_signature = inspect.signature(bound_method)
        hidden_metadata_signature = original_signature.replace(
            parameters=[
                parameter
                for parameter in original_signature.parameters.values()
                if parameter.name not in trusted_fields
            ]
        )

    @wraps(bound_method)
    def _wrapped(*args, **kwargs):
        allowed = _current_allowed_tools.get()
        if allowed is None or tool_info.name not in allowed:
            logger.info(
                "tool call blocked: not in this call's allowed_tools",
                extra={"event": "tool_blocked", "agent": agent_name, "tool": tool_info.name, "trace_id": get_trace_id()},
            )
            return f"Tool '{tool_info.name}' is not permitted for this task."

        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        correlation = _tool_execution_correlation.get() or (None, None)
        try:
            metadata = _trusted_event_metadata.get()
            if tool_info.name == "record_attendance_response" and metadata is not None:
                # Bind model arguments against the reduced business-only
                # signature, then overwrite the trusted fields mechanically.
                business_kwargs = {key: value for key, value in kwargs.items() if key not in trusted_fields}
                bound = hidden_metadata_signature.bind_partial(*args, **business_kwargs)
                call_kwargs = dict(bound.arguments)
                for field_name in trusted_fields:
                    if field_name in metadata:
                        call_kwargs[field_name] = metadata[field_name]
                tool_result = bound_method(**call_kwargs)
            else:
                tool_result = bound_method(*args, **kwargs)
        except Exception as exc:
            completed_at = datetime.now(timezone.utc).isoformat()
            _record_tool_receipt(
                ToolReceipt(
                    tool_name=tool_info.name,
                    status="failed",
                    success=False,
                    started_at=started_at,
                    completed_at=completed_at,
                    event_id=correlation[0],
                    step_id=correlation[1],
                    side_effecting=tool_info.side_effecting,
                    failure_kind=type(exc).__name__,
                )
            )
            logger.exception(
                "tool call failed",
                extra={
                    "event": "tool_call",
                    "agent": agent_name,
                    "tool": tool_info.name,
                    "status": "error",
                    "duration_seconds": time.monotonic() - started,
                    "trace_id": get_trace_id(),
                },
            )
            raise
        logger.info(
            "tool call",
            extra={
                "event": "tool_call",
                "agent": agent_name,
                "tool": tool_info.name,
                "status": "success",
                "duration_seconds": time.monotonic() - started,
                "trace_id": get_trace_id(),
            },
        )
        _record_tool_receipt(
            ToolReceipt(
                tool_name=tool_info.name,
                status="succeeded",
                success=True,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                event_id=correlation[0],
                step_id=correlation[1],
                side_effecting=tool_info.side_effecting,
            )
        )
        return tool_result

    if hidden_metadata_signature is not None:
        # CrewAI derives the tool's args schema from this signature.  The
        # model must see only business inputs; trusted event metadata is not a
        # model argument and cannot be guessed or substituted by it.
        _wrapped.__signature__ = hidden_metadata_signature
    return _wrapped


class Agent:
    name: str = ""
    role: str = ""
    system_prompt: str = ""
    timeout_seconds: int = 60

    def __init__(self, model: str, api_key: str | None = None):
        missing = [attribute for attribute in _REQUIRED_CLASS_ATTRS if not getattr(type(self), attribute, "")]
        if missing:
            raise TypeError(f"{type(self).__name__} must set class-level {', '.join(missing)}")

        self.model = model
        self.api_key = api_key
        self._wrapped_tools: dict[str, Callable] = {}

        tool_infos = exposed_tools_for(self)
        for attribute_name in dir(type(self)):
            method = getattr(type(self), attribute_name, None)
            tool_info = tool_info_of(method)
            if tool_info is not None:
                self._wrapped_tools[tool_info.name] = _wrap_tool(self.name, getattr(self, attribute_name), tool_info)

        self.descriptor = AgentDescriptor(
            name=self.name,
            role=self.role,
            system_prompt=self.system_prompt,
            tools=tool_infos,
            model=model,
            api_key=api_key,
        )

    def exposed_tools(self) -> tuple[ToolInfo, ...]:
        return self.descriptor.tools

    def ingest_report(self, event: dict, *, scope=None) -> ReportIngestionResult:
        """Optionally commit a validated report to this agent's domain store.

        Domain agents override this hook; the default reports an explicit
        ``not_applicable`` outcome for agents that own no mutable domain state.
        It is deliberately separate from ``execute_tool`` so a report commit
        never fabricates an action receipt.
        """

        return ReportIngestionResult("not_applicable")

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
        allowed_tools: list[str],
    ) -> AgentResult:
        """Execute one explicitly selected tool through the normal runtime boundary."""

        allowed = frozenset(allowed_tools)
        exposed_by_name = {tool_info.name: tool_info for tool_info in self.descriptor.tools}
        unknown = sorted(allowed - exposed_by_name.keys())
        if unknown:
            raise AgentInvocationError(
                self.name,
                f"task allows tools not exposed by this agent: {', '.join(unknown)}",
                trace_id=get_trace_id(),
            )
        if tool_name not in allowed:
            raise AgentInvocationError(
                self.name,
                f"tool '{tool_name}' is not permitted for this task",
                trace_id=get_trace_id(),
            )
        if not isinstance(arguments, dict):
            raise AgentInvocationError(
                self.name,
                "direct tool arguments must be an object",
                trace_id=get_trace_id(),
            )

        wrapped = self._wrapped_tools[tool_name]
        try:
            inspect.signature(wrapped).bind(**arguments)
        except TypeError as exc:
            raise AgentInvocationError(
                self.name,
                f"tool '{tool_name}' arguments failed schema validation: {exc}",
                trace_id=get_trace_id(),
                cause=exc,
            ) from exc

        token = _current_allowed_tools.set(allowed)
        receipt_token = _tool_receipt_buffer.set([])
        trace_key = get_trace_id()
        _take_cross_thread_receipts(trace_key)
        invocation_error = None
        try:
            try:
                result = wrapped(**arguments)
            except AgentInvocationError as exc:
                invocation_error = exc
            except Exception as exc:
                invocation_error = AgentInvocationError(
                    self.name,
                    f"tool '{tool_name}' invocation failed: {exc}",
                    trace_id=get_trace_id(),
                    cause=exc,
                )
        finally:
            receipts = _consume_tool_receipts()
            cross_thread = _take_cross_thread_receipts(trace_key)
            receipts = tuple({receipt.receipt_id: receipt for receipt in (*receipts, *cross_thread)}.values())
            _tool_receipt_buffer.reset(receipt_token)
            _current_allowed_tools.reset(token)

        if invocation_error is not None:
            invocation_error.tool_receipts = receipts
            raise invocation_error from invocation_error.cause

        return AgentResult(status="success", text=str(result), tool_receipts=receipts)

    def process(self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None) -> AgentResult:
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
        invocation_tools = {name: wrapped for name, wrapped in self._wrapped_tools.items() if name in allowed}

        token = _current_allowed_tools.set(allowed)
        receipt_token = _tool_receipt_buffer.set([])
        trace_key = get_trace_id()
        _take_cross_thread_receipts(trace_key)
        invocation_error = None
        try:
            try:
                if invocation_policy is None:
                    raw_text = invoke(invocation_descriptor, invocation_tools, text, self.timeout_seconds)
                else:
                    raw_text = invoke(invocation_descriptor, invocation_tools, text, self.timeout_seconds, invocation_policy)
            except AgentInvocationError as exc:
                invocation_error = exc
            except Exception as exc:
                invocation_error = AgentModelError(
                    self.name, f"model invocation failed: {exc}", trace_id=get_trace_id(), cause=exc
                )
            local_receipts = _consume_tool_receipts()
            cross_thread = _take_cross_thread_receipts(trace_key)
            receipts = tuple({receipt.receipt_id: receipt for receipt in (*local_receipts, *cross_thread)}.values())
            if invocation_error is not None:
                invocation_error.tool_receipts = receipts
                raise invocation_error from invocation_error.cause
            result = parse_agent_output(raw_text)
            return AgentResult(status=result.status, text=result.text, tool_receipts=receipts)
        finally:
            _tool_receipt_buffer.reset(receipt_token)
            _current_allowed_tools.reset(token)


@lru_cache(maxsize=1)
def _import_crewai():
    try:
        import crewai
        import crewai.tools
    except ImportError as exc:
        raise AgentFrameworkNotReadyError(
            "framework",
            "crewai is not installed in this environment yet — see requirements.txt",
            trace_id=get_trace_id(),
            cause=exc,
        ) from exc

    return crewai


def _get_crewai():
    crewai = _import_crewai()
    from crewai.events.utils.console_formatter import set_suppress_console_output

    set_suppress_console_output(True)
    return crewai


def _llm_options(
    descriptor: AgentDescriptor,
    *,
    timeout_seconds: float,
    invocation_policy: InvocationPolicy | None = None,
) -> dict:
    """Build request-safe CrewAI LLM options in one place."""

    options = {
        "model": descriptor.model,
        "timeout": timeout_seconds,
        # CrewAI's native OpenAI-compatible providers configure retries on the
        # SDK client.  Putting this in ``additional_params`` would forward it
        # to ``Completions.create`` as an invalid request parameter.
        "max_retries": 0,
    }
    if descriptor.api_key:
        options["api_key"] = descriptor.api_key
    if invocation_policy is not None and invocation_policy.max_output_tokens is not None:
        options["max_tokens"] = invocation_policy.max_output_tokens
    if invocation_policy is not None and invocation_policy.reasoning_effort != "none":
        options["reasoning_effort"] = invocation_policy.reasoning_effort
    if invocation_policy is not None and invocation_policy.response_schema is not None and _structured_output_mode != "off":
        capabilities = provider_capabilities(descriptor.model)
        if capabilities.strict_json_schema:
            schema_name = str(invocation_policy.response_schema.get("name", "agentshub_output"))
            schema = invocation_policy.response_schema.get("schema", invocation_policy.response_schema)
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
            if capabilities.structured_output_via_additional_params:
                options["additional_params"] = {"response_format": response_format}
            else:
                options["response_format"] = response_format
        elif _structured_output_mode == "required":
            raise AgentModelError(
                descriptor.name,
                f"provider for {descriptor.model!r} does not support strict structured output",
                trace_id=get_trace_id(),
            )
    return options


def _llm_cache_key(descriptor: AgentDescriptor, options: dict) -> tuple[str, str, str]:
    """Return a non-rendered key containing no reversible credential value."""

    secret_identity = hashlib.sha256((descriptor.api_key or "").encode("utf-8")).hexdigest()
    public_options = {key: value for key, value in options.items() if key != "api_key"}
    option_identity = json.dumps(public_options, sort_keys=True, separators=(",", ":"), default=str)
    return descriptor.model, secret_identity, option_identity


def _build_or_reuse_llm(crewai_module, descriptor: AgentDescriptor, options: dict):
    """Construct an isolated LLM unless its provider explicitly opts into reuse."""

    if not provider_capabilities(descriptor.model).thread_safe_client:
        return crewai_module.LLM(**options)

    cache_key = _llm_cache_key(descriptor, options)
    with _llm_cache_lock:
        cached = _llm_cache.get(cache_key)
        if cached is not None:
            _llm_cache.move_to_end(cache_key)
            return cached
        llm = crewai_module.LLM(**options)
        _llm_cache[cache_key] = llm
        _llm_cache.move_to_end(cache_key)
        while len(_llm_cache) > _LLM_CACHE_MAX_SIZE:
            _llm_cache.popitem(last=False)
        return llm


def _clear_llm_cache() -> None:
    """Test/process-lifecycle helper; normal process restart clears the cache."""

    with _llm_cache_lock:
        _llm_cache.clear()


def initialize_agent_runtime(agents: tuple["Agent", ...] | list["Agent"]) -> tuple[str, ...]:
    """Import CrewAI and verify each unique configured provider/model.

    The verification is one real, deterministic, tool-free request per model.
    It is called before queue workers and the HTTP listener start. Secrets are
    never included in the returned identifiers, logs, or raised message.
    """

    crewai_module = _get_crewai()
    unique_descriptors: dict[str, AgentDescriptor] = {}
    for agent in agents:
        unique_descriptors.setdefault(agent.descriptor.model, agent.descriptor)

    warmed_models: list[str] = []
    with trace_context() as startup_trace_id:
        for model, descriptor in unique_descriptors.items():
            provider = model.split("/", 1)[0]
            started = time.monotonic()
            logger.info(
                "model warmup started",
                extra={
                    "event": "model_warmup_started",
                    "stage": "warmup",
                    "provider": provider,
                    "model": model,
                    "trace_id": startup_trace_id,
                    "telemetry_only": True,
                },
            )
            try:
                with stage_context("warmup"):
                    warmup_options = _llm_options(descriptor, timeout_seconds=_model_timeout_seconds)
                    warmup_options.update({"max_tokens": 8, "temperature": 0})
                    llm = _build_or_reuse_llm(crewai_module, descriptor, warmup_options)
                    response = llm.call([{"role": "user", "content": "Reply with OK."}])
                if not isinstance(response, str) or not response.strip():
                    raise ValueError("provider returned an empty or non-text warmup response")
            except Exception as exc:
                logger.error(
                    "model warmup failed",
                    extra={
                        "event": "model_warmup_finished",
                        "stage": "warmup",
                        "provider": provider,
                        "model": model,
                        "status": "error",
                        "termination_reason": type(exc).__name__,
                        "latency_ms": round((time.monotonic() - started) * 1000, 3),
                        "trace_id": startup_trace_id,
                        "telemetry_only": True,
                    },
                )
                raise AgentWarmupError(
                    "runtime",
                    f"startup verification failed for configured model {model!r}",
                    trace_id=startup_trace_id,
                    cause=exc,
                ) from exc
            logger.info(
                "model warmup finished",
                extra={
                    "event": "model_warmup_finished",
                    "stage": "warmup",
                    "provider": provider,
                    "model": model,
                    "status": "success",
                    "termination_reason": "completed",
                    "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    "trace_id": startup_trace_id,
                    "telemetry_only": True,
                },
            )
            warmed_models.append(model)
    return tuple(warmed_models)


def _build_crewai_tools(crewai_module, agent_name: str, wrapped_tools: dict[str, Callable], tool_infos: tuple[ToolInfo, ...]) -> list:
    base_tool_class = crewai_module.tools.BaseTool
    built = []

    for tool_info in tool_infos:
        wrapped = wrapped_tools[tool_info.name]

        def _run(self, *args, _wrapped=wrapped, **kwargs):
            return _wrapped(*args, **kwargs)

        # CrewAI derives tool schemas from this dynamic wrapper signature.
        _run.__signature__ = inspect.Signature(
            [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD), *inspect.signature(wrapped).parameters.values()]
        )

        try:
            cache_key = (base_tool_class, agent_name, tool_info.name, id(wrapped))
            with _tool_class_cache_lock:
                tool_class = _tool_class_cache.get(cache_key)
                if tool_class is None:
                    tool_class = type(
                        f"_{agent_name}_{tool_info.name}_tool",
                        (base_tool_class,),
                        {
                            "__annotations__": {"name": str, "description": str},
                            "name": tool_info.name,
                            "description": tool_info.description,
                            "_run": _run,
                        },
                    )
                    _tool_class_cache[cache_key] = tool_class
            built.append(tool_class())
        except Exception as exc:
            raise AgentToolConstructionError(
                agent_name, f"failed to build CrewAI tool '{tool_info.name}'", trace_id=get_trace_id(), cause=exc
            ) from exc

    return built


def invoke(
    descriptor: AgentDescriptor,
    wrapped_tools: dict[str, Callable],
    text: str,
    timeout_seconds: int,
    invocation_policy: InvocationPolicy | None = None,
) -> str:
    setup_started = time.monotonic()
    effective_timeout = timeout_seconds
    effective_timeout = min(effective_timeout, _model_timeout_seconds)
    if invocation_policy is not None and invocation_policy.timeout_seconds is not None:
        effective_timeout = min(effective_timeout, invocation_policy.timeout_seconds)
    llm_timeout = _model_timeout_seconds
    request_deadline = _invocation_deadline.get()
    if request_deadline is not None:
        remaining_seconds = request_deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise AgentTimeoutError(
                descriptor.name, "shared request deadline was exhausted before invocation", trace_id=get_trace_id()
            )
        effective_timeout = min(effective_timeout, remaining_seconds)
        llm_timeout = min(llm_timeout, remaining_seconds)

    # CrewAI validates `max_execution_time` as an integer. Keep the precise
    # floating-point timeout for deadline and semaphore accounting, but give
    # CrewAI a whole number that never exceeds the remaining budget.
    if effective_timeout < 1:
        raise AgentTimeoutError(
            descriptor.name,
            "less than one second remains before the invocation deadline",
            trace_id=get_trace_id(),
        )
    crewai_timeout_seconds = int(effective_timeout)

    crewai_module = _get_crewai()
    imported_at = time.monotonic()
    crewai_tools = _build_crewai_tools(crewai_module, descriptor.name, wrapped_tools, descriptor.tools)
    tools_built_at = time.monotonic()

    backstory = f"{descriptor.system_prompt}\n\n{UNCLEAR_TASK_PROMPT_INSTRUCTION}"

    llm_options = _llm_options(
        descriptor,
        timeout_seconds=llm_timeout,
        invocation_policy=invocation_policy,
    )
    diagnostic_trace = get_active_provider_diagnostic_trace()
    if diagnostic_trace is not None:
        diagnostic_trace.record_request_build(llm_options)
    llm = _build_or_reuse_llm(crewai_module, descriptor, llm_options)
    restore_diagnostic_client = (
        install_provider_client_diagnostics(llm, diagnostic_trace)
        if diagnostic_trace is not None
        else (lambda: None)
    )
    llm_built_at = time.monotonic()

    crewai_agent = crewai_module.Agent(
        role=descriptor.role,
        goal="Complete the task given, or state clearly what is missing if it cannot be completed.",
        backstory=backstory,
        llm=llm,
        tools=crewai_tools,
        max_iter=_max_iter,
        max_retry_limit=0,
        max_execution_time=crewai_timeout_seconds,
        verbose=False,
    )
    agent_built_at = time.monotonic()

    invocation_started_at = time.monotonic()
    try:
        acquired = _provider_semaphore.acquire(timeout=effective_timeout)
        if not acquired:
            raise TimeoutError("provider concurrency wait exceeded the invocation timeout")
        try:
            crewai_output = crewai_agent.kickoff(text)
        finally:
            _provider_semaphore.release()
    except TimeoutError as exc:
        if diagnostic_trace is not None:
            diagnostic_trace.record_provider_error(exc)
        logger.info(
            "model invocation finished",
            extra={
                "event": "model_invocation_finished",
                "agent": descriptor.name,
                "model": descriptor.model,
                "provider": descriptor.model.split("/", 1)[0],
                "stage": get_current_stage(),
                "attempt": 1,
                "status": "error",
                "termination_reason": "timeout",
                "timeout_seconds": effective_timeout,
                "latency_ms": round((time.monotonic() - invocation_started_at) * 1000, 3),
                "trace_id": get_trace_id(),
                "telemetry_only": True,
            },
        )
        raise AgentTimeoutError(
            descriptor.name, f"timed out after {effective_timeout}s", trace_id=get_trace_id(), cause=exc
        ) from exc
    except Exception as exc:
        if diagnostic_trace is not None:
            diagnostic_trace.record_provider_error(exc)
        logger.info(
            "model invocation finished",
            extra={
                "event": "model_invocation_finished",
                "agent": descriptor.name,
                "model": descriptor.model,
                "provider": descriptor.model.split("/", 1)[0],
                "stage": get_current_stage(),
                "attempt": 1,
                "status": "error",
                "termination_reason": type(exc).__name__,
                "timeout_seconds": effective_timeout,
                "latency_ms": round((time.monotonic() - invocation_started_at) * 1000, 3),
                "trace_id": get_trace_id(),
                "telemetry_only": True,
            },
        )
        raise AgentModelError(descriptor.name, "the model call failed", trace_id=get_trace_id(), cause=exc) from exc
    finally:
        restore_diagnostic_client()

    if diagnostic_trace is not None:
        diagnostic_trace.record_normalization(crewai_output)
    raw_text = getattr(crewai_output, "raw", None)
    if raw_text is None:
        if diagnostic_trace is not None:
            diagnostic_trace.record_content_failure()
        raise AgentOutputParseError(
            descriptor.name, f"could not extract text from CrewAI output: {crewai_output!r}", trace_id=get_trace_id()
        )
    if not isinstance(raw_text, str):
        if diagnostic_trace is not None:
            diagnostic_trace.record_content_failure()
        raise AgentOutputParseError(
            descriptor.name, "CrewAI output raw content was not text", trace_id=get_trace_id()
        )
    if diagnostic_trace is not None:
        diagnostic_trace.record_content_extraction(raw_text)

    if deep_debug_enabled():
        interaction_payload = json.dumps(
            {
                "role": descriptor.role,
                "goal": "Complete the task given, or state clearly what is missing if it cannot be completed.",
                "backstory": backstory,
                "model": descriptor.model,
                "tools": [info.name for info in descriptor.tools],
                "kickoff_text": text,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        log_ai_interaction(descriptor.name, interaction_payload, raw_text, stage=get_current_stage(), trace_id=get_trace_id())

    usage = getattr(crewai_output, "token_usage", None)
    def _usage_value(*names: str):
        for name in names:
            value = getattr(usage, name, None)
            if value is not None:
                return value
            if isinstance(usage, dict) and name in usage:
                return usage[name]
        return None

    logger.info(
        "model invocation finished",
        extra={
            "event": "model_invocation_finished",
            "agent": descriptor.name,
            "model": descriptor.model,
            "provider": descriptor.model.split("/", 1)[0],
            "stage": get_current_stage(),
            "attempt": 1,
            "status": "success",
            "termination_reason": "completed",
            "timeout_seconds": effective_timeout,
            "ttft_seconds": getattr(crewai_output, "ttft_seconds", None),
            "input_tokens": _usage_value("prompt_tokens", "input_tokens"),
            "output_tokens": _usage_value("completion_tokens", "output_tokens"),
            "cache_tokens": _usage_value("cached_tokens", "cache_read_tokens"),
            "total_tokens": _usage_value("total_tokens"),
            "latency_ms": round((time.monotonic() - invocation_started_at) * 1000, 3),
            "trace_id": get_trace_id(),
            "runtime_import_seconds": imported_at - setup_started,
            "runtime_tools_seconds": tools_built_at - imported_at,
            "runtime_llm_seconds": llm_built_at - tools_built_at,
            "runtime_agent_seconds": agent_built_at - llm_built_at,
            "runtime_kickoff_seconds": time.monotonic() - agent_built_at,
            "telemetry_only": True,
        },
    )
    return raw_text


class DuplicateAgentNameError(Exception):
    """Raised when two runtime agents share a registry name."""


class AgentRegistry:
    def __init__(self, agents: dict[str, Agent]):
        self._agents = agents

    def get(self, name: str) -> Agent:
        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(f"no agent registered under '{name}'") from None

    def all(self) -> tuple[Agent, ...]:
        return tuple(self._agents.values())

    def descriptor_for(self, name: str) -> AgentDescriptor:
        return self.get(name).descriptor

    def restricted_to(self, names: "set[str] | frozenset[str]") -> "AgentRegistry":
        """A registry view over the subset of agents whose names are in `names`.

        Unknown names are ignored; the underlying Agent instances are shared,
        not copied, so tool state and provider clients stay the same."""

        return AgentRegistry({name: agent for name, agent in self._agents.items() if name in names})


def build_agent_registry(core_agents: dict[str, Agent], profile_agents: list[Agent]) -> AgentRegistry:
    agents: dict[str, Agent] = {}

    for agent in [*core_agents.values(), *profile_agents]:
        if agent.name in agents:
            raise DuplicateAgentNameError(f"agent name '{agent.name}' is registered more than once")
        agents[agent.name] = agent

    return AgentRegistry(agents)
