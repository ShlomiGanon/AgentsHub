"""Structured logging (work_plan.md §1.8).

Emits logs as structured records with named fields, not formatted
sentences, so a run can later be reassembled by querying rather than by
reading. Every record carries the active profile name and, where one is in
progress, the current trace ID (tools.tracing).

This module sets up *how* records are emitted. The named events (intent
decision, extraction result, risk assessment, protocol selection, hold
kind, precedent lookup, per-step task/result, tool calls including
blocked ones and retries, insight text and verdict) are logged at INFO
from the call sites that produce them — always on, since an operator
following `docs/operator_guide.md` needs them in normal operation.

Three handlers can be attached to the root logger, all reading from the
exact same `logger.*` calls — no call site knows or cares which of them
are attached:

- The JSON `StreamHandler` (`_JsonFormatter`, below), on stdout — the
  original, still-full-detail stream. Untouched by the two follow-ups
  below: several tests capture stdout and parse every line as JSON
  (`tests/test_logging.py`, `tests/test_integration_end_to_end_flow.py`,
  ...), and nothing here breaks that contract.
- `_PersistenceLogHandler` (§1.8 follow-up) — a full, unabbreviated copy
  of every record in the deployment's own database, attached only when
  `configure_logging` is given a `persistence` handle.
- `_HumanReadableFormatter`'s `StreamHandler` (§1.8 follow-up, this
  pass), on **stderr** — a short, one-line-per-record summary for a human
  watching the process run, e.g. `[22:29:40] INFO  8f697469  intent
  classified → question (...)`. Deliberately a *different stream* than
  the JSON handler, not a replacement for it: stdout stays the full,
  parseable record (redirect it to a file or a collector); stderr is
  what an operator actually watches in a terminal. Running the server
  with stdout redirected (`python -m api.app profiles.demo >server.jsonl`)
  gives a terminal with only the human-readable lines and nothing else.

`log_ai_interaction` is the one exception: the full model prompt and raw
response, logged at DEBUG, emitted only when `config.base.DEBUG_FLAG` is
on (`DEBUG_VERBOSE_LOGGING` in the environment). This can contain the
full original event/message text verbatim — treat it as sensitive
diagnostic output, not something to leave on in normal operation.
`verbose_logging_enabled()` lets a caller (`agents/adapter.py`) skip
building the (potentially large) payload entirely when the flag is off,
rather than build it and rely on this function's own internal check to
discard it — logging a large prompt/response must cost nothing when off.
"""

import json
import logging
import sys
from typing import TYPE_CHECKING, Any, Callable

import config.base as base_config
from tools.tracing import get_current_stage, get_trace_id

if TYPE_CHECKING:
    from persistence.interface import PersistenceInterface

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)

_active_profile_name = ""

logger = logging.getLogger(__name__)


def _resolve_trace_id(record: logging.LogRecord) -> str:
    """The trace ID a record belongs to: an explicit one a call site passed
    via `extra={"trace_id": ...}` (e.g. `log_ai_interaction`, which may log
    slightly after the trace context that produced it has moved on) wins;
    otherwise the trace currently in progress. Shared by the JSON formatter
    and the DB handler below so the two always agree on whose trace a
    record belongs to.
    """

    explicit = getattr(record, "trace_id", None)
    return explicit if explicit is not None else get_trace_id()


def _record_extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Every field a call site passed via `extra=`, beyond the standard
    `LogRecord` attributes — shared by the JSON formatter and the DB
    handler so neither can drift from what the other captures.
    """

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

        # Anything passed via logging's `extra=` lands as a plain attribute
        # on the record; surface it as a named field rather than dropping it.
        payload.update(_record_extra_fields(record))

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _truncate(value: Any, limit: int = 70) -> str:
    """Render `value` as a string short enough for one terminal line —
    never wrap, never dump a whole prompt/response/task/result. The full
    value is never lost: it's still in the JSON stream and the DB sink,
    unabbreviated, per this module's own docstring.
    """

    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _render_default(fields: dict[str, Any], record: logging.LogRecord) -> str:
    """Every record with no `event` field this module recognizes — third-
    party library lines (crewai, httpx, werkzeug, litellm's bare `root`
    logger, ...) and anything logged without `extra={"event": ...}` —
    falls back to the record's own message, truncated. Still one line,
    still readable, just not specially rendered.
    """

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
        # (orchestrator.selection's own status, distinct from "ambiguous")
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


# One renderer per named event (§1.8's own eleven, plus this pass's own
# gap-filling additions) — keyed by the `event` value every real call site
# already passes via `extra=`. Anything not listed here (including every
# third-party logger — crewai, httpx, werkzeug, litellm) falls through to
# `_render_default`, never a KeyError.
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
    """`[HH:MM:SS] LEVEL  <8-char trace>  <short message>` — one line per
    record, for a human watching the process run rather than a machine
    parsing it. See this module's own docstring for why this is a
    *second*, additional handler (on stderr) rather than a change to the
    JSON handler already on stdout.

    The message is a genuinely short summary built from the record's own
    structured `extra=` fields (`_EVENT_RENDERERS`, above) — never the
    raw `message` field truncated, and never a dumped dict. Any long
    value (a task, a result, a reason) is truncated with `_truncate`
    rather than left to wrap the terminal; the full value is still in the
    JSON stream and the DB sink, unabbreviated.
    """

    def format(self, record: logging.LogRecord) -> str:
        trace_id = _resolve_trace_id(record)
        trace_display = trace_id[:8] if trace_id else "-" * 8

        fields = _record_extra_fields(record)
        renderer = _EVENT_RENDERERS.get(fields.get("event"), _render_default)
        message = renderer(fields, record)

        time_str = self.formatTime(record, "%H:%M:%S")
        return f"[{time_str}] {record.levelname:<5} {trace_display}  {message}"


class _PersistenceLogHandler(logging.Handler):
    """Lands a full, unabbreviated copy of every emitted record in the
    active deployment's own database, through `PersistenceInterface
    .write_log_entry` — the one write path (work_plan.md §1.8 follow-up:
    the JSON formatter above only ever reached stdout, never anywhere
    durable). No call site changes: this attaches as a second handler on
    the same root logger the JSON `StreamHandler` already uses, so every
    existing `logger.*` call reaches both, unmodified.

    Captures exactly what `_JsonFormatter` captures — level, logger name,
    message, profile name, every `extra=` field, and the formatted
    exception, if any — built from the same two shared helpers above so
    the DB row and the stdout line can never quietly diverge. `trace_id`
    travels as `write_log_entry`'s own separate parameter rather than a
    `details` key (see that method's docstring); `timestamp` is not
    included here at all — `write_log_entry` captures it itself, at write
    time, per its own contract.

    Never lets a write failure reach application code (work_plan.md §1.8
    follow-up's own explicit requirement): any exception here — including
    the persistence layer's own deadlock guard on a reentrant write, see
    `persistence.sqlite_backend.SQLitePersistence._submit_write` — is
    swallowed, with a one-time fallback warning to stderr so a broken sink
    is discoverable without spamming one line per subsequent log record.
    """

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
    """Reconfigure a real console/file stream to UTF-8 so the human-
    readable formatter's `→`/`…` survive redirection — found live: on
    Windows, a stream attached to an actual console often already
    negotiates UTF-8, but the *same* process with stderr redirected to a
    file (`python -m api.app profiles.demo 2>console.log`, an entirely
    normal way to run this) falls back to the system's ANSI code page
    (`cp1255`, `cp1252`, ...), which cannot represent either character —
    silently corrupting them into `?`/`�` in the saved file, not just a
    display glitch.

    `errors="backslashreplace"` is defense in depth for the (practically
    unreachable, once the encoding above is UTF-8) case of a character
    even UTF-8 can't encode — a readable `\\uXXXX` escape, never silent
    data loss.

    Guarded by `hasattr` and a broad `except`: a real `io.TextIOWrapper`
    (an actual console or redirected file) has `.reconfigure`; a test
    double standing in for `sys.stdout`/`sys.stderr` (`io.StringIO`,
    `capsys`'s writer) usually doesn't, and must be left alone rather than
    raising out of `configure_logging`.
    """

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return

    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


def configure_logging(profile_name: str, level: int | None = None, persistence: "PersistenceInterface | None" = None) -> None:
    """Configure the root logger to emit one JSON object per record.

    `profile_name` is stamped on every record from here on. Call this once,
    right after a profile loads, so two deployments logging to the same
    collector never blur together.

    `level` defaults to `DEBUG` when `config.base.DEBUG_FLAG` is on and
    `INFO` otherwise — read once here, at startup, not on every log call.
    The DEBUG-gated records this enables (`log_ai_interaction` below, and
    the internal-detail records in `agents/`, `history/`, `protocols/`)
    are otherwise indistinguishable in cost from any other logging call;
    Python's own level check is what keeps them from being emitted (or,
    for `log_ai_interaction` specifically, from even being *built* — see
    below) when the flag is off. Pass an explicit `level` to override.

    `persistence`, when given, attaches `_PersistenceLogHandler` alongside
    the stdout `StreamHandler` — every record then also lands a full row
    in that deployment's own database. Only a process that actually owns a
    persistence handle can do this: `api.app.build_context` passes one
    (opening persistence before calling this, specifically so it can);
    `bot.app.build_deps` has no persistence access at all
    (`docs/allowed_calls.md`: "bot calls only api") and always omits it,
    same as before this parameter existed.

    A third handler — `_HumanReadableFormatter`, on stderr — is always
    attached, independent of `persistence`: it needs no external resource
    and every process running this benefits from a readable console.

    The stdout JSON handler itself is skipped when `config.base
    .LOG_CONSOLE_JSON_ENABLED` is off (`LOG_CONSOLE_JSON=false`/`0` in the
    environment) — an opt-in way to get a terminal with only the
    human-readable lines, without removing the JSON stream as a
    capability: `LOG_CONSOLE_JSON_ENABLED` defaults to on, so every
    existing caller that doesn't set the variable — including every test
    in this suite that captures and parses stdout as JSON — sees exactly
    today's behavior, unchanged. This flag never touches
    `_PersistenceLogHandler`, below: the DB sink keeps getting full detail
    regardless of whether the JSON stream is visible on the console.
    """

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
    """Whether `DEBUG_VERBOSE_LOGGING` is on. A caller about to build a
    potentially large payload (`agents/adapter.py`'s full prompt/response)
    should check this *before* building it, rather than build it
    unconditionally and rely on `log_ai_interaction`'s own internal check
    to discard it — that would defeat the point of gating it at all.
    """

    return base_config.DEBUG_FLAG


def log_ai_interaction(agent_name: str, prompt: str, response: str, stage: str = "", trace_id: str | None = None) -> None:
    """Log one exact model exchange — the full prompt sent and the full
    raw response received, before any parsing — at DEBUG, through the
    same structured JSON logger and trace-ID mechanism as every other log
    record (never a second logging mechanism). Only emitted when
    `config.base.DEBUG_FLAG` is on; a no-op call otherwise, kept cheap and
    unconditional here as defense-in-depth for any caller that didn't
    already check `verbose_logging_enabled()` first.

    Sensitive: `prompt`/`response` can contain the full original event or
    message text verbatim. This is a diagnostic mode, not something to
    leave on in normal operation — see docs/operator_guide.md.
    """

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
