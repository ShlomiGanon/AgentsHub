"""Deliver asynchronous results (work_plan.md §8.9).

The acknowledgment half of §8.9 ("acknowledge every submission
immediately") is `bot.entrypoint.handle_incoming_message`'s job — it
replies in the same turn `submit_message` returns a job ID, before any
model call runs. This module is the other half: once a job finishes, its
result is pushed back to whoever submitted it, in the chat they submitted
it from, as a reply referencing their original message (minutes may have
passed; they may have sent others meanwhile).
"""

from typing import TYPE_CHECKING

from bot.formatting import format_job_result

if TYPE_CHECKING:
    from bot.api_client import BotNotification
    from bot.deps import BotDeps


async def deliver_job_result(deps: "BotDeps", notification: "BotNotification") -> None:
    result = notification.payload
    text = format_job_result(result)

    for chat_id in notification.target_chat_ids:
        await deps.telegram_client.send_reply(chat_id, text, notification.reply_to_message_id)
