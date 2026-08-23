"""Tool declaration and exposure (work_plan.md §3.3, §3.4).

The `@tool` decorator is how a concrete agent declares one of its methods
as a tool: a name, a description written for a model to act on, and the
side-effect marks the retry policy and the question flow both depend on.
Marks are required explicitly, with no default — an unmarked tool is
exactly the path to a duplicated action in the field.

`exposed_tools_for` derives the list from the agent's actual methods
rather than a hand-maintained constant, so a tool cannot be added without
appearing here.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    side_effecting: bool
    idempotent: bool | None  # meaningful, and required, only when side_effecting is True


_TOOL_META_ATTR = "_agent_tool_info"


def tool(name: str, description: str, *, side_effecting: bool, idempotent: bool | None = None):
    """Mark a method as an exposed tool.

    Raises at class-definition time (decoration happens when the class
    body executes) rather than waiting for exposure or validation to
    discover the problem later:
    - `side_effecting` has no default — it must always be passed.
    - `idempotent` is required when `side_effecting` is True, and
      forbidden (must be left as `None`) when it is False, where it would
      have no meaning.
    """

    if side_effecting and idempotent is None:
        raise ValueError(f"tool '{name}': side_effecting=True requires idempotent to be explicitly True or False")

    if not side_effecting and idempotent is not None:
        raise ValueError(f"tool '{name}': idempotent has no meaning for a read-only tool (side_effecting=False)")

    info = ToolInfo(name=name, description=description, side_effecting=side_effecting, idempotent=idempotent)

    def _decorator(func: Callable) -> Callable:
        setattr(func, _TOOL_META_ATTR, info)
        return func

    return _decorator


def tool_info_of(method: Callable) -> ToolInfo | None:
    """Return the ToolInfo a method was decorated with, or None."""

    return getattr(method, _TOOL_META_ATTR, None)


def exposed_tools_for(agent_instance) -> tuple[ToolInfo, ...]:
    """Introspect `agent_instance` for every `@tool`-marked method.

    Callable by anything, with no privileged access — profile validation,
    the Main Agent, and the question flow all call this the same way.
    """

    tools = []
    for attr_name in dir(type(agent_instance)):
        method = getattr(type(agent_instance), attr_name, None)
        info = tool_info_of(method)
        if info is not None:
            tools.append(info)

    return tuple(tools)
