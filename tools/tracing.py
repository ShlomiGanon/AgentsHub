"""Trace-ID generation and propagation (work_plan.md §1.8).

One trace ID is generated when an event or message enters the system, and
attaches to every log record produced while handling it — through
extraction, every agent call, every tool call, and the final write.
Propagation uses a contextvar so it survives across function calls within
one logical flow without being threaded through every signature by hand.

`stage_context`/`get_current_stage` follow the same pattern for one more
piece of correlation: which decision (risk assessment, protocol
selection, task formulation, ...) a given model call belongs to.
`agents.base.Agent.process(text, allowed_tools)` (§3.1) is deliberately
the *only* public entry point every caller reaches an agent through, so
there is no signature to add a "stage" parameter to without touching that
contract — the same reasoning `_current_allowed_tools` in `agents/base.py`
already applies to `allowed_tools`. Each orchestrator-level call site
(risk assessment, protocol selection, task formulation, ...) wraps its own
`agent.process(...)` call in `with stage_context("..."):`; the one real
model-I/O logging choke point (`agents/adapter.py::invoke`) reads it back
via `get_current_stage()` when building a debug-gated log record, with no
signature threaded through the agent framework itself.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")
_current_stage: ContextVar[str] = ContextVar("current_stage", default="")


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


def set_trace_id(trace_id: str) -> None:
    """Make `trace_id` current from this call onward, on this thread,
    with no corresponding reset — unlike `trace_context`, which is for a
    bounded block that should stop being "current" the moment it exits.

    Exists for exactly one caller shape: an API route (`api/ingestion.py`,
    `api/ingestion.py`, `api/operations.py`) that mints a trace ID and does real
    work before returning a response — and needs that same trace ID still
    current *after* the route function returns, because werkzeug's own
    request-log line (`WSGIRequestHandler.log_request`, via the stdlib's
    `send_response`) is written only once the WSGI app's response is fully
    built, which is strictly after the view function — and any
    `trace_context` block inside it — has already returned and reset the
    contextvar back to `""`. `api/app.py`'s `before_request` hook clears
    the trace ID to `""` at the start of every request specifically so an
    unrelated route that never calls this (a plain read like `GET
    /SYSTEM`) doesn't inherit a stale value left over from whichever
    request previously ran on the same thread.
    """

    _current_trace_id.set(trace_id)


def get_current_stage() -> str:
    """Return the stage name for the model call currently in progress.

    Returns the empty string outside any `stage_context` — a caller
    (`agents/adapter.py`) that gets this must not assume it's non-empty;
    it means the invocation happened outside any of the orchestrator's
    own decision points (e.g. a direct call in a test).
    """

    return _current_stage.get()


@contextmanager
def stage_context(stage: str):
    """Make `stage` current for the duration of the `with` block,
    restoring the previous value on exit — the same shape as
    `trace_context`, for the same reason.
    """

    token = _current_stage.set(stage)
    try:
        yield
    finally:
        _current_stage.reset(token)
