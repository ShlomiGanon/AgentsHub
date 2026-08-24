"""The single message entry point (work_plan.md §8.3).

Everything a person types as free text (never a slash-command — `bot.app`
routes those separately, per this subtask's own "reserve slash-commands
... so they never collide with free text" requirement) goes to exactly
one place: the message endpoint, via `BotApiClient.submit_message`.
There is no separate "report" or "ask" command; intent classification
(work_plan.md §6.13, behind the API) decides, and giving the user a
second entry point would let their own framing override that judgment.
"""

from typing import TYPE_CHECKING

from bot.users import check_permission, resolve_caller

if TYPE_CHECKING:
    from bot.deps import BotDeps


async def handle_incoming_message(deps: "BotDeps", telegram_identity: str, text: str, message_id: str) -> str:
    """Return the exact text to reply with in the chat that sent `text`.

    `message_id` — the incoming Telegram message's own ID — is threaded
    straight through to `submit_message` (see that method's own
    docstring): this is the one place it's available, and it must reach
    persistence now or it's lost for good before any later asynchronous
    reply could reference it.
    """

    resolution = await resolve_caller(deps.api_client, telegram_identity)
    if resolution.status == "unregistered":
        return resolution.refusal_message

    caller = resolution.caller
    refusal = check_permission(caller, "send_message")
    if refusal is not None:
        return refusal

    result = await deps.api_client.submit_message(text, telegram_identity, message_id)

    if result.kind == "question":
        # A question has no job to track — the answer is the whole reply (§8.3, §7.4).
        return result.answer_text or "(no answer was returned)"

    lines = [f"Got it — taken as a {result.kind}."]

    if result.awaiting_approval:
        lines.append("It is now waiting for a commander's approval.")
    elif result.job_id:
        lines.append(f"Job ID: {result.job_id}. You'll hear back here once it's done.")

    # Silence after a request is indistinguishable from the system having
    # ignored it (§8.3) — this reply is what rules that out.
    return "\n".join(lines)
