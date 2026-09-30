"""Correlation IDs for execution telemetry; never used for decisions."""

from contextlib import contextmanager
from contextvars import ContextVar

_invocation_id: ContextVar[str | None] = ContextVar("agent_invocation_id", default=None)
_last_finished_invocation_id: ContextVar[str | None] = ContextVar("last_finished_agent_invocation_id", default=None)


def current_invocation_id() -> str | None:
    return _invocation_id.get()


def last_finished_invocation_id() -> str | None:
    return _last_finished_invocation_id.get()


def record_finished_invocation_id(invocation_id: str | None) -> None:
    _last_finished_invocation_id.set(invocation_id)


@contextmanager
def invocation_scope(invocation_id: str):
    token = _invocation_id.set(invocation_id)
    try:
        yield
    finally:
        _invocation_id.reset(token)
