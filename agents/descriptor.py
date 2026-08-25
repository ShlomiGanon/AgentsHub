"""The agent descriptor (work_plan.md §3.2).

Holds the declarative shape of one agent — its name, role, system prompt,
tool list, and the model it was constructed with. Exposed through the
registry (§3.8) so the Main Agent can read roles and profile validation
can resolve protocol references without constructing anything or reaching
into agent internals.

The live CrewAI instance is deliberately *not* held here — see
agents/base.py's module docstring for why it lives on the Agent instance
instead, built lazily on first use.

`api_key` (optional, `None` by default) carries a resolved model tier's
actual API key value (`config.base.build_tier_model`'s `TierModel.api_key`
— see `docs/profile_spec.md`'s "Model tiers" section), so `agents/adapter.py`
can pass it straight through as `crewai.LLM(api_key=...)` rather than
relying on litellm's implicit, provider-named, process-wide env lookup.
`None` means no explicit key was given — `agents/adapter.py` falls back to
that implicit lookup unchanged, the same behavior this had before `api_key`
existed.
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
    api_key: str | None = None
