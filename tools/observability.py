"""Structured logging (work_plan.md §1.8)."""

import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Callable

from config import base as base_config
if TYPE_CHECKING:
    from persistence import PersistenceInterface

_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")
_current_stage: ContextVar[str] = ContextVar("current_stage", default="")
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_latency_samples: dict[str, list[float]] = defaultdict(list)
_latency_lock = threading.Lock()

try:
    from opentelemetry import metrics, trace
except ImportError:
    metrics = None
    trace = None

_tracer = trace.get_tracer("agentshub") if trace is not None else None
_meter = metrics.get_meter("agentshub") if metrics is not None else None
_stage_histogram = _meter.create_histogram("agentshub.stage.duration", unit="s") if _meter is not None else None
_telemetry_configured = False


def new_trace_id() -> str:
    return uuid.uuid4().hex


def normalize_trace_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _TRACE_ID_PATTERN.fullmatch(candidate) else new_trace_id()


def is_valid_trace_id(value: str | None) -> bool:
    """Return whether a caller-supplied trace ID is valid without replacing it."""

    return bool(_TRACE_ID_PATTERN.fullmatch((value or "").strip()))


def _debug_tokens(entry: dict[str, Any], catalog: Any) -> str:
    values = (entry.get("input_tokens"), entry.get("output_tokens"), entry.get("cache_tokens"))
    if entry.get("total_tokens") is not None:
        return str(entry["total_tokens"])
    if all(value is None for value in values):
        return catalog.text("debug.tokens_unavailable")
    return catalog.text(
        "debug.tokens_breakdown",
        input=values[0] if values[0] is not None else "-",
        output=values[1] if values[1] is not None else "-",
        cache=values[2] if values[2] is not None else "-",
    )


def render_deep_debug_entry(entry: dict[str, Any], catalog: Any) -> str | None:
    """Render one approved structured trace record without exposing raw model I/O."""

    event = entry.get("event")
    if event == "api_request_started":
        return catalog.text("debug.api_received")
    if event == "intent_classified":
        return catalog.text("debug.intent", intent=entry.get("intent", "?"))
    if event == "report_received":
        return catalog.text("debug.report", event_id=entry.get("event_id", "?"))
    if event == "request_received":
        return catalog.text("debug.request", event_id=entry.get("event_id", "?"))
    if event == "extraction_result":
        return catalog.text(
            "debug.extraction",
            classification=entry.get("classification") or "-",
            area=entry.get("area") or "-",
        )
    if event == "risk_assessed":
        return catalog.text("debug.risk", risk_level=entry.get("risk_level", "?"))
    if event == "protocol_selection":
        return catalog.text(
            "debug.protocol",
            status=entry.get("status", "?"),
            protocol=entry.get("protocol_name") or "-",
        )
    if event in {"hold_created", "hold_resolved"}:
        return catalog.text(
            "debug.hold_created" if event == "hold_created" else "debug.hold_resolved",
            hold_kind=entry.get("hold_kind", "?"),
            event_id=entry.get("event_id", "?"),
        )
    if event == "queue_started":
        return catalog.text(
            "debug.queue",
            wait_ms=round(float(entry.get("queue_wait_seconds") or 0) * 1000, 3),
        )
    if event == "stage_finished":
        return catalog.text(
            "debug.stage",
            stage=entry.get("stage", "?"),
            status=entry.get("status", "?"),
            latency_ms=round(float(entry.get("duration_seconds") or 0) * 1000, 3),
        )
    if event == "step_start":
        return catalog.text(
            "debug.step_start",
            step_index=entry.get("step_index", "?"),
            agent=entry.get("agent", "?"),
        )
    if event == "step_result":
        return catalog.text(
            "debug.step_result",
            step_index=entry.get("step_index", "?"),
            agent=entry.get("agent", "?"),
            status="success" if entry.get("succeeded") else "failed",
        )
    if event in {"step_retry", "step_failed"}:
        return catalog.text(
            "debug.step_retry" if event == "step_retry" else "debug.step_failed",
            agent=entry.get("agent", "?"),
            attempt=entry.get("attempt", "?"),
        )
    if event == "protocol_waiting_for_event_data":
        return catalog.text(
            "debug.waiting_data",
            fields=", ".join(entry.get("missing_event_fields") or ("-",)),
        )
    if event == "tool_call":
        return catalog.text(
            "debug.tool",
            agent=entry.get("agent", "?"),
            tool=entry.get("tool", "?"),
            status=entry.get("status", "?"),
        )
    if event == "tool_blocked":
        return catalog.text(
            "debug.tool_blocked",
            agent=entry.get("agent", "?"),
            tool=entry.get("tool", "?"),
        )
    if event in {"provider_request_finished", "provider_request_failed"}:
        return catalog.text(
            "debug.provider_failed" if event == "provider_request_failed" else "debug.provider",
            provider=entry.get("provider", "?"),
            model=entry.get("model", "?"),
            latency_ms=entry.get("latency_ms", "?"),
            tokens=_debug_tokens(entry, catalog),
        )
    if event == "insight_generated":
        return catalog.text("debug.insight", protocol=entry.get("protocol", "?"))
    if event == "final_verdict":
        return catalog.text("debug.judgment", verdict=entry.get("verdict", "?"))
    if event == "event_outcome":
        return catalog.text(
            "debug.outcome",
            event_id=entry.get("event_id", "?"),
            outcome=entry.get("outcome", "?"),
        )
    if event in {"queue_processing_failed", "queue_deadline_expired"}:
        return catalog.text("debug.queue_failed")
    return None


def get_trace_id() -> str:
    return _current_trace_id.get()


@contextmanager
def trace_context(trace_id: str | None = None):
    token = _current_trace_id.set(trace_id or new_trace_id())
    try:
        yield _current_trace_id.get()
    finally:
        _current_trace_id.reset(token)


def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


def get_current_stage() -> str:
    return _current_stage.get()


@contextmanager
def stage_context(stage: str):
    token = _current_stage.set(stage)
    started = time.monotonic()
    status = "success"
    termination_reason = "completed"
    span_context = _tracer.start_as_current_span(stage) if _tracer is not None else None

    if span_context is not None:
        span_context.__enter__()

    try:
        yield
    except BaseException as exc:
        status = "error"
        termination_reason = type(exc).__name__
        if span_context is not None:
            span = trace.get_current_span()
            span.record_exception(exc)
            span.set_attribute("agentshub.status", status)
        raise
    finally:
        duration_seconds = time.monotonic() - started
        with _latency_lock:
            _latency_samples[stage].append(duration_seconds)

        if _stage_histogram is not None:
            _stage_histogram.record(duration_seconds, {"stage": stage, "status": status})

        logger.info(
            "stage finished",
            extra={
                "event": "stage_finished",
                "stage": stage,
                "status": status,
                "termination_reason": termination_reason,
                "duration_seconds": duration_seconds,
                "trace_id": get_trace_id(),
                "telemetry_only": True,
            },
        )

        if span_context is not None:
            span = trace.get_current_span()
            span.set_attribute("agentshub.status", status)
            span.set_attribute("agentshub.termination_reason", termination_reason)
            span.set_attribute("agentshub.duration_seconds", duration_seconds)
            span_context.__exit__(None, None, None)

        _current_stage.reset(token)


@contextmanager
def telemetry_span(name: str, **attributes: Any):
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(f"agentshub.{key}", value)
        yield


def latency_snapshot() -> dict[str, dict[str, float | int]]:
    with _latency_lock:
        copied = {stage: sorted(samples) for stage, samples in _latency_samples.items()}

    def _percentile(samples: list[float], ratio: float) -> float:
        if not samples:
            return 0.0
        index = min(len(samples) - 1, max(0, int((len(samples) - 1) * ratio)))
        return samples[index]

    return {
        stage: {
            "count": len(samples),
            "p50": _percentile(samples, 0.50),
            "p95": _percentile(samples, 0.95),
            "p99": _percentile(samples, 0.99),
        }
        for stage, samples in copied.items()
    }


def configure_telemetry() -> None:
    global _telemetry_configured
    mode = os.environ.get("OBSERVABILITY_MODE", "log").strip().lower()
    if mode not in {"log", "otlp"}:
        raise RuntimeError("OBSERVABILITY_MODE must be 'log' or 'otlp'")
    if mode == "otlp" and not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        raise RuntimeError("OBSERVABILITY_MODE=otlp requires OTEL_EXPORTER_OTLP_ENDPOINT")
    if mode != "otlp" or _telemetry_configured:
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError("OBSERVABILITY_MODE=otlp requires the OpenTelemetry SDK and OTLP exporter") from exc

    resource = Resource.create({"service.name": "agentshub", "service.instance.id": _active_profile_name or "unknown"})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
    _telemetry_configured = True

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)

_active_profile_name = ""

logger = logging.getLogger(__name__)


def _resolve_trace_id(record: logging.LogRecord) -> str:
    """The trace ID a record belongs to: an explicit one a call site passed via `extra={"trace_id": ...}` (e.g."""

    explicit = getattr(record, "trace_id", None)
    return explicit if explicit is not None else get_trace_id()


def _record_extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Every field a call site passed via `extra=`, beyond the standard `LogRecord` attributes — shared by the JSON formatter and the DB handler so neither can drift from what the othe..."""

    return {key: value for key, value in record.__dict__.items() if key not in _RESERVED_LOG_RECORD_ATTRS}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "profile_name": _active_profile_name,
            "trace_id": _resolve_trace_id(record),
        }

        payload.update(_record_extra_fields(record))

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _truncate(value: Any, limit: int = 70) -> str:
    """Render `value` as a string short enough for one terminal line — never wrap, never dump a whole prompt/response/task/result."""

    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _render_default(fields: dict[str, Any], record: logging.LogRecord) -> str:
    """Every record with no `event` field this module recognizes — third- party library lines (crewai, httpx, werkzeug, litellm's bare `root` logger, ...) and anything logged without `..."""

    return _truncate(record.getMessage(), 100)


def _render_intent_classified(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"intent classified → {f.get('intent', '?')} ({_truncate(f.get('reason', ''))})"


def _render_extraction_result(f: dict[str, Any], record: logging.LogRecord) -> str:
    classification = f.get("classification") or "(unclassified)"
    area = f.get("area") or "(no area)"
    missing = f.get("missing_fields") or []
    suffix = f", missing: {', '.join(missing)}" if missing else ""
    if f.get("occurred_at_is_fallback"):
        suffix += ", occurred_at fell back to received_at"
    return f"extraction → {classification}/{area}{suffix}"


def _render_hold_created(f: dict[str, Any], record: logging.LogRecord) -> str:
    if f.get("hold_kind") == "clarification":
        return f"clarification hold created → unresolved: {f.get('unresolved_field', '?')}"
    return f"approval hold created → reason: {f.get('reason', '?')}"


def _render_hold_resolved(f: dict[str, Any], record: logging.LogRecord) -> str:
    resolved_by = f.get("resolved_by", "?")
    if f.get("hold_kind") == "clarification":
        return f"clarification resolved by {resolved_by} → {f.get('chosen_classification', '?')}"
    return f"approval resolved by {resolved_by} → {f.get('status', '?')} (decision: {f.get('decision', '?')})"


def _render_risk_assessed(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"risk assessed → {f.get('risk_level', '?')} (score={f.get('risk_score', '?')}, {_truncate(f.get('risk_reason', ''))})"


def _render_protocol_selection(f: dict[str, Any], record: logging.LogRecord) -> str:
    status = f.get("status")
    if status == "selected":
        return f"protocol selected → {f.get('protocol_name', '?')} ({_truncate(f.get('reason', ''))})"
    if status == "ambiguous":
        candidates = f.get("candidate_names") or []
        return f"protocol selection → ambiguous among [{', '.join(candidates)}]"
    if status == "no_match":
        return f"protocol selection → no match ({_truncate(f.get('reason', ''))})"
    return f"protocol selection → {status} ({_truncate(f.get('reason', ''))})"


def _render_precedent_closure(f: dict[str, Any], record: logging.LogRecord) -> str:
    matches = f.get("matched_event_ids") or []
    if f.get("closed"):
        return f"precedent lookup → {len(matches)} match(es), closed via {f.get('closing_event_id', '?')}"
    return f"precedent lookup → {len(matches)} match(es), not closed"


def _render_step_start(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"step {f.get('step_index', '?')} started → {f.get('agent', '?')}: {_truncate(f.get('task_text', ''))}"


def _render_step_result(f: dict[str, Any], record: logging.LogRecord) -> str:
    status = "succeeded" if f.get("succeeded") else "failed"
    result_text = f.get("result_text")
    detail = _truncate(result_text) if result_text else "no result"
    return f"step {f.get('step_index', '?')} {status} → {f.get('agent', '?')} ({detail})"


def _render_step_retry(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"retrying step → {f.get('agent', '?')}, attempt {f.get('attempt', '?')} ({_truncate(f.get('cause', ''))})"


def _render_step_failed(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"step failed → {f.get('agent', '?')}, attempt {f.get('attempt', '?')} ({_truncate(f.get('cause', ''))})"


def _render_step_unclear(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"step unclear → {f.get('agent', '?')}, attempt {f.get('attempt', '?')} (missing: {_truncate(f.get('missing', ''))})"


def _render_insight_generated(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"insight → {f.get('protocol', '?')}: {_truncate(f.get('insight_text', ''))}"


def _render_final_verdict(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"final verdict → {f.get('verdict', '?')} ({_truncate(f.get('reasoning', ''))})"


def _render_event_outcome(f: dict[str, Any], record: logging.LogRecord) -> str:
    outcome = f.get("outcome", "?")
    detail = f.get("failure_reason") or f.get("reasoning") or f.get("precedent_event_id")
    if not detail:
        return f"event outcome → {outcome}"
    return f"event outcome → {outcome} ({_truncate(detail)})"


def _render_report_received(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"report received from {f.get('sender_identity', '?')} ({f.get('source', '?')}): {_truncate(f.get('raw_text', ''))}"


def _render_request_received(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"request received from {f.get('sender_identity', '?')}: {_truncate(f.get('raw_text', ''))}"


def _render_tool_blocked(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"tool call BLOCKED → {f.get('agent', '?')} tried '{f.get('tool', '?')}'"


def _render_tool_call(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"tool call → {f.get('agent', '?')}: {f.get('tool', '?')}"


def _render_queue_processing_failed(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"queue item failed → {_truncate(f.get('item', ''))}"


def _render_api_error(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"API request refused → {f.get('status_code', '?')} {f.get('error_class', '?')}: {_truncate(f.get('error_message', ''))}"


def _render_api_unexpected_error(f: dict[str, Any], record: logging.LogRecord) -> str:
    return "unhandled exception in an API request"


def _render_model_io(f: dict[str, Any], record: logging.LogRecord) -> str:
    return f"model I/O → {f.get('agent', '?')} [{f.get('stage', '?')}]"


def _render_provider_request_failed(f: dict[str, Any], record: logging.LogRecord) -> str:
    target = f"{f.get('provider', '?')}/{f.get('model', '?')}"
    detail = _truncate(f.get("error_detail") or f.get("termination_reason", "provider error"))
    return f"provider request failed → {target} ({detail})"


_EVENT_RENDERERS: dict[str, Callable[[dict[str, Any], logging.LogRecord], str]] = {
    "intent_classified": _render_intent_classified,
    "extraction_result": _render_extraction_result,
    "hold_created": _render_hold_created,
    "hold_resolved": _render_hold_resolved,
    "risk_assessed": _render_risk_assessed,
    "protocol_selection": _render_protocol_selection,
    "precedent_closure": _render_precedent_closure,
    "step_start": _render_step_start,
    "step_result": _render_step_result,
    "step_retry": _render_step_retry,
    "step_failed": _render_step_failed,
    "step_unclear": _render_step_unclear,
    "insight_generated": _render_insight_generated,
    "final_verdict": _render_final_verdict,
    "event_outcome": _render_event_outcome,
    "report_received": _render_report_received,
    "request_received": _render_request_received,
    "tool_blocked": _render_tool_blocked,
    "tool_call": _render_tool_call,
    "queue_processing_failed": _render_queue_processing_failed,
    "api_error": _render_api_error,
    "api_unexpected_error": _render_api_unexpected_error,
    "model_io": _render_model_io,
    "provider_request_failed": _render_provider_request_failed,
}


class _RedundantCrewAIErrorFilter(logging.Filter):
    """Hide CrewAI's duplicated, provider-mislabelled raw error records.

    The correlated ``provider_request_failed`` event retains the real provider,
    model, trace and underlying error. CrewAI emits these raw root records in
    both its inner and outer call wrappers and labels OpenRouter as OpenAI.
    """

    _PREFIXES = (
        "OpenAI API call failed:",
        "OpenAI Responses API call failed:",
        "Failed to connect to OpenAI API:",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.name == "root"
            and record.getMessage().startswith(self._PREFIXES)
        )


class _HumanReadableFormatter(logging.Formatter):
    """`[HH:MM:SS] LEVEL <8-char trace> <short message>` — one line per record, for a human watching the process run rather than a machine parsing it."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = _resolve_trace_id(record)
        trace_display = trace_id[:8] if trace_id else "-" * 8

        fields = _record_extra_fields(record)
        renderer = _EVENT_RENDERERS.get(fields.get("event"), _render_default)
        message = renderer(fields, record)

        time_str = self.formatTime(record, "%H:%M:%S")
        return f"[{time_str}] {record.levelname:<5} {trace_display}  {message}"


class _PersistenceLogHandler(logging.Handler):
    """Lands a full, unabbreviated copy of every emitted record in the active deployment's own database, through `PersistenceInterface .write_log_entry` — the one write path (work_plan..."""

    def __init__(self, persistence: "PersistenceInterface"):
        super().__init__()
        self._persistence = persistence
        self._warned = False

    def emit(self, record: logging.LogRecord) -> None:
        durable_telemetry_events = {
            "api_request_finished",
            "model_invocation_finished",
            "provider_request_finished",
            "provider_request_failed",
            "queue_started",
            "stage_finished",
        }
        if (
            getattr(record, "telemetry_only", False)
            and getattr(record, "event", None) not in durable_telemetry_events
        ):
            return
        try:
            trace_id = _resolve_trace_id(record) or None

            details: dict[str, Any] = {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "profile_name": _active_profile_name,
            }
            details.update(_record_extra_fields(record))
            if record.exc_info:
                details["exc_info"] = logging.Formatter().formatException(record.exc_info)

            self._persistence.write_log_entry(trace_id, details)
        except Exception as exc:
            if not self._warned:
                self._warned = True
                print(f"warning: DB-backed log sink is not writable, continuing with stdout logging only: {exc}", file=sys.stderr)


def _ensure_utf8_stream(stream: Any) -> None:
    """Reconfigure a real console/file stream to UTF-8 so the human- readable formatter's `→`/`…` survive redirection — found live: on Windows, a stream attached to an actual console o..."""

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return

    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


def configure_logging(profile_name: str, level: int | None = None, persistence: "PersistenceInterface | None" = None) -> None:
    """Configure the root logger to emit one JSON object per record."""

    global _active_profile_name
    _active_profile_name = profile_name

    _ensure_utf8_stream(sys.stderr)  # see that function's own docstring — the
    _ensure_utf8_stream(sys.stdout)  # human-readable formatter's "→"/"…" need it

    root = logging.getLogger()
    root.setLevel(level if level is not None else (logging.DEBUG if base_config.DEBUG_FLAG else logging.INFO))
    root.handlers.clear()
    if not any(isinstance(log_filter, _RedundantCrewAIErrorFilter) for log_filter in root.filters):
        root.addFilter(_RedundantCrewAIErrorFilter())

    if base_config.LOG_CONSOLE_JSON_ENABLED:
        json_handler = logging.StreamHandler(stream=sys.stdout)
        json_handler.setFormatter(_JsonFormatter())
        root.addHandler(json_handler)

    console_handler = logging.StreamHandler(stream=sys.stderr)
    console_handler.setFormatter(_HumanReadableFormatter())
    root.addHandler(console_handler)

    if persistence is not None:
        root.addHandler(_PersistenceLogHandler(persistence))


def verbose_logging_enabled() -> bool:
    """Whether `DEBUG_VERBOSE_LOGGING` is on."""

    return base_config.DEBUG_FLAG


def deep_debug_enabled() -> bool:
    """Whether full structured model I/O persistence is enabled."""

    return base_config.DEEP_DEBUG


def log_ai_interaction(agent_name: str, prompt: str, response: str, stage: str = "", trace_id: str | None = None) -> None:
    """Log one exact model exchange — the full prompt sent and the full raw response received, before any parsing — at DEBUG, through the same structured JSON logger and trace-ID mecha..."""

    if not base_config.DEEP_DEBUG:
        return

    logger.info(
        "model interaction",
        extra={
            "event": "model_io",
            "agent": agent_name,
            "stage": stage or get_current_stage(),
            "prompt": prompt,
            "response": response,
            "trace_id": trace_id or get_trace_id(),
        },
    )
