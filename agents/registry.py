"""The agent registry (work_plan.md §3.8).

Registers the three core agents (from the base configuration — still an
empty mapping, per profiles/loader.py's documented seam until §5.3/§6.1/
§6.9 exist) and every specialist agent instance the active profile
constructed. Accepts agents from no other source: built from exactly the
two collections passed to `build_agent_registry`, never anything that
registers itself at import time.
"""

from agents.base import Agent
from agents.runtime import AgentDescriptor


class DuplicateAgentNameError(Exception):
    """Two agents were registered under the same name."""


class AgentRegistry:
    def __init__(self, agents: dict[str, Agent]):
        self._agents = agents

    def get(self, name: str) -> Agent:
        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(f"no agent registered under '{name}'") from None

    def all(self) -> tuple[Agent, ...]:
        return tuple(self._agents.values())

    def descriptor_for(self, name: str) -> AgentDescriptor:
        # Role and tools returned together as one structure (§3.8) — both
        # live on the descriptor already, so this is just a named path to
        # it rather than a second call that could drift from `get`.
        return self.get(name).descriptor


def build_agent_registry(core_agents: dict[str, Agent], profile_agents: list[Agent]) -> AgentRegistry:
    agents: dict[str, Agent] = {}

    for agent in [*core_agents.values(), *profile_agents]:
        if agent.name in agents:
            raise DuplicateAgentNameError(f"agent name '{agent.name}' is registered more than once")
        agents[agent.name] = agent

    return AgentRegistry(agents)
