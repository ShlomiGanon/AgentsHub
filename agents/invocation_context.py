"""Correlation IDs for execution telemetry; never used for decisions."""

from contextlib import contextmanager
from contextvars import ContextVar
import threading

_invocation_id: ContextVar[str | None] = ContextVar("agent_invocation_id", default=None)
_last_finished_invocation_id: ContextVar[str | None] = ContextVar("last_finished_agent_invocation_id", default=None)
_invocation_agent: ContextVar[str | None] = ContextVar("agent_invocation_agent", default=None)
_invocation_parent_agent: ContextVar[str | None] = ContextVar("agent_invocation_parent_agent", default=None)
_invocation_parent_id: ContextVar[str | None] = ContextVar("agent_invocation_parent_id", default=None)


_last_tools: dict[str, str] = {}
_last_tools_lock = threading.Lock()


def current_invocation_id() -> str | None:
    return _invocation_id.get()


def current_invocation_agent() -> str | None:
    return _invocation_agent.get()


def current_parent_agent() -> str | None:
    return _invocation_parent_agent.get()


def current_parent_invocation_id() -> str | None:
    return _invocation_parent_id.get()


def record_invocation_tool(invocation_id: str | None, tool_name: str) -> None:
    if invocation_id:
        with _last_tools_lock:
            _last_tools[invocation_id] = tool_name


def last_invocation_tool(invocation_id: str | None) -> str | None:
    if not invocation_id:
        return None
    with _last_tools_lock:
        return _last_tools.get(invocation_id)


def last_finished_invocation_id() -> str | None:
    return _last_finished_invocation_id.get()


def record_finished_invocation_id(invocation_id: str | None) -> None:
    _last_finished_invocation_id.set(invocation_id)
    if invocation_id:
        with _last_tools_lock:
            _last_tools.pop(invocation_id, None)


@contextmanager
def invocation_scope(invocation_id: str, agent_name: str | None = None, parent_agent: str | None = None, parent_invocation_id: str | None = None):
    variables = (_invocation_id, _invocation_agent, _invocation_parent_agent, _invocation_parent_id)
    tokens = tuple(variable.set(value) for variable, value in zip(variables, (invocation_id, agent_name, parent_agent, parent_invocation_id)))
    try:
        yield
    finally:
        for variable, token in zip(reversed(variables), reversed(tokens)):
            variable.reset(token)
