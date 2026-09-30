"""Public agent framework facade."""

import sys

from agents import contracts
from agents.contracts import (
    AgentDescriptor,
    InvocationPolicy,
    ProviderCapabilities,
    provider_capabilities,
    AgentFrameworkNotReadyError,
    AgentInvocationError,
    AgentModelError,
    AgentOutputParseError,
    AgentResult,
    AgentTimeoutError,
    AgentToolConstructionError,
    AgentWarmupError,
    is_retryable_invocation_error,
    ToolInfo,
    ToolResult,
    failed_tool_result,
    parse_agent_output,
    tool,
)

errors = contracts
results = contracts
sys.modules[f"{__name__}.errors"] = contracts
sys.modules[f"{__name__}.results"] = contracts

from agents import runtime
from agents.runtime import (
    Agent,
    AgentRegistry,
    DuplicateAgentNameError,
    ExactResultCapture,
    build_agent_registry,
    configure_provider_concurrency,
    configure_structured_output_mode,
    configure_invocation_limits,
    authenticated_request_identity,
    get_authenticated_request_identity,
    initialize_agent_runtime,
    make_exact_result_capture,
    set_invocation_deadline,
)
from agents.provider_telemetry import install_crewai_provider_telemetry
from agents.invocation_context import last_finished_invocation_id, record_finished_invocation_id

adapter = runtime
base = runtime
sys.modules[f"{__name__}.adapter"] = runtime
sys.modules[f"{__name__}.base"] = runtime
sys.modules[f"{__name__}.registry"] = runtime

from agents import standard_agents
from agents.standard_agents import HistoryAgent, ReferenceAgent
from agents.team_status_agent import TeamStatusAgent
from agents.surveillance_agent import SurveillanceAgent
from agents.friendly_forces_agent import FriendlyForcesAgent
from agents.neighboring_forces_agent import NeighboringForcesAgent
from agents.roster_agent import RosterAgent
from agents.fire_station_agents import DispatchAgent, HazmatAgent

history = standard_agents
reference = standard_agents
builtins = standard_agents
sys.modules[f"{__name__}.builtins"] = standard_agents
sys.modules[f"{__name__}.history"] = standard_agents
sys.modules[f"{__name__}.reference"] = standard_agents

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
    "AgentWarmupError",
    "is_retryable_invocation_error",
    "configure_provider_concurrency",
    "configure_structured_output_mode",
    "configure_invocation_limits",
    "authenticated_request_identity",
    "get_authenticated_request_identity",
    "install_crewai_provider_telemetry",
    "initialize_agent_runtime",
    "set_invocation_deadline",
    "last_finished_invocation_id",
    "record_finished_invocation_id",
    "DuplicateAgentNameError",
    "ExactResultCapture",
    "HistoryAgent",
    "InvocationPolicy",
    "ProviderCapabilities",
    "ReferenceAgent",
    "TeamStatusAgent",
    "SurveillanceAgent",
    "FriendlyForcesAgent",
    "NeighboringForcesAgent",
    "RosterAgent",
    "DispatchAgent",
    "HazmatAgent",
    "ToolInfo",
    "ToolResult",
    "build_agent_registry",
    "failed_tool_result",
    "make_exact_result_capture",
    "parse_agent_output",
    "provider_capabilities",
    "tool",
]
