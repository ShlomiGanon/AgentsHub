"""Terminal stand-in for a commander's Telegram session (manual testing tool).

Lets you exercise a running `api.app` server as a commander-level user by
typing in a terminal instead of using Telegram, while going through exactly
the same code the real bot goes through:

  * `bot.app.handle_incoming_message` for free-form text (message
    mode) — the same function `bot.app._on_text_message` calls.
  * `bot.http_api_client.HttpApiClient` for every HTTP call to the API —
    the bot's own real client, not a stub.
  * `bot.notifications.NotificationCursorStore` and
    `bot.notifications.run_notification_poll_loop`'s own shape (a
    continuous background task, started at process startup, dispatching
    every notification unconditionally) for everything that arrives
    asynchronously — the same mechanism `bot.app` gives the real bot,
    reused as closely as a single shared terminal (see below) allows.

Nothing here reimplements message wording, permission checks, or dispatch
semantics — `bot.notifications.dispatch_notification` plus
`bot.holds`/`bot.holds`'s own formatting and answer-handling are
unmodified real bot code throughout.

Every capability here — resolving a clarification hold, answering an
approval — is commander-only per `auth/permissions.py`'s own
`ACTION_REQUIREMENTS` (`resolve_hold`, `approve_run`, both COMMANDER); this
file exists because that table says so, not because of anything decided
here. See `tools/terminal_client_viewer.py` for the VIEWER-permitted
counterpart (`send_message`, `view_history`) — message and event mode only,
no hold-answering capability, because the real permission table gives a
viewer none.

Background polling, and why it replaced the old reactive wait (found live,
2026-08): a prior version of this tool only ever polled `GET /Notifications`
as a side effect of the operator's own submission, filtering out anything
not about that specific job. A hold created by someone else's activity
while this process sat idle at the prompt — even *after* it had already
started — was therefore never shown, no matter how long it stayed open,
because nothing ever polled again until the operator made a submission of
their own, and even then only their own job's notifications were surfaced.
The real bot has no such gap: `bot.app`'s `_post_init` starts
`run_notification_poll_loop` as a standing background task the moment the
bot's own polling starts, and `dispatch_notification` has no per-job filter
at all — every notification is handled, for every event, unconditionally.
This file now does the same: `_background_poll_loop` starts at REPL
startup and never stops until the process exits, using the same
`NotificationCursorStore` the real bot persists to disk with (a separate,
per-identity file — see `main`'s own comment on the path — so two
concurrent commander sessions, or a real bot, never fight over one cursor).

One companion problem this creates and solves, not present in the real
bot: `bot.notifications.deliver_job_result` (and the hold-broadcast functions in
`bot.holds`/`bot.holds`) send to whichever `chat_id`s they're
given — safe in a real deployment, where different chat_ids are different
people's separate phones, but this tool's `ConsoleTelegramClient` used to
print regardless of `chat_id`. Fully unconditional dispatch onto one shared
terminal would otherwise print *other* identities' job results and
duplicate hold broadcasts (one per registered commander) alongside this
session's own. `tools._terminal_client_shared.ConsoleTelegramClient` is now
identity-aware for exactly this reason — constructed with the one identity
this terminal represents, silently dropping anything addressed elsewhere,
the same way a real deployment's *other* chats never see it either.

The other companion problem: printing a hold's prompt the instant the
background task discovers it would interrupt the operator mid-keystroke at
the "message>" prompt — a different, and worse, failure than the one this
redesign fixes. So the background task never prints anything itself; it
only accumulates what arrived (`_BackgroundState.arrived`) and the
foreground loop drains and displays it exactly once per turn — right
before showing the next prompt, never during one (`_drain_arrived`, called
at the top of every REPL iteration). A hold surfaced this way still needs
an answer, and the terminal has no equivalent of Telegram's out-of-band
button tap — so answering happens through an explicit `/holds` command,
typed at the "message>" prompt whenever the operator is ready, which walks
every not-yet-answered hold via the same `_prompt_clarification`/
`_prompt_approval` UI. Each offers a `[s]` "skip for now" choice that sends
nothing to the server and leaves the hold genuinely open — re-queued for a
later `/holds` call, since the notification stream will never redeliver
something the cursor has already advanced past.

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
from pathlib import Path

from bot.interface import (
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
from tools._terminal_client_shared import (
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
    """Shared, single-writer-single-reader state between the background
    poll task and the foreground REPL loop — safe with no locking since
    both run as coroutines on the one asyncio event loop (never truly
    concurrent at the bytecode level); the background task only ever
    appends/overwrites, the foreground only ever drains at a point where
    the background task is guaranteed to be suspended (`await`ing its own
    next poll or sleep).
    """

    def __init__(self):
        self.arrived: list = []
        self.poll_error: str | None = None


async def _initial_cursor(deps: BotDeps, cursor_store: NotificationCursorStore, cursor_path: Path, identity: str) -> int:
    """First-ever run for this identity+deployment: fast-forward past
    whatever is already in `notification_log`, without displaying or
    acting on any of it, then persist that starting point immediately.

    Found live in an earlier pass: the demo (or any shared/reused)
    database persists `notification_log` across every process that has
    ever run against it — sensor-simulator runs, earlier manual testing,
    another developer's session. Starting from cursor 0, as the real bot's
    own first-ever deployment run would, replays that entire backlog on
    the very first poll. A genuinely fresh real deployment doesn't hit
    this (its own `notification_log` starts empty), but this tool's own
    database very much does — worth keeping this one-time skip rather than
    matching the real bot's "replay once is fine" stance literally.

    Every run *after* this one — including a plain restart — reads the
    persisted cursor back and resumes from exactly there, same as the real
    bot: nothing taken here is re-bootstrapped or reset on a later run.
    """

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
    """Mirrors `bot.notifications.run_notification_poll_loop`'s own shape
    — a standing loop, started once, running until told to stop — but
    never calls `dispatch_notification` itself: printing here would land
    in the middle of whatever the operator is mid-typing at the "message>"
    prompt. It only accumulates (`state.arrived`) for `_drain_arrived` to
    display at the next safe point. A failed poll is recorded the same way
    (`state.poll_error`), never printed directly, for the same reason.
    """

    while not stop_event.is_set():
        try:
            notifications, cursor = await deps.api_client.poll_pending_notifications(cursor)
        except ApiRequestError as exc:
            state.poll_error = str(exc)
        else:
            state.poll_error = None
            cursor_store.write(cursor)
            state.arrived.extend(notifications)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass


async def _drain_arrived(deps: BotDeps, state: _BackgroundState, pending_holds: list) -> None:
    """The one place anything the background task found gets shown —
    called at the top of every REPL iteration, i.e. always between turns,
    never while a prompt is outstanding. Every notification is dispatched
    unconditionally (no job-scoped filtering, matching the real bot); a
    hold needs more than a dispatched print to be acted on, so it's also
    queued for `/holds`.
    """

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
    """Returns True once resolved, False if skipped. A skipped hold is
    re-queued by the caller (`_handle_holds_command`), never dropped — the
    notification stream will never redeliver something the cursor has
    already advanced past, so this is the only way to see it again.
    """

    print(f"\nClarification hold — event {notice.event_id}")
    print("Choose the correct classification:")
    for i, choice in enumerate(notice.available_classifications, start=1):
        print(f"  [{i}] {choice}")
    print("  [s] Skip for now (leave this hold open)")

    while True:
        raw = (await ainput("your choice> ")).strip()
        if raw.lower() == "s":
            print("(skipped — this hold is still open; use /holds to come back to it)")
            return False
        if raw.isdigit() and 1 <= int(raw) <= len(notice.available_classifications):
            chosen = notice.available_classifications[int(raw) - 1]
            break
        if raw in notice.available_classifications:
            chosen = raw
            break
        print("Invalid choice — pick one of the numbers above, or 's' to skip.")

    await handle_clarification_answer(deps, CONSOLE_CHAT_ID, answering_identity, notice.event_id, chosen)
    return True


async def _prompt_approval(deps: BotDeps, answering_identity: str, notice) -> bool:
    """Same return contract as `_prompt_clarification`. Re-renders the
    prompt text via the same `approval.format_approval_prompt` the
    background drain already printed once — reached again here possibly
    much later, via `/holds`, after other output has scrolled by.

    `format_approval_prompt` always returns at least one button for every
    approval hold that can be created today: `flagged_protocol` always has
    exactly two (Approve/Reject), and `ambiguous_selection` always has two
    or more by construction (an "ambiguous" selection with fewer than two
    genuine candidates couldn't have been reported as ambiguous in the
    first place). The former third case, a report-only no-buttons hold for
    `orchestrator.main_agent`'s NO_MATCH outcome, no longer reaches this
    function at all — that's a real terminal outcome plus a one-way
    notification now (`bot.holds.notify_no_match`), never a
    `held_events` row, so there is nothing here left to display or skip.
    """

    text, buttons = format_approval_prompt(notice)
    print(f"\n{text}")
    print("\nChoose:")
    for i, (label, _data) in enumerate(buttons, start=1):
        print(f"  [{i}] {label}")
    print("  [s] Skip for now (leave this hold open)")

    while True:
        raw = (await ainput("your choice> ")).strip()
        if raw.lower() == "s":
            print("(skipped — this hold is still open; use /holds to come back to it)")
            return False
        if raw.isdigit() and 1 <= int(raw) <= len(buttons):
            _, data = buttons[int(raw) - 1]
            break
        print("Invalid choice — pick one of the numbers above, or 's' to skip.")

    event_id, choice = parse_approval_callback_data(data)
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
                    # Drain again, right here: the top-of-loop drain ran
                    # *before* this prompt was shown, so anything that
                    # arrived while the operator was typing "/holds" itself
                    # is still sitting silently in `state.arrived` — without
                    # this, they'd have to invoke /holds a second time to
                    # see it.
                    await _drain_arrived(deps, state, pending_holds)
                    await _handle_holds_command(deps, pending_holds, test_identity)
                    continue

                try:
                    reply = await handle_incoming_message(deps, test_identity, text, new_message_id())
                except (ApiRequestError, BotError) as exc:
                    print(f"(request failed: {exc})")
                    continue

                print(reply)
                # No synchronous wait for a result here — whatever happens
                # to this submission, including landing on a hold, arrives
                # asynchronously through the background loop above, the
                # same way a real commander's own Telegram chat works.

            else:  # event mode
                payload = await choose_event_payload(test_identity)
                if payload is None:
                    mode = await choose_mode()
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
    finally:
        stop_event.set()
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass


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
    deps = BotDeps(loaded_profile=None, telegram_client=ConsoleTelegramClient(args.identity), api_client=observing_client)

    # Distinct from the real bot's own `{db_path}.notification_cursor` (so
    # this tool never fights a real bot process over one cursor file) and
    # scoped per `--identity` (so two commander sessions testing
    # independently — different identities against the same deployment —
    # each resume their own progress rather than clobbering one another's).
    cursor_path = Path(f"{profile_module.DB_PATH}.notification_cursor.terminal_commander.{args.identity}")
    cursor_store = NotificationCursorStore(cursor_path)

    try:
        try:
            asyncio.run(
                _run_repl(deps, observing_client, base_url, args.identity, args.poll_interval, cursor_store, cursor_path)
            )
        except (KeyboardInterrupt, EOFError):
            # EOFError alongside KeyboardInterrupt: every `ainput()` call in
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
        # cursor_path travels with it (see cleanup_test_identity's own
        # docstring): deleting the identity without also deleting its
        # cursor file is exactly what let a later session provisioning the
        # same default name replay a dead identity's old notification
        # backlog instead of bootstrapping fresh (found live, 2026-08).
        cleanup_test_identity(args.profile, args.identity, created_this_session, cursor_path=cursor_path)

    print("\nGoodbye.")


if __name__ == "__main__":
    sys.exit(main())
