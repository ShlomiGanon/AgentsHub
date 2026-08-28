"""Structured logging (work_plan.md §1.8)."""

import json
import logging
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Callable

from config import base as base_config
if TYPE_CHECKING:
    from persistence import PersistenceInterface

_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")
_current_stage: ContextVar[str] = ContextVar("current_stage", default="")


def new_trace_id() -> str:
    return uuid.uuid4().hex


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
    try:
        yield
    finally:
        _current_stage.reset(token)

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
        # Found live: before this branch existed, a real NO_MATCH selection
        # (orchestrator.main_agent's own status, distinct from "ambiguous")
        # fell through to the line above with an empty candidate list —
        # printing "ambiguous among []" for an event that was never
        # actually ambiguous, only ever mismatched here in the console
        # summary (the stored data and every other rendering — job status,
        # notifications — always had it right).
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
}


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


def log_ai_interaction(agent_name: str, prompt: str, response: str, stage: str = "", trace_id: str | None = None) -> None:
    """Log one exact model exchange — the full prompt sent and the full raw response received, before any parsing — at DEBUG, through the same structured JSON logger and trace-ID mecha..."""

    if not base_config.DEBUG_FLAG:
        return

    logger.debug(
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
