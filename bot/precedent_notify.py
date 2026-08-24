"""Precedent-closure notifications (work_plan.md §8.6).

Pushed to every commander immediately and individually — never batched
into a digest — whenever an event closes without running. Purely
informational: it carries the event, the precedent it matched, and how
that precedent ended, so the closure can be judged, but it is never
phrased as a question and needs no reply.
"""

from typing import TYPE_CHECKING

from bot.formatting import format_header

if TYPE_CHECKING:
    from bot.api_client import PrecedentClosureNotice
    from bot.deps import BotDeps


def format_precedent_closure_notice(notice: "PrecedentClosureNotice") -> str:
    return (
        f"{format_header('precedent_closure')}\n\n"
        f"Event: {notice.raw_text}\n\n"
        f"Closed against precedent {notice.matched_precedent_event_id}, "
        f"which ended: {notice.precedent_ending}"
    )


async def notify_precedent_closure(deps: "BotDeps", notice: "PrecedentClosureNotice") -> None:
    text = format_precedent_closure_notice(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)
