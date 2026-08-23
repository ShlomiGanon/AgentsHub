"""Approval prompts (work_plan.md §8.5).

The two hold reasons ask different questions, so they get different
presentations: a flagged protocol asks a yes/no "should this run at
all", an ambiguous selection asks "which of these", and only the second
needs to show candidates. An uncertain-verdict notice is pushed the same
way but is deliberately never phrased as a question — nothing is
waiting on an answer to it, and presenting it as an approval invites one
that has nowhere to go.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_header
from bot.users import check_permission, resolve_caller

if TYPE_CHECKING:
    from bot.api_client import HeldApprovalNotice, UncertainVerdictNotice
    from bot.deps import BotDeps

CALLBACK_PREFIX = "approve"


def build_callback_data(hold_id: str, choice: str) -> str:
    return f"{CALLBACK_PREFIX}:{hold_id}:{choice}"


def parse_callback_data(data: str) -> tuple[str, str]:
    _, hold_id, choice = data.split(":", 2)
    return hold_id, choice


def format_approval_prompt(notice: "HeldApprovalNotice") -> tuple[str, list[tuple[str, str]]]:
    """Return (message text, buttons) — buttons differ by hold reason."""

    header = format_header("approval_needed")
    common = f"Risk: {notice.risk_level} ({notice.risk_reason})"

    if notice.reason == "flagged_protocol":
        text = (
            f"{header}\n\n"
            f"Protocol flagged for approval: {notice.selected_protocol_name}\n"
            f"{common}\n\n"
            f"Should this run?"
        )
        buttons = [("Approve", build_callback_data(notice.hold_id, "approved")), ("Reject", build_callback_data(notice.hold_id, "rejected"))]
        return text, buttons

    # ambiguous_selection
    candidates = ", ".join(notice.candidate_protocol_names) or "(none)"
    text = f"{header}\n\nMultiple protocols fit equally well:\n{candidates}\n{common}\n\nWhich should run?"
    buttons = [(name, build_callback_data(notice.hold_id, name)) for name in notice.candidate_protocol_names]
    return text, buttons


async def push_approval_prompt(deps: "BotDeps", notice: "HeldApprovalNotice") -> None:
    text, buttons = format_approval_prompt(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def format_uncertain_verdict_notice(notice: "UncertainVerdictNotice") -> str:
    return f"{format_header('uncertain_verdict')}\n\nEvent {notice.event_id} finished with an uncertain verdict.\n\nInsight:\n{notice.insight_text}"


async def notify_uncertain_verdict(deps: "BotDeps", notice: "UncertainVerdictNotice") -> None:
    text = format_uncertain_verdict_notice(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


def _describe_outcome(outcome) -> str:
    if outcome.status == "approved":
        return "Recorded — the protocol has been resumed."

    if outcome.status == "rejected":
        return "Recorded — declined; the event will not run."

    if outcome.status in ("unauthorized", "invalid_classification"):
        return outcome.message

    who = f" by {outcome.resolved_by}" if outcome.resolved_by else ""
    return f"This approval was already answered{who}. {outcome.message}".strip()


async def handle_approval_answer(deps: "BotDeps", chat_id: str, answering_identity: str, hold_id: str, choice: str) -> None:
    """`choice` is already "approved"/"rejected" for a flagged-protocol
    hold (the button's callback data), or the chosen candidate's protocol
    name for an ambiguous-selection hold — see `BotApiClient
    .answer_approval_hold`'s docstring for the orchestrator-side gap this
    currently has nowhere to resolve to. This function needs no separate
    `reason` parameter: it forwards whichever `choice` the button carried
    and describes whatever status comes back.
    """

    resolution = await resolve_caller(deps.api_client, answering_identity)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, "approve_run")
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_approval_hold(hold_id, choice, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_outcome(outcome))
