"""Structured logging (work_plan.md §1.8).

Emits logs as structured records with named fields, not formatted
sentences, so a run can later be reassembled by querying rather than by
reading. Every record carries the active profile name and, where one is in
progress, the current trace ID (tools.tracing).

This module sets up *how* records are emitted. The named events later
sections must log (intent decision, extraction result, risk assessment,
protocol selection, hold kind, precedent lookup, per-step task/result,
tool calls including blocked ones and retries, insight text and verdict)
are logged from the call sites that produce them, once those sections
exist — this is the infrastructure they will call into.
"""

import json
import logging
import sys
from typing import Any

import config.base as base_config
from tools.tracing import get_trace_id

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)

_active_profile_name = ""


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


def configure_logging(profile_name: str, level: int = logging.INFO) -> None:
    """Configure the root logger to emit one JSON object per record.

    `profile_name` is stamped on every record from here on. Call this once,
    right after a profile loads, so two deployments logging to the same
    collector never blur together.
    """

    global _active_profile_name
    _active_profile_name = profile_name

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)


def log_ai_interaction(agent_name: str, prompt: str, response: str, trace_id: str | None = None) -> None:
    """Print an exact model exchange only when ephemeral debugging is enabled."""

    if not base_config.DEBUG_FLAG:
        return

    active_trace_id = trace_id or get_trace_id()

    print(f"[AI interaction: {agent_name}; trace_id={active_trace_id}; prompt]")
    print(prompt)
    print(f"[AI interaction: {agent_name}; trace_id={active_trace_id}; response]")
    print(response)
