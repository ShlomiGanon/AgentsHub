"""Approval prompts (work_plan.md §8.5).

The two hold reasons ask different questions, so they get different
presentations: a flagged protocol asks a yes/no "should this run at
all", and an ambiguous selection asks "which of these" and needs to show
candidates. A no-match selection (orchestrator.main_agent's NO_MATCH
outcome — no protocol genuinely fits) is *not* one of these anymore: it
used to be a third, report-only hold reason here, but that left the
underlying event with no terminal outcome ever recorded (nothing could
resolve it — no candidate, no yes/no). It's now a real terminal outcome
(`orchestrator.flows.FlowOutcome`'s `"no_match_protocol"`) plus the
one-way `notify_no_match` notice below, the same "nothing is waiting on
an answer" treatment `notify_uncertain_verdict` already gets — never
offering "run the closest one anyway," same as before.

Callback data carries the event ID, not the orchestrator's internal hold
ID — `api/operations.py`'s `POST /Approve/<event_id>` (§7.11) is keyed by event
ID, the one stable external identifier the whole API is already built
around. Found and fixed in the Mission 8 deep audit: this module used to
encode `notice.hold_id` here instead, which no real `HttpApiClient` could
have forwarded to a working endpoint.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_header
from bot.users import check_permission, resolve_caller

if TYPE_CHECKING:
    from bot.api_client import HeldApprovalNotice, NoMatchNotice, UncertainVerdictNotice
    from bot.deps import BotDeps

CALLBACK_PREFIX = "approve"


def build_callback_data(event_id: str, choice: str) -> str:
    return f"{CALLBACK_PREFIX}:{event_id}:{choice}"


def parse_callback_data(data: str) -> tuple[str, str]:
    _, event_id, choice = data.split(":", 2)
    return event_id, choice


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
        buttons = [("Approve", build_callback_data(notice.event_id, "approved")), ("Reject", build_callback_data(notice.event_id, "rejected"))]
        return text, buttons

    if notice.reason == "ambiguous_selection":
        candidates = ", ".join(notice.candidate_protocol_names) or "(none)"
        text = f"{header}\n\nMultiple protocols fit equally well:\n{candidates}\n{common}\n\nWhich should run?"
        buttons = [(name, build_callback_data(notice.event_id, name)) for name in notice.candidate_protocol_names]
        return text, buttons

    # Every current reason is handled above (HeldApprovalNotice.reason is
    # typed as exactly these two — see bot/api_client.py). Found live: an
    # earlier version of this function fell through to the
    # ambiguous_selection rendering for *any* reason it didn't recognize,
    # which silently mis-rendered stale "no_match"-reason data (a relic of
    # NO_MATCH's old hold-based design, before it became a real terminal
    # outcome — see orchestrator.holds.determine_approval_hold's own
    # docstring) as "Multiple protocols fit equally well: (none)". Raising
    # here instead means a future third reason value — or any other stale
    # data — fails loudly at render time instead of silently lying about
    # what kind of hold this is.
    raise ValueError(f"unrecognized approval hold reason: {notice.reason!r}")


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


def format_no_match_notice(notice: "NoMatchNotice") -> str:
    # Deliberately not phrased as a question, same as an uncertain-verdict
    # notice above — nothing is waiting on an answer to it, and never
    # offers "run the closest one anyway" (that would silently reintroduce
    # the forced, low-confidence match this outcome exists to avoid).
    why = notice.reason or "(no reason given)"
    return (
        f"{format_header('no_match')}\n\n"
        f"No existing protocol can fulfill this request.\n"
        f"Raw text: {notice.raw_text}\n"
        f"{why}\n"
        f"Risk: {notice.risk_level} ({notice.risk_reason})"
    )


async def notify_no_match(deps: "BotDeps", notice: "NoMatchNotice") -> None:
    text = format_no_match_notice(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


def _describe_outcome(outcome) -> str:
    if outcome.status == "approved":
        return "Recorded — the protocol has been resumed."

    if outcome.status == "rejected":
        return "Recorded — declined; the event will not run."

    if outcome.status in ("unauthorized", "invalid_classification", "invalid_candidate"):
        return outcome.message

    who = f" by {outcome.resolved_by}" if outcome.resolved_by else ""
    return f"This approval was already answered{who}. {outcome.message}".strip()


async def handle_approval_answer(deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, choice: str) -> None:
    """`choice` is already "approved"/"rejected" for a flagged-protocol
    hold (the button's callback data), or the chosen candidate's protocol
    name for an ambiguous-selection hold — see `BotApiClient
    .answer_approval_hold`'s docstring for how the orchestrator side
    handles all three. This function needs no separate `reason` parameter:
    it forwards whichever `choice` the button carried and describes
    whatever status comes back.
    """

    resolution = await resolve_caller(deps.api_client, answering_identity)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, "approve_run")
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_approval_hold(event_id, choice, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_outcome(outcome))

"""Clarification prompts (work_plan.md §8.4).

Pushes a held event to every commander the moment it holds, offering the
loaded event types as buttons (never free text — the registry is fixed
for the run, and only a type drawn from it can ever be accepted). Answers
route back through the same permission check every other commander
action uses, and a second commander answering an already-resolved hold
is told so, and told who resolved it first, rather than silently
accepted a second time.

Callback data carries the event ID, not the orchestrator's internal hold
ID — `api/operations.py`'s `POST /Clarify/<event_id>` (§7.11) is keyed by event
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
CLARIFICATION_CALLBACK_PREFIX = "clarify"


def build_clarification_callback_data(event_id: str, classification: str) -> str:
    return f"{CLARIFICATION_CALLBACK_PREFIX}:{event_id}:{classification}"


def parse_clarification_callback_data(data: str) -> tuple[str, str]:
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
    buttons = [(choice, build_clarification_callback_data(notice.event_id, choice)) for choice in notice.available_classifications]

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def _describe_clarification_outcome(outcome) -> str:
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
    await deps.telegram_client.send_text(chat_id, _describe_clarification_outcome(outcome))



