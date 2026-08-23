"""The unclear-task signal (work_plan.md §3.9).

An agent needs a way to report that its task is unclear or unactionable —
a distinct return, not an exception and not an error string, kept
separate from an execution failure because the two lead to opposite
retries: a failure resends the same text, an unclear task is sent back to
the Main Agent to be rewritten from what the agent said was missing.

Since a bare string return can't be "distinct" from a normal successful
result without becoming exactly the "error string" §3.9 rules out, the
model signals unclear-task through a fixed sentinel line in its own raw
output, and `parse_agent_output` turns that into a tagged `AgentResult` —
the sentinel itself never reaches a caller of `Agent.process`.
"""

from dataclasses import dataclass
from typing import Literal

UNCLEAR_TASK_PREFIX = "UNCLEAR_TASK:"

# Appended to every agent's prompt (agents/adapter.py) so no agent author
# has to know this convention exists — it's framework-level, not
# per-agent.
UNCLEAR_TASK_PROMPT_INSTRUCTION = (
    f'If the task you are given is unclear, ambiguous, or you lack what you need to act on it, '
    f'respond with exactly one line starting with "{UNCLEAR_TASK_PREFIX}" followed by a specific '
    f"statement of what is missing — which parameter, which context, which ambiguity. "
    f"Do not attempt a partial or guessed answer in that case."
)


@dataclass(frozen=True)
class AgentResult:
    status: Literal["success", "unclear_task"]
    text: str


def parse_agent_output(raw_text: str) -> AgentResult:
    stripped = raw_text.strip()

    if stripped.startswith(UNCLEAR_TASK_PREFIX):
        missing = stripped[len(UNCLEAR_TASK_PREFIX):].strip()
        return AgentResult(status="unclear_task", text=missing)

    return AgentResult(status="success", text=raw_text)
