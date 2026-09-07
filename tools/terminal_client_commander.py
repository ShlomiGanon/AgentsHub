"""Terminal stand-in for a commander's Telegram session (manual testing tool)."""

import argparse
import asyncio
import importlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

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
    present_incoming_message,
    parse_approval_callback_data,
)
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
            message_catalog_for(deps).text(
                "terminal.first_run_skip", identity=repr(identity), count=len(notifications)
            )
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
        print(
            "\n" + message_catalog_for(deps).text(
                "terminal.poll_background_error", reason=state.poll_error
            )
        )
        state.poll_error = None

    if not state.arrived:
        return

    to_process = state.arrived[:]
    state.arrived.clear()

    print(
        "\n" + message_catalog_for(deps).text(
            "terminal.new_notifications", count=len(to_process)
        )
    )
    hold_count = 0
    for note in to_process:
        await dispatch_notification(deps, note)
        if note.kind in _HOLD_KINDS:
            pending_holds.append(note)
            hold_count += 1

    if hold_count:
        print(
            "\n" + message_catalog_for(deps).text(
                "terminal.holds_need_answer", count=hold_count
            )
        )


async def _prompt_clarification(deps: BotDeps, answering_identity: str, notice) -> bool:
    """Returns True once resolved, False if skipped."""

    messages = message_catalog_for(deps)
    print("\n" + messages.text("terminal.clarification_hold", event_id=notice.event_id))
    print(messages.text("terminal.choose_classification"))
    for choice_number, choice in enumerate(notice.available_classifications, start=1):
        print(f"  [{choice_number}] {choice}")
    print(messages.text("terminal.skip_hold"))

    while True:
        selected_option = (await ainput(messages.text("terminal.your_choice"))).strip()
        if selected_option.lower() == "s":
            print(messages.text("terminal.skipped_hold"))
            return False
        if selected_option.isdigit() and 1 <= int(selected_option) <= len(notice.available_classifications):
            chosen = notice.available_classifications[int(selected_option) - 1]
            break
        if selected_option in notice.available_classifications:
            chosen = selected_option
            break
        print(messages.text("terminal.invalid_hold_choice"))

    await handle_clarification_answer(deps, CONSOLE_CHAT_ID, answering_identity, notice.event_id, chosen)
    return True


async def _prompt_approval(deps: BotDeps, answering_identity: str, notice) -> bool:
    """Same return contract as `_prompt_clarification`."""

    messages = message_catalog_for(deps)
    text, buttons = format_approval_prompt(notice, messages)
    print(f"\n{text}")
    print("\n" + messages.text("terminal.choose"))
    for choice_number, (label, _data) in enumerate(buttons, start=1):
        print(f"  [{choice_number}] {label}")
    print(messages.text("terminal.skip_hold"))

    while True:
        selected_option = (await ainput(messages.text("terminal.your_choice"))).strip()
        if selected_option.lower() == "s":
            print(messages.text("terminal.skipped_hold"))
            return False
        if selected_option.isdigit() and 1 <= int(selected_option) <= len(buttons):
            _, callback_data = buttons[int(selected_option) - 1]
            break
        print(messages.text("terminal.invalid_hold_choice"))

    event_id, choice = parse_approval_callback_data(callback_data)
    await handle_approval_answer(deps, CONSOLE_CHAT_ID, answering_identity, event_id, choice)
    return True


async def _handle_holds_command(deps: BotDeps, pending_holds: list, answering_identity: str) -> None:
    if not pending_holds:
        print(message_catalog_for(deps).text("terminal.no_holds"))
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
        messages = message_catalog_for(deps)
        mode = await choose_mode(messages)

        while mode is not None:
            await _drain_arrived(deps, state, pending_holds)

            if mode == "message":
                text = (await ainput(messages.text("terminal.message_prompt"))).strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    break
                if text == "/mode":
                    mode = await choose_mode(messages)
                    continue
                if text == "/holds":
                    await _drain_arrived(deps, state, pending_holds)
                    await _handle_holds_command(deps, pending_holds, test_identity)
                    continue

                await present_incoming_message(
                    deps,
                    CONSOLE_CHAT_ID,
                    test_identity,
                    text,
                    new_message_id(),
                    conversation_id=conversation_id,
                )

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
    messages = get_catalog(profile_module.DEFAULT_LANGUAGE)

    print(messages.text("terminal.profile", profile=args.profile))
    print(messages.text("terminal.database", database=profile_module.DB_PATH))
    print(
        messages.text(
            "terminal.api", base_url=base_url, command=f"python -m api.app {args.profile}"
        )
    )
    print(messages.text("terminal.background"))

    created_this_session = ensure_test_identity(
        args.profile, args.identity, "commander", profile_module, messages
    )

    http_client = HttpApiClient(base_url)
    observing_client = ObservingApiClient(http_client)
    bot_dependencies = BotDeps(
        loaded_profile=SimpleNamespace(message_catalog=messages),
        telegram_client=ConsoleTelegramClient(args.identity),
        api_client=observing_client,
    )

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

    print(messages.text("terminal.goodbye"))


if __name__ == "__main__":
    sys.exit(main())
