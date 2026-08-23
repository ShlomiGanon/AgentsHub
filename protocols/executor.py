"""The protocol executor (work_plan.md §4.4, §4.6, §4.8).

`execute_steps` is the *one* function boundary through which a step list
runs — the seam §4.8 asks for: nothing else may execute a step except
through this call, so a later alternative execution mode (steps with
dependencies between them, instead of a flat ordered list) could be
selected here without redesigning the interface between the Main Agent
and the executor. Nothing about that mode exists yet — no field, flag, or
branch for it — the seam is this function's shape, not a feature.

Composes no task text — `step.task_text` (or, on a retry after an
unclear-task signal, exactly what `task_rewriter` returned) is sent to the
agent unmodified. The Insights Agent (§6.9, later) will be shown this text
as what the agent was asked, and it must be true.

Steps run strictly in order and stop at the first one that doesn't
succeed (§4.4's "return control... when every step has finished or the
run has failed") — every step that *did* succeed before that point is
preserved in the result, never discarded (§4.6). What §4.6 additionally
asks for — writing partial results onto the event record, notifying the
event's originator, moving on to the next event in the queue — belongs to
the orchestrator (§6.11), persistence, and the bot (§8.11), none of which
exist yet; this module's job ends at producing the data they'll need.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from protocols.model import Step
from protocols.retry import StepOutcome, execute_step_with_retry
from tools.tracing import get_trace_id

if TYPE_CHECKING:
    # agents.base is not an entry point (docs/allowed_calls.md) — Agent is
    # only ever used here as a type hint.
    from agents.base import Agent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProtocolRunResult:
    step_outcomes: tuple[StepOutcome, ...]
    completed: bool
    failed_step_index: int | None = None
    failed_step_agent: str | None = None
    failure_cause: str | None = None


def execute_steps(
    steps: list[Step],
    agents_by_name: dict[str, Agent],
    settings_store,
    task_rewriter: Callable[[Step, str], str] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ProtocolRunResult:
    outcomes: list[StepOutcome] = []

    for index, step in enumerate(steps):
        agent = agents_by_name[step.agent_name]

        logger.info(
            "executing step",
            extra={"event": "step_start", "agent": step.agent_name, "step_index": index, "task_text": step.task_text, "trace_id": get_trace_id()},
        )

        outcome = execute_step_with_retry(agent, step, settings_store, task_rewriter=task_rewriter, sleep_fn=sleep_fn)
        outcomes.append(outcome)

        logger.info(
            "step finished",
            extra={
                "event": "step_result",
                "agent": step.agent_name,
                "step_index": index,
                "succeeded": outcome.succeeded,
                "attempt_count": outcome.attempt_count,
                "result_text": outcome.result_text,
                "trace_id": get_trace_id(),
            },
        )

        if not outcome.succeeded:
            return ProtocolRunResult(
                step_outcomes=tuple(outcomes),
                completed=False,
                failed_step_index=index,
                failed_step_agent=step.agent_name,
                failure_cause=outcome.failure_reason,
            )

    return ProtocolRunResult(step_outcomes=tuple(outcomes), completed=True)
