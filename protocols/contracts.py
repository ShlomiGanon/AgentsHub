"""The protocol model (work_plan.md §4.1) and the Step contract (§1.2/§4.4)."""

from dataclasses import dataclass
from enum import IntEnum


EVENT_DATA_FIELDS = (
    "classification",
    "area",
    "entities",
    "description",
    "severity",
    "occurred_at",
    "availability_start",
    "availability_end",
    "business_fields",
)


@dataclass(frozen=True)
class DirectToolExecution:
    """Protocol-declared projection from validated event data to one tool call."""

    tool_name: str
    argument_sources: tuple[tuple[str, str], ...] = ()
    required_arguments: tuple[str, ...] = ()
    required_when: tuple[tuple[str, str, object], ...] = ()
    business_field_enums: tuple[tuple[str, tuple[str, ...]], ...] = ()


class CriticalityLevel(IntEnum):
    """Ordered so `max()` picks the most critical of several tied candidates (§6.4, later) — the same pattern as auth.permissions' PermissionLevel."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class Protocol:
    name: str
    description: str
    participating_agents: tuple[str, ...]
    approved_tools: tuple[str, ...]
    expected_success_output: str
    criticality: CriticalityLevel
    approval_flag: bool
    requires_confirmation: bool = False
    commander_only: bool = False
    # None means task decomposition remains model-driven.  A tuple (including
    # an empty tuple) explicitly declares that this is a single deterministic
    # step and lists the event fields that step cannot execute without.
    deterministic_required_event_fields: tuple[str, ...] | None = None
    direct_tool_execution: DirectToolExecution | None = None


@dataclass(frozen=True)
class Step:
    """The contract between the Main Agent and the executor (§1.2, §4.4)."""

    agent_name: str
    task_text: str
    allowed_tools: tuple[str, ...]
    step_id: str = ""
    depends_on: tuple[str, ...] = ()
    required_event_fields: tuple[str, ...] = ()
    direct_tool_name: str | None = None
    direct_tool_arguments: dict[str, object] | None = None


class ProtocolEditError(Exception):
    """A protocol source edit was rejected."""


@dataclass(frozen=True)
class StepOutcome:
    step: Step
    result_text: str | None
    attempt_count: int
    succeeded: bool
    failure_reason: str | None = None
    status: str = "succeeded"
    missing_event_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtocolRunResult:
    step_outcomes: tuple[StepOutcome, ...]
    completed: bool
    failed_step_index: int | None = None
    failed_step_agent: str | None = None
    failure_cause: str | None = None
    waiting_for_event_data: bool = False
    missing_event_fields: tuple[str, ...] = ()
