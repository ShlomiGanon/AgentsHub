"""The agent registry (work_plan.md §3.8)."""

from agents.contracts import AgentDescriptor
from agents.runtime import Agent


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
        return self.get(name).descriptor


def build_agent_registry(core_agents: dict[str, Agent], profile_agents: list[Agent]) -> AgentRegistry:
    agents: dict[str, Agent] = {}

    for agent in [*core_agents.values(), *profile_agents]:
        if agent.name in agents:
            raise DuplicateAgentNameError(f"agent name '{agent.name}' is registered more than once")
        agents[agent.name] = agent

    return AgentRegistry(agents)
