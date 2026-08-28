"""The protocol executor (work_plan.md §4.4, §4.6, §4.8)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from agents import AgentInvocationError
from protocols.contracts import ProtocolRunResult, Step, StepOutcome
from tools import get_trace_id, stage_context

if TYPE_CHECKING:
    from agents import Agent

logger = logging.getLogger(__name__)


def _can_retry(step: Step, agent: Agent) -> bool:
    exposed = {tool.name: tool for tool in agent.exposed_tools()}
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
        attempt_limit = settings_store.get_retry_count()
        attempts += 1

        try:
            with stage_context("step_execution"):
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
