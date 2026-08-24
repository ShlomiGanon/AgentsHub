"""Clarification prompts (work_plan.md §8.4).

Pushes a held event to every commander the moment it holds, offering the
loaded event types as buttons (never free text — the registry is fixed
for the run, and only a type drawn from it can ever be accepted). Answers
route back through the same permission check every other commander
action uses, and a second commander answering an already-resolved hold
is told so, and told who resolved it first, rather than silently
accepted a second time.

Callback data carries the event ID, not the orchestrator's internal hold
ID — `api/holds.py`'s `POST /Clarify/<event_id>` (§7.11) is keyed by event
ID, the one stable external identifier the whole API is already built
around. Found and fixed in the Mission 8 deep audit: this module used to
encode `notice.hold_id` here instead, which no real `HttpApiClient` could
have forwarded to a working endpoint.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_header
from bot.users import check_permission, resolve_caller

if TYPE_CHECKING:
    from bot.api_client import HeldClarificationNotice
    from bot.deps import BotDeps

# Encodes which hold a button answers and what it chose, without needing
# any server-side state beyond the hold itself. Parsed by `bot.app`'s
# callback-query handler.
CALLBACK_PREFIX = "clarify"


def build_callback_data(event_id: str, classification: str) -> str:
    return f"{CALLBACK_PREFIX}:{event_id}:{classification}"


def parse_callback_data(data: str) -> tuple[str, str]:
    _, event_id, classification = data.split(":", 2)
    return event_id, classification


def format_clarification_prompt(notice: "HeldClarificationNotice") -> str:
    return (
        f"{format_header('clarification_needed')}\n\n"
        f"Raw report:\n{notice.raw_text}\n\n"
        f"Could not resolve: {notice.unresolved_field}.\n"
        f"Choose the correct classification below."
    )


async def push_clarification_prompt(deps: "BotDeps", notice: "HeldClarificationNotice") -> None:
    text = format_clarification_prompt(notice)
    buttons = [(choice, build_callback_data(notice.event_id, choice)) for choice in notice.available_classifications]

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def _describe_outcome(outcome) -> str:
    if outcome.status == "resolved":
        return "Recorded — the flow has resumed with your choice."

    if outcome.status == "unauthorized":
        return outcome.message

    if outcome.status == "invalid_classification":
        return outcome.message

    # "not_found": already resolved, by this same race or someone else —
    # never silently re-accepted as if it were the first answer (§8.4).
    who = f" by {outcome.resolved_by}" if outcome.resolved_by else ""
    return f"This clarification was already resolved{who}. {outcome.message}".strip()


async def handle_clarification_answer(
    deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, chosen_classification: str
) -> None:
    resolution = await resolve_caller(deps.api_client, answering_identity)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, "resolve_hold")
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_clarification_hold(event_id, chosen_classification, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_outcome(outcome))
