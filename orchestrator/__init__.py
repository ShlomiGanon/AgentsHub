"""Public orchestration facade."""

import sys
from types import ModuleType

from orchestrator import event_queue, reasoning
from orchestrator.reasoning import (
    InsightsAgent,
    MainAgent,
    OrchestrationParseError,
    build_insight,
    construct_core_agents,
    construct_insights_agent,
    determine_closure,
    look_up_precedent,
)
from orchestrator.event_queue import SerialEventQueue

decisions = reasoning
main_agent = reasoning
precedent = reasoning
question_flow = reasoning
runtime = event_queue
queue = event_queue
sys.modules[f"{__name__}.decisions"] = reasoning
sys.modules[f"{__name__}.main_agent"] = reasoning
sys.modules[f"{__name__}.precedent"] = reasoning
sys.modules[f"{__name__}.question_flow"] = reasoning
sys.modules[f"{__name__}.runtime"] = event_queue
sys.modules[f"{__name__}.queue"] = event_queue

insights = ModuleType(f"{__name__}.insights")
insights.InsightsAgent = InsightsAgent
insights.build_insight = build_insight
insights.construct_core_agents = construct_insights_agent
insights._build_insight_prompt = reasoning._build_insight_prompt
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
