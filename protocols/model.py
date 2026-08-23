"""The protocol model (work_plan.md §4.1) and the Step contract (§1.2/§4.4).

A protocol is a named playbook, chosen by the Main Agent reading
descriptions — not bound to event types. `criticality` exists for exactly
one purpose: breaking ties between candidates that fit a report equally
well; it is not a risk level and does not decide whether a protocol runs.
`approval_flag` is the only field that stops a run, and is kept fully
independent of criticality — a critical protocol may run instantly and
unattended, a low-criticality one may always need a human first.
"""

from dataclasses import dataclass
from enum import IntEnum


class CriticalityLevel(IntEnum):
    """Ordered so `max()` picks the most critical of several tied
    candidates (§6.4, later) — the same pattern as auth.permissions'
    PermissionLevel.
    """

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
    """The contract between the Main Agent and the executor (§1.2, §4.4).

    Exactly these three fields, nothing else — the executor must be able
    to run a step without consulting the protocol that produced it.
    """

    agent_name: str
    task_text: str
    allowed_tools: tuple[str, ...]
