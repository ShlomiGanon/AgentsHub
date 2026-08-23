"""Trace-ID generation and propagation (work_plan.md §1.8).

One trace ID is generated when an event or message enters the system, and
attaches to every log record produced while handling it — through
extraction, every agent call, every tool call, and the final write.
Propagation uses a contextvar so it survives across function calls within
one logical flow without being threaded through every signature by hand.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")


def new_trace_id() -> str:
    """Generate a fresh trace ID. Does not set it as current."""

    return uuid.uuid4().hex


def get_trace_id() -> str:
    """Return the trace ID for the flow currently in progress.

    Returns the empty string outside any trace_context — that is a
    programming error upstream (something is logging before an event or
    message entered the system), not something to paper over with a
    generated fallback.
    """

    return _current_trace_id.get()


@contextmanager
def trace_context(trace_id: str | None = None):
    """Make `trace_id` (or a freshly generated one) current for the
    duration of the `with` block, restoring the previous value on exit.
    """

    token = _current_trace_id.set(trace_id or new_trace_id())
    try:
        yield _current_trace_id.get()
    finally:
        _current_trace_id.reset(token)
