"""The retry policy (work_plan.md §4.5).

Internal to the Protocol Engine — called only by protocols.executor, never
a package entry point (parallel to profiles.validate's status).

Two kinds of non-success, two different responses: an execution failure
(a model error, a timeout, unparseable output — anything agents.errors
raises) resends the same task text unchanged, since the text wasn't the
problem. An unclear-task signal is never resent as-is — it goes back
through `task_rewriter` (the Main Agent's job, §6.8 — not built yet, so a
missing rewriter fails the step immediately rather than looping on
identical unclear text). Both count against the same attempt limit, read
fresh from the settings store on every call, never cached.

Retry safety is read conservatively: whether a non-idempotent,
side-effecting tool actually fired before a failed call can't be known —
CrewAI's internal reasoning is opaque, and this can't be verified without
a real installed crewai anyway (an open item from Mission 3). So a step
naming *any* side-effecting, non-idempotent tool among its allowed_tools
is never retried once its first attempt fails, full stop — treated as
"may have acted," matching the spec's own reasoning ("re-running such a
step is how one alert becomes two dispatches").
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from agents.errors import AgentInvocationError
from protocols.model import Step
from tools.tracing import get_trace_id

if TYPE_CHECKING:
    # agents.base is not an entry point (docs/allowed_calls.md) — Agent is
    # only ever used here as a type hint, so this import is never taken at
    # runtime and never crosses the boundary for real.
    from agents.base import Agent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepOutcome:
    step: Step
    result_text: str | None
    attempt_count: int
    succeeded: bool
    failure_reason: str | None = None


def _can_retry(step: Step, agent: Agent) -> bool:
    exposed = {t.name: t for t in agent.exposed_tools()}

    for tool_name in step.allowed_tools:
        info = exposed.get(tool_name)
        if info is not None and info.side_effecting and not info.idempotent:
            return False

    return True


def execute_step_with_retry(
    agent: Agent,
    step: Step,
    settings_store,
    *,
    task_rewriter: Callable[[Step, str], str] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    backoff_seconds: float = 1.0,
) -> StepOutcome:
    current_task_text = step.task_text
    attempts = 0
    last_failure_reason = "attempt limit exhausted"

    while True:
        attempt_limit = settings_store.get_retry_count()  # read live, every attempt
        attempts += 1

        try:
            result = agent.process(current_task_text, list(step.allowed_tools))
        except AgentInvocationError as exc:
            last_failure_reason = str(exc)
            logger.info(
                "step execution failed",
                extra={"event": "step_failed", "agent": step.agent_name, "attempt": attempts, "cause": last_failure_reason, "trace_id": get_trace_id()},
            )

            if attempts >= attempt_limit or not _can_retry(step, agent):
                return StepOutcome(step=step, result_text=None, attempt_count=attempts, succeeded=False, failure_reason=last_failure_reason)

            logger.info("retrying step", extra={"event": "step_retry", "agent": step.agent_name, "attempt": attempts + 1, "cause": last_failure_reason, "trace_id": get_trace_id()})
            sleep_fn(backoff_seconds)
            continue

        if result.status == "unclear_task":
            last_failure_reason = f"task unclear: {result.text}"
            logger.info(
                "step reported task unclear",
                extra={"event": "step_unclear", "agent": step.agent_name, "attempt": attempts, "missing": result.text, "trace_id": get_trace_id()},
            )

            if task_rewriter is None:
                return StepOutcome(step=step, result_text=None, attempt_count=attempts, succeeded=False, failure_reason=f"{last_failure_reason} (no task rewriter available)")

            if attempts >= attempt_limit or not _can_retry(step, agent):
                return StepOutcome(step=step, result_text=None, attempt_count=attempts, succeeded=False, failure_reason=last_failure_reason)

            current_task_text = task_rewriter(step, result.text)
            logger.info("retrying step with rewritten task", extra={"event": "step_retry", "agent": step.agent_name, "attempt": attempts + 1, "cause": last_failure_reason, "trace_id": get_trace_id()})
            sleep_fn(backoff_seconds)
            continue

        return StepOutcome(step=step, result_text=result.text, attempt_count=attempts, succeeded=True)
