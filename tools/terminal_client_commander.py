"""Terminal stand-in for a commander's Telegram session (manual testing tool)."""

import argparse
import asyncio
import importlib
import sys
import uuid
from pathlib import Path

from bot import (
    ApiRequestError,
    BotDeps,
    BotError,
    HttpApiClient,
    NotificationCursorStore,
    dispatch_notification,
    format_approval_prompt,
    handle_approval_answer,
    handle_clarification_answer,
    handle_incoming_message,
    parse_approval_callback_data,
)
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
    submit_event,
)

_HOLD_KINDS = ("clarification_hold", "approval_hold")


class _BackgroundState:
    """Shared, single-writer-single-reader state between the background poll task and the foreground REPL loop — safe with no locking since both run as coroutines on the one asyncio ev..."""

    def __init__(self):
        self.arrived: list = []
        self.poll_error: str | None = None


async def _initial_cursor(deps: BotDeps, cursor_store: NotificationCursorStore, cursor_path: Path, identity: str) -> int:
    """First-ever run for this identity+deployment: fast-forward past whatever is already in `notification_log`, without displaying or acting on any of it, then persist that starting p..."""

    if cursor_path.exists():
        return cursor_store.read()

    notifications, cursor = await deps.api_client.poll_pending_notifications(0)
    cursor_store.write(cursor)
    if notifications:
        print(
            f"(first run for identity {identity!r} — skipping {len(notifications)} pre-existing notification(s) "
            "already in this deployment's history; every run after this one resumes from here, the same way "
            "the real bot does)"
        )
    return cursor


async def _background_poll_loop(
    deps: BotDeps,
    cursor_store: NotificationCursorStore,
    cursor: int,
    poll_interval: float,
    state: _BackgroundState,
    stop_event: asyncio.Event,
) -> None:
    """Mirrors `bot.notifications.run_notification_poll_loop`'s own shape — a standing loop, started once, running until told to stop — but never calls `dispatch_notification` itself:..."""

    transport_backoff = 0.5
    while not stop_event.is_set():
        try:
            notifications, cursor = await deps.api_client.poll_pending_notifications(cursor, 20)
        except ApiRequestError as exc:
            state.poll_error = str(exc)
            reconnect_delay = transport_backoff
            transport_backoff = min(30.0, transport_backoff * 2)
        else:
            state.poll_error = None
            cursor_store.write(cursor)
            state.arrived.extend(notifications)
            reconnect_delay = 0
            transport_backoff = 0.5

        if reconnect_delay:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=reconnect_delay)
            except asyncio.TimeoutError:
                pass


async def _drain_arrived(deps: BotDeps, state: _BackgroundState, pending_holds: list) -> None:
    """The one place anything the background task found gets shown — called at the top of every REPL iteration, i.e."""

    if state.poll_error:
        print(f"\n(background polling hit an error and is retrying: {state.poll_error})")
        state.poll_error = None

    if not state.arrived:
        return

    to_process = state.arrived[:]
    state.arrived.clear()

    print(f"\n--- {len(to_process)} new notification(s) since your last turn ---")
    hold_count = 0
    for note in to_process:
        await dispatch_notification(deps, note)
        if note.kind in _HOLD_KINDS:
            pending_holds.append(note)
            hold_count += 1

    if hold_count:
        print(f"\n({hold_count} of those need an answer — type /holds to review)")


async def _prompt_clarification(deps: BotDeps, answering_identity: str, notice) -> bool:
    """Returns True once resolved, False if skipped."""

    print(f"\nClarification hold — event {notice.event_id}")
    print("Choose the correct classification:")
    for choice_number, choice in enumerate(notice.available_classifications, start=1):
        print(f"  [{choice_number}] {choice}")
    print("  [s] Skip for now (leave this hold open)")

    while True:
        selected_option = (await ainput("your choice> ")).strip()
        if selected_option.lower() == "s":
            print("(skipped — this hold is still open; use /holds to come back to it)")
            return False
        if selected_option.isdigit() and 1 <= int(selected_option) <= len(notice.available_classifications):
            chosen = notice.available_classifications[int(selected_option) - 1]
            break
        if selected_option in notice.available_classifications:
            chosen = selected_option
            break
        print("Invalid choice — pick one of the numbers above, or 's' to skip.")

    await handle_clarification_answer(deps, CONSOLE_CHAT_ID, answering_identity, notice.event_id, chosen)
    return True


async def _prompt_approval(deps: BotDeps, answering_identity: str, notice) -> bool:
    """Same return contract as `_prompt_clarification`."""

    text, buttons = format_approval_prompt(notice)
    print(f"\n{text}")
    print("\nChoose:")
    for choice_number, (label, _data) in enumerate(buttons, start=1):
        print(f"  [{choice_number}] {label}")
    print("  [s] Skip for now (leave this hold open)")

    while True:
        selected_option = (await ainput("your choice> ")).strip()
        if selected_option.lower() == "s":
            print("(skipped — this hold is still open; use /holds to come back to it)")
            return False
        if selected_option.isdigit() and 1 <= int(selected_option) <= len(buttons):
            _, callback_data = buttons[int(selected_option) - 1]
            break
        print("Invalid choice — pick one of the numbers above, or 's' to skip.")

    event_id, choice = parse_approval_callback_data(callback_data)
    await handle_approval_answer(deps, CONSOLE_CHAT_ID, answering_identity, event_id, choice)
    return True


async def _handle_holds_command(deps: BotDeps, pending_holds: list, answering_identity: str) -> None:
    if not pending_holds:
        print("No pending holds right now.")
        return

    to_review = pending_holds[:]
    pending_holds.clear()

    for note in to_review:
        if note.kind == "clarification_hold":
            resolved = await _prompt_clarification(deps, answering_identity, note.payload)
        else:
            resolved = await _prompt_approval(deps, answering_identity, note.payload)
        if not resolved:
            pending_holds.append(note)


async def _run_repl(
    deps: BotDeps,
    observing_client: ObservingApiClient,
    base_url: str,
    test_identity: str,
    poll_interval: float,
    cursor_store: NotificationCursorStore,
    cursor_path: Path,
) -> None:
    await deps.api_client.start()
    conversation_id = f"terminal-commander:{test_identity}:{uuid.uuid4().hex}"
    cursor = await _initial_cursor(deps, cursor_store, cursor_path, test_identity)

    state = _BackgroundState()
    pending_holds: list = []
    stop_event = asyncio.Event()
    background_task = asyncio.create_task(
        _background_poll_loop(deps, cursor_store, cursor, poll_interval, state, stop_event)
    )

    try:
        mode = await choose_mode()

        while mode is not None:
            await _drain_arrived(deps, state, pending_holds)

            if mode == "message":
                text = (await ainput("\nmessage> ")).strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    break
                if text == "/mode":
                    mode = await choose_mode()
                    continue
                if text == "/holds":
                    await _drain_arrived(deps, state, pending_holds)
                    await _handle_holds_command(deps, pending_holds, test_identity)
                    continue

                try:
                    reply = await handle_incoming_message(
                        deps, test_identity, text, new_message_id(), conversation_id=conversation_id
                    )
                except (ApiRequestError, BotError) as exc:
                    print(f"(request failed: {exc})")
                    continue

                print(reply)

            else:  # event mode
                payload = await choose_event_payload(test_identity)
                if payload is None:
                    mode = await choose_mode()
                    continue

                text, sender = payload
                try:
                    status, response_payload = submit_event(base_url, text, sender)
                except ApiRequestError as exc:
                    print(f"(request failed: {exc})")
                    continue

                if status >= 400:
                    print(f"submission refused ({status}): {response_payload.get('message', response_payload)}")
                    continue

                print(f"submitted: event_id={response_payload['event_id']} status={response_payload['status']}")
    finally:
        stop_event.set()
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
        await deps.api_client.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Terminal stand-in for a commander's Telegram session — talks to a running `api.app` "
        "server through the same code the real bot uses (work_plan.md §8), for manual end-to-end testing."
    )
    parser.add_argument("--profile", default="profiles.demo", help="dotted profile module path (default: profiles.demo)")
    parser.add_argument("--identity", default="cli_tester", help="commander-level test identity to act as (default: cli_tester)")
    parser.add_argument("--host", default="127.0.0.1", help="host the API server is bound to (default: 127.0.0.1)")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="seconds between notification polls (default: 2.0)")
    args = parser.parse_args(argv)

    try:
        profile_module = importlib.import_module(args.profile)
    except ImportError as exc:
        raise SystemExit(f"could not import profile module '{args.profile}': {exc}") from exc

    base_url = f"http://{args.host}:{profile_module.API_PORT}"

    print(f"Profile:  {args.profile}")
    print(f"Database: {profile_module.DB_PATH}")
    print(f"API:      {base_url}  (make sure `python -m api.app {args.profile}` is already running)")
    print("(background polling starts immediately; type /holds at the message prompt any time to review open holds)")

    created_this_session = ensure_test_identity(args.profile, args.identity, "commander", profile_module)

    http_client = HttpApiClient(base_url)
    observing_client = ObservingApiClient(http_client)
    bot_dependencies = BotDeps(loaded_profile=None, telegram_client=ConsoleTelegramClient(args.identity), api_client=observing_client)

    cursor_path = Path(f"{profile_module.DB_PATH}.notification_cursor.terminal_commander.{args.identity}")
    cursor_store = NotificationCursorStore(cursor_path)

    try:
        try:
            asyncio.run(
                _run_repl(bot_dependencies, observing_client, base_url, args.identity, args.poll_interval, cursor_store, cursor_path)
            )
        except (KeyboardInterrupt, EOFError):
            pass
    finally:
        cleanup_test_identity(args.profile, args.identity, created_this_session, cursor_path=cursor_path)

    print("\nGoodbye.")


if __name__ == "__main__":
    sys.exit(main())
