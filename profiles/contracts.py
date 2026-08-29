"""Profile module specification (work_plan.md §1.4)."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:
    from agents import Agent
    from messages import MessageCatalog


@dataclass(frozen=True)
class AgentSpec:
    """One `AGENTS` entry: `cls`, a class taking `(model: str, api_key: str | None = None)` — normally a concrete `agents.base.Agent` subclass (docs/agent_authoring.md) — and `tier`, w..."""

    cls: "type[Agent]"
    tier: Literal["core", "sub"]


class ProfileLoadError(Exception):
    """The profile cannot be imported or constructed."""


class ProfileValidationError(Exception):
    """The profile violates one or more startup contracts."""

    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("\n".join(failures))


@dataclass(frozen=True)
class StageModelPolicy:
    tier: Literal["core", "sub"] = "core"
    max_output_tokens: int = 700
    timeout_seconds: float = 60.0
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"


@dataclass(frozen=True)
class OptimizationPolicy:
    planner_mode: Literal["legacy", "shadow", "merged"] = "legacy"
    operational_decision_mode: Literal["separate", "shadow", "merged"] = "separate"
    final_assessment_mode: Literal["separate", "low_risk_merged"] = "separate"
    structured_output_mode: Literal["off", "auto", "required"] = "off"
    event_queue_mode: Literal["serial", "policy"] = "serial"
    event_workers: int = 4
    event_queue_size: int = 100
    reserved_continuation_percent: int = 20
    specialist_fanout: int = 4
    provider_concurrency: int = 8
    notification_wait_seconds: int = 20
    direct_deadline_seconds: int = 75
    job_deadline_seconds: int = 180
    stage_model_policies: Mapping[str, StageModelPolicy] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedProfile:
    module_path: str
    profile_name: str
    agents: tuple
    protocols: tuple
    event_types: tuple[str, ...]
    areas: tuple[str, ...]
    db_path: str
    api_port: int
    retry_count: int
    risk_threshold: float
    lookback_window_days: int
    profile_file_hash: str
    default_language: Literal["en", "he"]
    message_catalog: "MessageCatalog"
    max_iter: int
    model_timeout_seconds: float
    core_agents: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    resolved_secrets: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    timezone_name: str = "UTC"
    conversation_history_turns: int = 0
    conversation_history_ttl_hours: int = 24
    optimization_policy: OptimizationPolicy = field(default_factory=OptimizationPolicy)

REQUIRED_PROFILE_ATTRS = (
    "PROFILE_NAME",
    "DEFAULT_LANGUAGE",
    "MAX_ITER",
    "MODEL_TIMEOUT_SECONDS",
    "AGENTS",
    "PROTOCOLS",
    "EVENT_TYPES",
    "AREAS",
    "DB_PATH",
    "API_PORT",
    "RETRY_COUNT",
    "RISK_THRESHOLD",
    "LOOKBACK_WINDOW_DAYS",
    "BOT_TOKEN_ENV",
    "MODEL_CREDENTIAL_ENVS",
)

HUMAN_ACTIVATION_TYPE = "human_activation"

PROTOCOL_REQUIRED_ATTRS = (
    "name",
    "description",
    "participating_agents",
    "approved_tools",
    "expected_success_output",
    "criticality",
    "approval_flag",
)


def agent_has_required_shape(agent: Any) -> bool:
    """True if `agent` looks enough like an Agent (§3.1) to validate."""

    exposed_tools = getattr(agent, "exposed_tools", None)
    return bool(getattr(agent, "name", None)) and callable(exposed_tools)


def protocol_missing_attrs(protocol: Any) -> list[str]:
    """Names from PROTOCOL_REQUIRED_ATTRS that `protocol` does not expose."""

    missing = []
    for attr_name in PROTOCOL_REQUIRED_ATTRS:
        if not hasattr(protocol, attr_name):
            missing.append(attr_name)

    return missing


@dataclass(frozen=True)
class AreaRegistry:
    areas: tuple[str, ...]

    def is_valid(self, area: str) -> bool:
        return area in self.areas


@dataclass(frozen=True)
class EventTypeRegistry:
    types: tuple[str, ...]

    def is_valid(self, event_type: str) -> bool:
        return event_type in self.types
