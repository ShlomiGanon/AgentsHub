"""Public orchestration facade."""

import sys
from types import ModuleType

from orchestrator import decisions, runtime
from orchestrator.decisions import (
    InsightsAgent,
    MainAgent,
    OrchestrationParseError,
    build_insight,
    construct_core_agents,
    construct_insights_agent,
    determine_closure,
    look_up_precedent,
)
from orchestrator.runtime import SerialEventQueue

main_agent = decisions
precedent = decisions
queue = runtime
sys.modules[f"{__name__}.main_agent"] = decisions
sys.modules[f"{__name__}.precedent"] = decisions
sys.modules[f"{__name__}.queue"] = runtime

insights = ModuleType(f"{__name__}.insights")
insights.InsightsAgent = InsightsAgent
insights.build_insight = build_insight
insights.construct_core_agents = construct_insights_agent
insights._build_insight_prompt = decisions._build_insight_prompt
sys.modules[insights.__name__] = insights

from orchestrator import flows
from orchestrator.flows import FlowDeps, FlowResult, assemble_core_agents, process_message

__all__ = [
    "FlowDeps",
    "FlowResult",
    "InsightsAgent",
    "MainAgent",
    "OrchestrationParseError",
    "SerialEventQueue",
    "assemble_core_agents",
    "build_insight",
    "process_message",
]
