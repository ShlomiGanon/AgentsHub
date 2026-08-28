"""The protocol executor (work_plan.md §4.4, §4.6, §4.8)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import TYPE_CHECKING, Callable

from agents import AgentInvocationError
from protocols.contracts import ProtocolRunResult, Step, StepOutcome
from tools import get_trace_id, stage_context

if TYPE_CHECKING:
    from agents import Agent

logger = logging.getLogger(__name__)
_side_effect_locks: dict[str, threading.Lock] = {}
_side_effect_locks_guard = threading.Lock()


def _locks_for_step(agent: Agent, step: Step) -> list[threading.Lock]:
    exposed = {tool.name: tool for tool in agent.exposed_tools()}
    keys = sorted(
        f"{agent.name}:{tool_name}"
        for tool_name in step.allowed_tools
        if tool_name in exposed and exposed[tool_name].side_effecting
    )
    with _side_effect_locks_guard:
        return [_side_effect_locks.setdefault(key, threading.Lock()) for key in keys]


def _can_retry(step: Step, agent: Agent) -> bool:
    exposed = {tool.name: tool for tool in agent.exposed_tools()}
    for tool_name in step.allowed_tools:
        tool_info = exposed.get(tool_name)
        if tool_info is not None and tool_info.side_effecting and not tool_info.idempotent:
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
            side_effect_locks = _locks_for_step(agent, step)
            for side_effect_lock in side_effect_locks:
                side_effect_lock.acquire()
            try:
                with stage_context("step_execution"):
                    agent_result = agent.process(current_task_text, list(step.allowed_tools))
            finally:
                for side_effect_lock in reversed(side_effect_locks):
                    side_effect_lock.release()
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

        if agent_result.status == "unclear_task":
            last_failure_reason = f"task unclear: {agent_result.text}"
            logger.info(
                "step reported task unclear",
                extra={"event": "step_unclear", "agent": step.agent_name, "attempt": attempts, "missing": agent_result.text, "trace_id": get_trace_id()},
            )

            if task_rewriter is None:
                return StepOutcome(step=step, result_text=None, attempt_count=attempts, succeeded=False, failure_reason=f"{last_failure_reason} (no task rewriter available)")

            if attempts >= attempt_limit or not _can_retry(step, agent):
                return StepOutcome(step=step, result_text=None, attempt_count=attempts, succeeded=False, failure_reason=last_failure_reason)

            current_task_text = task_rewriter(step, agent_result.text)
            logger.info("retrying step with rewritten task", extra={"event": "step_retry", "agent": step.agent_name, "attempt": attempts + 1, "cause": last_failure_reason, "trace_id": get_trace_id()})
            sleep_fn(backoff_seconds)
            continue

        return StepOutcome(step=step, result_text=agent_result.text, attempt_count=attempts, succeeded=True)


def execute_steps(
    steps: list[Step],
    agents_by_name: dict[str, Agent],
    settings_store,
    task_rewriter: Callable[[Step, str], str] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ProtocolRunResult:
    if any(step.step_id or step.depends_on for step in steps):
        return _execute_dependency_steps(
            steps, agents_by_name, settings_store, task_rewriter=task_rewriter, sleep_fn=sleep_fn
        )

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


def _execute_dependency_steps(
    steps: list[Step],
    agents_by_name: dict[str, Agent],
    settings_store,
    *,
    task_rewriter: Callable[[Step, str], str] | None,
    sleep_fn: Callable[[float], None],
) -> ProtocolRunResult:
    step_ids = [step.step_id or str(index) for index, step in enumerate(steps)]
    if len(set(step_ids)) != len(step_ids):
        return ProtocolRunResult(step_outcomes=(), completed=False, failure_cause="duplicate protocol step_id")
    known = set(step_ids)
    if any(dependency not in known for step in steps for dependency in step.depends_on):
        return ProtocolRunResult(step_outcomes=(), completed=False, failure_cause="protocol step names an unknown dependency")

    completed: dict[str, StepOutcome] = {}
    pending = set(step_ids)
    index_by_id = {step_id: index for index, step_id in enumerate(step_ids)}

    def _read_only(step: Step) -> bool:
        agent = agents_by_name[step.agent_name]
        tool_by_name = {tool.name: tool for tool in agent.exposed_tools()}
        return all(name in tool_by_name and not tool_by_name[name].side_effecting for name in step.allowed_tools)

    while pending:
        ready = [
            step_id for step_id in step_ids
            if step_id in pending and all(dependency in completed and completed[dependency].succeeded for dependency in steps[index_by_id[step_id]].depends_on)
        ]
        if not ready:
            return ProtocolRunResult(step_outcomes=tuple(completed[step_id] for step_id in step_ids if step_id in completed), completed=False, failure_cause="protocol dependency cycle or failed dependency")

        parallel_ready = [step_id for step_id in ready if _read_only(steps[index_by_id[step_id]])]
        selected = parallel_ready[:4] if parallel_ready else [ready[0]]

        def _run(step_id: str) -> tuple[str, StepOutcome]:
            step = steps[index_by_id[step_id]]
            outcome = execute_step_with_retry(
                agents_by_name[step.agent_name], step, settings_store,
                task_rewriter=task_rewriter, sleep_fn=sleep_fn,
            )
            return step_id, outcome

        if len(selected) > 1:
            with ThreadPoolExecutor(max_workers=len(selected)) as executor:
                futures = [executor.submit(copy_context().run, _run, step_id) for step_id in selected]
                resolved = [future.result() for future in futures]
        else:
            resolved = [_run(selected[0])]

        for step_id, outcome in resolved:
            completed[step_id] = outcome
            pending.remove(step_id)
        failed = [(step_id, outcome) for step_id, outcome in resolved if not outcome.succeeded]
        if failed:
            failed_id, failed_outcome = failed[0]
            failed_index = index_by_id[failed_id]
            ordered = tuple(completed[step_id] for step_id in step_ids if step_id in completed)
            return ProtocolRunResult(
                step_outcomes=ordered,
                completed=False,
                failed_step_index=failed_index,
                failed_step_agent=steps[failed_index].agent_name,
                failure_cause=failed_outcome.failure_reason,
            )

    return ProtocolRunResult(step_outcomes=tuple(completed[step_id] for step_id in step_ids), completed=True)
