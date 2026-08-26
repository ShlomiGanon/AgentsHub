"""Terminal stand-in for a commander's Telegram session (manual testing tool).

Lets you exercise a running `api.app` server as a commander-level user by
typing in a terminal instead of using Telegram, while going through exactly
the same code the real bot goes through:

  * `bot.entrypoint.handle_incoming_message` for free-form text (message
    mode) — the same function `bot.app._on_text_message` calls.
  * `bot.http_api_client.HttpApiClient` for every HTTP call to the API —
    the bot's own real client, not a stub.
  * `bot.notifications.dispatch_notification` plus `bot.clarification`/
    `bot.approval`'s own formatting and answer-handling for everything
    that arrives asynchronously (holds, results, failures, precedent
    closures) — the same functions `bot.app`'s notification poll loop
    dispatches to.

Nothing here reimplements message wording, permission checks, or polling
semantics — it only substitutes a `bot.telegram_client.TelegramClient`
that prints to the terminal instead of calling the real Telegram API, and
adds the small amount of glue a terminal needs that Telegram's own UI
normally provides (turning inline buttons into a numbered prompt).

Every capability here — resolving a clarification hold, answering an
approval — is commander-only per `auth/permissions.py`'s own
`ACTION_REQUIREMENTS` (`resolve_hold`, `approve_run`, both COMMANDER); this
file exists because that table says so, not because of anything decided
here. See `tools/terminal_client_viewer.py` for the VIEWER-permitted
counterpart (`send_message`, `view_history`) — message and event mode only,
no hold-answering capability, because the real permission table gives a
viewer none.

This process is a CLIENT. It does not start the server. Run the API
separately first, e.g.:

    python -m api.app profiles.demo

(needs CORE_MODEL_*/SUB_MODEL_* environment variables set — the same ones
`api.app.main` itself requires; see `.env.example`.) Then, in another
terminal, with the same environment variables available (needed only for
the one-time `cli.user_admin` provisioning step below):

    python -m tools.terminal_client_commander --profile profiles.demo

Sensor events (§7.3's `POST /Event`) have no bot-side equivalent to reuse
— the bot never submits one, only sensors do — so event mode sends that
request the same minimal way `bot.http_api_client._do_request` sends
every other request, reusing that exact helper rather than a second
hand-rolled HTTP call.

On clean exit (normal quit, Ctrl+C, EOFError, or any other exception) this
process removes the one test identity *it* provisioned this session — see
`tools._terminal_client_shared.cleanup_test_identity`'s own docstring for
the two scoping rules (never `bot-service`; never an identity that already
existed before this session started).
"""

import argparse
import asyncio
import importlib
import sys

from bot import approval, clarification
from bot.deps import BotDeps
from bot.entrypoint import handle_incoming_message
from bot.errors import ApiRequestError, BotError
from bot.http_api_client import HttpApiClient
from bot.notifications import dispatch_notification
from tools._terminal_client_shared import (
    CONSOLE_CHAT_ID,
    ConsoleTelegramClient,
    ObservingApiClient,
    choose_event_payload,
    choose_mode,
    cleanup_test_identity,
    ensure_test_identity,
    new_message_id,
    notification_subject_id,
    submit_event,
)


async def _bootstrap_notification_cursor(deps: BotDeps) -> int:
    """Fast-forward past every notification that already exists before
    this session starts, without displaying or acting on any of them.

    Found live: the demo (or any shared/reused) database persists
    `notification_log` across every process that has ever run against
    it — sensor-simulator runs, earlier manual testing, another
    developer's session. Starting from cursor 0, as a fresh bot restart
    with no persistent `NotificationCursorStore` would, replays that
    entire backlog on the very first poll: old approval/clarification
    holds get answered-inline prompts opened for events nobody is
    waiting on, old finished/failed jobs print as if they just happened.
    None of it requires input to get *past* (§6.7/§6.2 holds only block
    when their own event_id is the one being awaited — see
    `_wait_for_completion`), so it all scrolls by unattended, looking
    exactly like the tool running away on its own. A real Telegram bot
    never hits this because `bot.app` gives its poll loop a real,
    disk-persisted `NotificationCursorStore`; this tool has no equivalent
    (there is nothing to resume across runs of a manual test client), so
    it must never start earlier than "now" instead.
    """

    notifications, cursor = await deps.api_client.poll_pending_notifications(0)
    if notifications:
        print(
            f"(skipping {len(notifications)} pre-existing notification(s) already in this deployment's "
            "history, from before this session started)"
        )
    return cursor


async def _prompt_clarification(deps: BotDeps, answering_identity: str, notice) -> None:
    print("\nChoose the correct classification:")
    for i, choice in enumerate(notice.available_classifications, start=1):
        print(f"  [{i}] {choice}")

    while True:
        raw = input("your choice> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(notice.available_classifications):
            chosen = notice.available_classifications[int(raw) - 1]
            break
        if raw in notice.available_classifications:
            chosen = raw
            break
        print("Invalid choice — pick one of the numbers above.")

    await clarification.handle_clarification_answer(deps, CONSOLE_CHAT_ID, answering_identity, notice.event_id, chosen)


async def _prompt_approval(deps: BotDeps, answering_identity: str, notice) -> None:
    _text, buttons = approval.format_approval_prompt(notice)

    print("\nChoose:")
    for i, (label, _data) in enumerate(buttons, start=1):
        print(f"  [{i}] {label}")

    while True:
        raw = input("your choice> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(buttons):
            _, data = buttons[int(raw) - 1]
            break
        print("Invalid choice — pick one of the numbers above.")

    event_id, choice = approval.parse_callback_data(data)
    await approval.handle_approval_answer(deps, CONSOLE_CHAT_ID, answering_identity, event_id, choice)


async def _wait_for_completion(deps: BotDeps, cursor: int, job_id: str, answering_identity: str, poll_interval: float) -> int:
    """Poll `GET /Notifications` exactly as `bot.notifications
    .run_notification_poll_once` does, until the one that finishes
    `job_id` arrives. Interactive holds are answered inline — the
    terminal equivalent of pressing the Telegram inline button.

    Only ever acts on — or even prints — notifications about `job_id`
    itself. `GET /Notifications` is deployment-wide, not scoped to one
    caller or one submission (a real bot fans every notification out to
    every commander); a single-operator CLI waiting on one submission has
    no use for anything else that happens to arrive in the same poll —
    another event a different caller submitted, or, as found live, a
    backlog `_bootstrap_notification_cursor` didn't fully explain away.
    Displaying those anyway (`dispatch_notification`'s real bot code has
    no way to know they're not relevant right now) with no interactive
    gate on them is exactly what made unrelated holds and failures look
    like this tool "processing events on its own" — never move this
    filter to run only on the interactive branches below; the *printing*
    needs it just as much as the answering does.
    """

    print("\n(waiting for a result — Ctrl+C to stop waiting and return to the prompt)")

    while True:
        try:
            notifications, cursor = await deps.api_client.poll_pending_notifications(cursor)
        except ApiRequestError as exc:
            print(f"(polling failed: {exc}; retrying)")
            await asyncio.sleep(poll_interval)
            continue

        for note in notifications:
            subject_id = notification_subject_id(note)
            if subject_id != job_id:
                print(f"(a separate notification arrived for event {subject_id} — kind={note.kind}; ignoring, not the event awaited here)")
                continue

            await dispatch_notification(deps, note)

            if note.kind == "clarification_hold":
                await _prompt_clarification(deps, answering_identity, note.payload)
            elif note.kind == "approval_hold":
                await _prompt_approval(deps, answering_identity, note.payload)
            elif note.kind in ("job_finished", "job_failed"):
                return cursor

        if not notifications:
            await asyncio.sleep(poll_interval)


async def _run_repl(deps: BotDeps, observing_client: ObservingApiClient, base_url: str, test_identity: str, poll_interval: float) -> None:
    cursor = await _bootstrap_notification_cursor(deps)
    mode = choose_mode()

    while mode is not None:
        if mode == "message":
            text = input("\nmessage> ").strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/mode":
                mode = choose_mode()
                continue

            try:
                reply = await handle_incoming_message(deps, test_identity, text, new_message_id())
            except (ApiRequestError, BotError) as exc:
                print(f"(request failed: {exc})")
                continue

            print(reply)

            submission = observing_client.last_submission
            if submission is not None and submission.kind != "question" and submission.job_id:
                cursor = await _wait_for_completion(deps, cursor, submission.job_id, test_identity, poll_interval)

        else:  # event mode
            payload = choose_event_payload(test_identity)
            if payload is None:
                mode = choose_mode()
                continue

            text, sender = payload
            try:
                status, body = submit_event(base_url, text, sender)
            except ApiRequestError as exc:
                print(f"(request failed: {exc})")
                continue

            if status >= 400:
                print(f"submission refused ({status}): {body.get('message', body)}")
                continue

            print(f"submitted: event_id={body['event_id']} status={body['status']}")
            cursor = await _wait_for_completion(deps, cursor, body["event_id"], test_identity, poll_interval)


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

    created_this_session = ensure_test_identity(args.profile, args.identity, "commander", profile_module)

    http_client = HttpApiClient(base_url)
    observing_client = ObservingApiClient(http_client)
    deps = BotDeps(loaded_profile=None, telegram_client=ConsoleTelegramClient(), api_client=observing_client)

    try:
        try:
            asyncio.run(_run_repl(deps, observing_client, base_url, args.identity, args.poll_interval))
        except (KeyboardInterrupt, EOFError):
            # EOFError alongside KeyboardInterrupt: every `input()` call in
            # this REPL can raise it (closed stdin, a piped/scripted input
            # stream running dry, Ctrl+D/Ctrl+Z) — found live producing a
            # raw traceback instead of a clean exit, at the exact moment a
            # real answer was finally due. Same graceful-shutdown treatment
            # as Ctrl+C, not a crash.
            pass
    finally:
        # Runs on every exit path, including an unexpected exception — a
        # deliberately wider net than just the three named cases, so a
        # genuine crash never leaves a stale test identity behind either.
        cleanup_test_identity(args.profile, args.identity, created_this_session)

    print("\nGoodbye.")


if __name__ == "__main__":
    sys.exit(main())
