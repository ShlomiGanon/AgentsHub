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
from typing import Any

import config.base as base_config
from tools.tracing import get_current_stage, get_trace_id

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)

_active_profile_name = ""

logger = logging.getLogger(__name__)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "profile_name": _active_profile_name,
            "trace_id": get_trace_id(),
        }

        # Anything passed via logging's `extra=` lands as a plain attribute
        # on the record; surface it as a named field rather than dropping it.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(profile_name: str, level: int | None = None) -> None:
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
    """

    global _active_profile_name
    _active_profile_name = profile_name

    root = logging.getLogger()
    root.setLevel(level if level is not None else (logging.DEBUG if base_config.DEBUG_FLAG else logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)


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
