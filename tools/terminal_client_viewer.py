"""Terminal stand-in for a viewer's Telegram session (manual testing tool)."""

import argparse
import asyncio
import importlib
import sys
import uuid
from types import SimpleNamespace

from bot import ApiRequestError, BotDeps, BotError, HttpApiClient, dispatch_notification
from bot.app import present_incoming_message
from bot.interactions import message_catalog_for
from messages import get_catalog
from tools.terminal_support import (
    CONSOLE_CHAT_ID,
    ConsoleTelegramClient,
    ObservingApiClient,
    ainput,
    choose_event_payload,
    choose_mode,
    cleanup_test_identity,
    ensure_test_identity,
    new_message_id,
    notification_subject_id,
    submit_event,
)


async def _bootstrap_notification_cursor(deps: BotDeps) -> int:
    """Fast-forward past every notification that already exists before this session starts — see `tools.terminal_client_commander`'s own identical reasoning (the same shared/reused dat..."""

    notifications, cursor = await deps.api_client.poll_pending_notifications(0)
    if notifications:
        print(
            message_catalog_for(deps).text(
                "terminal.skip_existing", count=len(notifications)
            )
        )
    return cursor


async def _wait_for_completion(deps: BotDeps, cursor: int, job_id: str, poll_interval: float) -> int:
    """Poll `GET /Notifications` exactly as `bot.notifications .run_notification_poll_once` does, until the one that finishes `job_id` arrives."""

    messages = message_catalog_for(deps)
    print(messages.text("terminal.waiting"))

    transport_backoff = 0.5
    while True:
        try:
            notifications, cursor = await deps.api_client.poll_pending_notifications(cursor, 20)
        except ApiRequestError as exc:
            print(messages.text("terminal.poll_failed", reason=exc))
            await asyncio.sleep(transport_backoff)
            transport_backoff = min(30.0, transport_backoff * 2)
            continue
        transport_backoff = 0.5

        for note in notifications:
            await dispatch_notification(deps, note)
            if note.kind in ("job_finished", "job_failed", "event_data_hold") and notification_subject_id(note) == job_id:
                return cursor



async def _run_repl(deps: BotDeps, observing_client: ObservingApiClient, base_url: str, test_identity: str, poll_interval: float) -> None:
    await deps.api_client.start()
    conversation_id = f"terminal-viewer:{test_identity}:{uuid.uuid4().hex}"
    try:
        cursor = await _bootstrap_notification_cursor(deps)
        messages = message_catalog_for(deps)
        mode = await choose_mode(messages)
        while mode is not None:
            if mode == "message":
                text = (await ainput(messages.text("terminal.message_prompt"))).strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    break
                if text == "/mode":
                    mode = await choose_mode(messages)
                    continue

                await present_incoming_message(
                    deps,
                    CONSOLE_CHAT_ID,
                    test_identity,
                    text,
                    new_message_id(),
                    conversation_id=conversation_id,
                )

                submission = observing_client.last_submission
                if submission is not None and submission.kind != "question" and submission.job_id:
                    cursor = await _wait_for_completion(deps, cursor, submission.job_id, poll_interval)

            else:  # event mode
                payload = await choose_event_payload(test_identity, messages)
                if payload is None:
                    mode = await choose_mode(messages)
                    continue

                text, sender = payload
                try:
                    status, response_payload = submit_event(base_url, text, sender)
                except ApiRequestError as exc:
                    print(messages.text("terminal.request_failed", reason=exc))
                    continue

                if status >= 400:
                    print(
                        messages.text(
                            "terminal.submission_refused",
                            status=status,
                            reason=response_payload.get("message", response_payload),
                        )
                    )
                    continue

                print(
                    messages.text(
                        "terminal.submitted",
                        event_id=response_payload["event_id"],
                        status=response_payload["status"],
                    )
                )
                cursor = await _wait_for_completion(deps, cursor, response_payload["event_id"], poll_interval)
    finally:
        await deps.api_client.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Terminal stand-in for a viewer's Telegram session — talks to a running `api.app` "
        "server through the same code the real bot uses (work_plan.md §8), for manual end-to-end testing."
    )
    parser.add_argument("--profile", default="profiles.demo", help="dotted profile module path (default: profiles.demo)")
    parser.add_argument("--identity", default="viewer_tester", help="viewer-level test identity to act as (default: viewer_tester)")
    parser.add_argument("--host", default="127.0.0.1", help="host the API server is bound to (default: 127.0.0.1)")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="seconds between notification polls (default: 2.0)")
    args = parser.parse_args(argv)

    try:
        profile_module = importlib.import_module(args.profile)
    except ImportError as exc:
        raise SystemExit(f"could not import profile module '{args.profile}': {exc}") from exc

    base_url = f"http://{args.host}:{profile_module.API_PORT}"
    messages = get_catalog(profile_module.DEFAULT_LANGUAGE)

    print(messages.text("terminal.profile", profile=args.profile))
    print(messages.text("terminal.database", database=profile_module.DB_PATH))
    print(
        messages.text(
            "terminal.api", base_url=base_url, command=f"python -m api.app {args.profile}"
        )
    )

    created_this_session = ensure_test_identity(
        args.profile, args.identity, "viewer", profile_module, messages
    )

    http_client = HttpApiClient(base_url)
    observing_client = ObservingApiClient(http_client)
    deps = BotDeps(
        loaded_profile=SimpleNamespace(message_catalog=messages),
        telegram_client=ConsoleTelegramClient(args.identity),
        api_client=observing_client,
    )

    try:
        try:
            asyncio.run(_run_repl(deps, observing_client, base_url, args.identity, args.poll_interval))
        except (KeyboardInterrupt, EOFError):
            pass
    finally:
        cleanup_test_identity(args.profile, args.identity, created_this_session)

    print(messages.text("terminal.goodbye"))


if __name__ == "__main__":
    sys.exit(main())
