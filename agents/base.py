"""The abstract agent class (work_plan.md §3.1, §3.7).

`process(text, allowed_tools) -> AgentResult` is the only public entry
point — every caller in the system reaches every agent through this
signature and no other. Everything common lives here: holding the
descriptor, the (lazily built) CrewAI instance, the exposure function, and
the invocation path. A concrete agent should have to write only what
makes it different: a class-level `name`, `role`, `system_prompt`, and its
tool methods.

Tool-permission enforcement (§3.7) happens per invocation, never once at
construction — `allowed_tools` comes from the protocol and differs
between runs, so the same agent legitimately has different permissions on
two consecutive events. Each tool method is wrapped once, at construction,
in a function that checks a contextvar `process()` sets for the *current*
call; binding the check to a list fixed at construction would make that
impossible to express.
"""

import logging
from contextvars import ContextVar
from functools import wraps
from typing import Callable

from agents import adapter
from agents.descriptor import AgentDescriptor
from agents.results import AgentResult, parse_agent_output
from agents.tooling import ToolInfo, exposed_tools_for, tool_info_of
from tools.tracing import get_trace_id

logger = logging.getLogger(__name__)

_REQUIRED_CLASS_ATTRS = ("name", "role", "system_prompt")

_current_allowed_tools: ContextVar[frozenset | None] = ContextVar("current_allowed_tools", default=None)


def _wrap_tool(agent_name: str, bound_method: Callable, info: ToolInfo) -> Callable:
    @wraps(bound_method)
    def _wrapped(*args, **kwargs):
        allowed = _current_allowed_tools.get()
        if allowed is None or info.name not in allowed:
            logger.info(
                "tool call blocked: not in this call's allowed_tools",
                extra={"event": "tool_blocked", "agent": agent_name, "tool": info.name, "trace_id": get_trace_id()},
            )
            return f"Tool '{info.name}' is not permitted for this task."

        result = bound_method(*args, **kwargs)
        # DEBUG, not INFO: an allowed, successful tool call is routine
        # internal detail, not a decision an operator needs to see in
        # normal operation — unlike a *blocked* call above, which stays
        # at INFO (§1.8's own list; never move it).
        logger.debug(
            "tool call",
            extra={"event": "tool_call", "agent": agent_name, "tool": info.name, "trace_id": get_trace_id()},
        )
        return result

    return _wrapped


class Agent:
    # Required on every concrete subclass; checked at construction so a
    # missing one fails loudly the moment the agent is built, not the
    # first time it's asked to do something.
    name: str = ""
    role: str = ""
    system_prompt: str = ""

    # A sane framework-wide default (§3.10) — override on a subclass if
    # one agent genuinely needs longer.
    timeout_seconds: int = 60

    def __init__(self, model: str, api_key: str | None = None):
        missing = [attr for attr in _REQUIRED_CLASS_ATTRS if not getattr(type(self), attr, "")]
        if missing:
            raise TypeError(f"{type(self).__name__} must set class-level {', '.join(missing)}")

        self.model = model
        self.api_key = api_key
        self._wrapped_tools: dict[str, Callable] = {}

        tool_infos = exposed_tools_for(self)
        for attr_name in dir(type(self)):
            method = getattr(type(self), attr_name, None)
            info = tool_info_of(method)
            if info is not None:
                self._wrapped_tools[info.name] = _wrap_tool(self.name, getattr(self, attr_name), info)

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

    def process(self, text: str, allowed_tools: list[str]) -> AgentResult:
        token = _current_allowed_tools.set(frozenset(allowed_tools))
        try:
            raw_text = adapter.invoke(self.descriptor, self._wrapped_tools, text, self.timeout_seconds)
            return parse_agent_output(raw_text)
        finally:
            _current_allowed_tools.reset(token)
