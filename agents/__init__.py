"""Public agent framework facade."""

import sys

from agents import contracts
from agents.contracts import (
    AgentDescriptor,
    AgentFrameworkNotReadyError,
    AgentInvocationError,
    AgentModelError,
    AgentOutputParseError,
    AgentResult,
    AgentTimeoutError,
    AgentToolConstructionError,
    ToolInfo,
    parse_agent_output,
    tool,
)

errors = contracts
results = contracts
sys.modules[f"{__name__}.errors"] = contracts
sys.modules[f"{__name__}.results"] = contracts

from agents import runtime
from agents.runtime import Agent

adapter = runtime
base = runtime
sys.modules[f"{__name__}.adapter"] = runtime
sys.modules[f"{__name__}.base"] = runtime

from agents import builtins, registry
from agents.builtins import HistoryAgent, ReferenceAgent
from agents.registry import AgentRegistry, DuplicateAgentNameError, build_agent_registry

history = builtins
reference = builtins
sys.modules[f"{__name__}.history"] = builtins
sys.modules[f"{__name__}.reference"] = builtins

__all__ = [
    "Agent",
    "AgentDescriptor",
    "AgentFrameworkNotReadyError",
    "AgentInvocationError",
    "AgentModelError",
    "AgentOutputParseError",
    "AgentRegistry",
    "AgentResult",
    "AgentTimeoutError",
    "AgentToolConstructionError",
    "DuplicateAgentNameError",
    "HistoryAgent",
    "ReferenceAgent",
    "ToolInfo",
    "build_agent_registry",
    "parse_agent_output",
    "tool",
]
