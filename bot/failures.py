"""Deliver failure notifications (work_plan.md §8.11).

Sent to whoever originated the event, naming the step that failed and
why it exhausted retries, and including whatever succeeded before that
step — a run that fails at its last step still produced findings, and
losing them from the notification would understate what happened. Kept
visually and textually distinct from a declined run and from an
uncertain verdict (`bot.formatting`'s headers): all three end without a
clean success, and each calls for a different response from whoever
reads it.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_failure_notice

if TYPE_CHECKING:
    from bot.api_client import BotNotification
    from bot.deps import BotDeps


async def deliver_failure_notification(deps: "BotDeps", notification: "BotNotification") -> None:
    notice = notification.payload
    text = format_failure_notice(notice)

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)
