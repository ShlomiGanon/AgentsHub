"""The protocol model (work_plan.md §4.1) and the Step contract (§1.2/§4.4)."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Literal

from agents.contracts import InvocationPolicy


EVENT_DATA_FIELDS = (
    "classification",
    "area",
    "entities",
    "description",
    "severity",
    "occurred_at",
    # Availability fields (Stage 3, docs/bar_improves.md): a team member's own
    # reported absence interval and reason. Nullable/optional like every other
    # field here — the required-fields gate is what makes them mandatory for a
    # specific event type (e.g. "attendance"), never this tuple itself.
    "availability_start",
    "availability_end",
    "absence_reason",
)


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
    # A group's bound agent (orchestrator/group_routing.py) is a context hint for
    # protocol selection, never a hard filter that can make this protocol
    # structurally unreachable from a channel: a safety_critical protocol stays a
    # selection candidate from every group regardless of which specialist that
    # group is bound to.
    safety_critical: bool = False
    # False skips build_insight/judge_success (orchestrator/flows.py::_finish_protocol_assessment)
    # entirely in favor of a deterministic verdict (every step succeeded -> succeeded, else
    # failed) — for protocols whose steps are all "direct_tool" kind (below): recording a
    # report exactly as given IS correct behavior for these, not something that needs a model's
    # judgment call, and there is no specialist-agent reasoning left to synthesize an insight
    # about.
    needs_insight: bool = True
    # Item 9: eligible for the direct lane (orchestrator/direct_lane.py) -- a simple,
    # low-stakes, single-agent action (attendance/absence, movement, camera/equipment status,
    # shift status, a read-only lookup) whose tool(s) may be called directly from one cheap
    # classification call, skipping this protocol's own normal formulate_tasks/agent-step/
    # insight pipeline entirely when the message turns out to actually be that simple. Never
    # widens what a protocol allows -- the direct lane still only ever calls tools already in
    # this protocol's own approved_tools, and still falls back to the full pipeline (this same
    # protocol, run normally) whenever the message carries a threat, a risk indicator, or is
    # missing a required parameter.
    direct_lane_eligible: bool = False
    # A profile-supplied callable: event dict -> tuple[Step, ...], each already fully bound
    # (concrete direct_tool_kwargs resolved from the event's own extracted fields) or carrying
    # required_event_fields naming what's still missing. When set, orchestrator/flows.py's
    # _run_protocol calls this INSTEAD of formulate_tasks — skipping task_formulation (and its
    # task_rewrite fallback) entirely, so precedent text can never reach these protocols'
    # instructions, and no crewai/LLM call happens for the step(s) themselves. Global mechanism;
    # each profile supplies its own binder per protocol (this field IS the config).
    direct_tool_binder: "Callable[[dict], tuple[Step, ...]] | None" = None


@dataclass(frozen=True)
class Step:
    """The contract between the Main Agent and the executor (§1.2, §4.4)."""

    agent_name: str
    task_text: str
    allowed_tools: tuple[str, ...]
    step_id: str = ""
    depends_on: tuple[str, ...] = ()
    required_event_fields: tuple[str, ...] = ()
    # "agent" (default): executed via the specialist agent's own LLM turn (protocols/executor.py's
    # existing crewai-backed retry loop), unchanged. "direct_tool": `direct_tool_name` is called as
    # a plain Python method on the resolved agent instance with `direct_tool_kwargs` -- no crewai,
    # no LLM call, no task_formulation/task_rewrite for this step at all.
    kind: Literal["agent", "direct_tool"] = "agent"
    direct_tool_name: str = ""
    direct_tool_kwargs: dict = field(default_factory=dict)
    # Set by a direct_tool_binder for an "agent"-kind step whose task is a narrow, low-stakes
    # judgment call (e.g. report_team_movement's incident-linking decision) where the model's
    # own default invocation cost is unnecessary -- executed exactly like any other agent step,
    # just with a cheaper/faster invocation (see protocols/executor.py's own call site). None
    # (the default) keeps the agent's own default invocation policy, unchanged.
    invocation_policy: "InvocationPolicy | None" = None


class ProtocolEditError(Exception):
    """A protocol source edit was rejected."""


@dataclass(frozen=True)
class ResourceUnavailable:
    """A deterministic, DB-sourced signal that a tool needed one instance of some finite/
    coverage-limited resource and found none -- set by the tool itself (from its own
    persistence layer's status, e.g. a fleet count or a roster snapshot), never inferred from
    a specialist agent's own wording (see `agents.runtime.signal_resource_unavailable`)."""

    resource_kind: str
    area: str
    reason: str


@dataclass(frozen=True)
class StepOutcome:
    step: Step
    result_text: str | None
    attempt_count: int
    succeeded: bool
    failure_reason: str | None = None
    status: str = "succeeded"
    missing_event_fields: tuple[str, ...] = ()
    resource_unavailable: "ResourceUnavailable | None" = None


@dataclass(frozen=True)
class ProtocolRunResult:
    step_outcomes: tuple[StepOutcome, ...]
    completed: bool
    failed_step_index: int | None = None
    failed_step_agent: str | None = None
    failure_cause: str | None = None
    waiting_for_event_data: bool = False
    missing_event_fields: tuple[str, ...] = ()
