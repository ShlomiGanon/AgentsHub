"""Immutable agent descriptors and tool declaration primitives."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    side_effecting: bool
    idempotent: bool | None


_TOOL_META_ATTR = "_agent_tool_info"


def tool(name: str, description: str, *, side_effecting: bool, idempotent: bool | None = None):
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
    return getattr(method, _TOOL_META_ATTR, None)


def exposed_tools_for(agent_instance) -> tuple[ToolInfo, ...]:
    tools = []
    for attr_name in dir(type(agent_instance)):
        method = getattr(type(agent_instance), attr_name, None)
        info = tool_info_of(method)
        if info is not None:
            tools.append(info)

    return tuple(tools)


@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    role: str
    system_prompt: str
    tools: tuple[ToolInfo, ...]
    model: str
    api_key: str | None = None
