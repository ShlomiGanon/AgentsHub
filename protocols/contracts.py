"""The protocol model (work_plan.md §4.1) and the Step contract (§1.2/§4.4)."""

from dataclasses import dataclass
from enum import IntEnum


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


@dataclass(frozen=True)
class Step:
    """The contract between the Main Agent and the executor (§1.2, §4.4)."""

    agent_name: str
    task_text: str
    allowed_tools: tuple[str, ...]
    step_id: str = ""
    depends_on: tuple[str, ...] = ()


class ProtocolEditError(Exception):
    """A protocol source edit was rejected."""


@dataclass(frozen=True)
class StepOutcome:
    step: Step
    result_text: str | None
    attempt_count: int
    succeeded: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class ProtocolRunResult:
    step_outcomes: tuple[StepOutcome, ...]
    completed: bool
    failed_step_index: int | None = None
    failed_step_agent: str | None = None
    failure_cause: str | None = None
