"""The agent descriptor (work_plan.md §3.2).

Holds the declarative shape of one agent — its name, role, system prompt,
tool list, and the model it was constructed with. Exposed through the
registry (§3.8) so the Main Agent can read roles and profile validation
can resolve protocol references without constructing anything or reaching
into agent internals.

The live CrewAI instance is deliberately *not* held here — see
agents/base.py's module docstring for why it lives on the Agent instance
instead, built lazily on first use.
"""

from dataclasses import dataclass

from agents.tooling import ToolInfo


@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    role: str
    system_prompt: str
    tools: tuple[ToolInfo, ...]
    model: str
