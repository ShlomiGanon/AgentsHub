"""Terminal stand-in for a viewer's Telegram session (manual testing tool).

Lets you exercise a running `api.app` server as a viewer-level user by
typing in a terminal instead of using Telegram, while going through exactly
the same code the real bot goes through:

  * `bot.entrypoint.handle_incoming_message` for free-form text (message
    mode) — the same function `bot.app._on_text_message` calls.
  * `bot.http_api_client.HttpApiClient` for every HTTP call to the API —
    the bot's own real client, not a stub.
  * `bot.notifications.dispatch_notification` for the one notification
    kind a viewer's own chat is ever actually addressed by — see below.

Nothing here reimplements message wording, permission checks, or polling
semantics — it only substitutes a `bot.telegram_client.TelegramClient`
that prints to the terminal instead of calling the real Telegram API.

Message mode and event mode only — `auth/permissions.py`'s own
`ACTION_REQUIREMENTS` gives `send_message` a VIEWER minimum, so both
`POST /Msg` and `POST /Event` are permitted here exactly as they are in
`tools/terminal_client_commander.py`. There is no hold-answering
capability here at all: `resolve_hold`/`approve_run` both require
COMMANDER, so a viewer identity could never do anything with one anyway.

What a viewer sees when their own submission lands on a hold — the one
piece of behavior genuinely specific to this file, not shared with the
commander tool:

  A real viewer's Telegram chat is never addressed by a
  `clarification_hold`/`approval_hold`/`uncertain_verdict`/
  `precedent_closure` notification at all. `api/notifications.py` never
  computes a recipient list for these kinds (`target_chat_ids` is always
  `[]`) — the bot resolves who gets them itself, via
  `list_commander_chat_ids()`, which a viewer identity is never a member
  of. So `_wait_for_completion` below calls that same real function and
  checks real membership before ever calling `dispatch_notification` for
  one of these kinds — never a hardcoded "viewers don't see holds" rule.
  When the check comes back negative (the normal case for a real viewer
  identity), *nothing is shown*: no message, no rephrasing of "this was
  held" — because that is exactly what a real viewer's chat receives.
  The eventual `job_finished`/`job_failed` result *is* still delivered,
  the same way it always is for the real bot: addressed directly to
  whoever submitted the event (`target_chat_ids`, computed from
  `sender_identity`, no permission check at all), reused unchanged here.

  Consequence worth knowing before you rely on this: if your own
  submission lands on a hold, this tool will sit at "waiting for a
  result" with no further output until a commander resolves it (in a
  real deployment, or via `tools/terminal_client_commander.py`) and the
  run reaches a finished/failed outcome. That silence is the accurate
  behavior, not a hang.

This process is a CLIENT. It does not start the server. Run the API
separately first, e.g.:

    python -m api.app profiles.demo

(needs CORE_MODEL_*/SUB_MODEL_* environment variables set — the same ones
`api.app.main` itself requires; see `.env.example`.) Then, in another
terminal, with the same environment variables available (needed only for
the one-time `cli.user_admin` provisioning step below):

    python -m tools.terminal_client_viewer --profile profiles.demo

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

from bot.deps import BotDeps
from bot.entrypoint import handle_incoming_message
from bot.errors import ApiRequestError, BotError
from bot.http_api_client import HttpApiClient
from bot.notifications import dispatch_notification
from tools._terminal_client_shared import (
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

# Notification kinds a real bot addresses only to commanders
# (`bot.clarification.push_clarification_prompt`,
# `bot.approval.push_approval_prompt`/`notify_uncertain_verdict`,
# `bot.precedent_notify.notify_precedent_closure` — each calls
# `list_commander_chat_ids()` directly, never anything the submitter's own
# identity could be a member of unless it happens to *also* be a
# commander, which `_wait_for_completion` checks for real rather than
# assuming). Named here only to say which kinds need that membership
# check at all; `job_finished`/`job_failed` are the one pair with a real,
# already-computed `target_chat_ids` and skip it entirely.
_COMMANDER_ONLY_KINDS = ("clarification_hold", "approval_hold", "uncertain_verdict", "precedent_closure")


async def _bootstrap_notification_cursor(deps: BotDeps) -> int:
    """Fast-forward past every notification that already exists before
    this session starts — see `tools.terminal_client_commander`'s own
    identical function for the full reasoning (the same shared/reused
    database, and the same "replays its whole history from cursor 0"
    failure mode, apply here regardless of role).
    """

    notifications, cursor = await deps.api_client.poll_pending_notifications(0)
    if notifications:
        print(
            f"(skipping {len(notifications)} pre-existing notification(s) already in this deployment's "
            "history, from before this session started)"
        )
    return cursor


async def _wait_for_completion(deps: BotDeps, cursor: int, job_id: str, viewer_identity: str, poll_interval: float) -> int:
    """Poll `GET /Notifications` exactly as `bot.notifications
    .run_notification_poll_once` does, until the one that finishes
    `job_id` arrives — but, unlike the commander tool, never offers an
    interactive answer to anything: a viewer identity cannot resolve a
    hold (`resolve_hold`/`approve_run` are both COMMANDER-only), and, per
    this module's own docstring, a real viewer's chat is never even shown
    one to begin with.

    For `job_finished`/`job_failed` (about this `job_id`): the real,
    already-computed `target_chat_ids` says whether `viewer_identity` is
    the addressee — exactly the same field the API built from
    `sender_identity`, unmodified.

    For every other kind (about this `job_id`, or any other event — see
    `_COMMANDER_ONLY_KINDS`): real membership in `list_commander_chat_ids()`
    decides whether to show it at all. This is checked with the real API
    call every time, not assumed `False` for "a viewer file" — an
    `--identity` that happens to already be a commander must still see
    what a commander would see.
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
            if note.kind in ("job_finished", "job_failed"):
                if notification_subject_id(note) != job_id:
                    continue  # someone else's job entirely — not addressed to us, not what we're waiting on
                if viewer_identity not in note.target_chat_ids:
                    continue  # this job's result is real, but it isn't ours to see
                await dispatch_notification(deps, note)
                return cursor

            elif note.kind in _COMMANDER_ONLY_KINDS:
                commander_chat_ids = await deps.api_client.list_commander_chat_ids()
                if viewer_identity in commander_chat_ids:
                    # Only reachable if --identity is *also* a real commander —
                    # real membership, checked live, not assumed impossible.
                    await dispatch_notification(deps, note)
                # else: nothing is shown — exactly what a real viewer's chat receives.

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
        description="Terminal stand-in for a viewer's Telegram session — talks to a running `api.app` "
        "server through the same code the real bot uses (work_plan.md §8), for manual end-to-end testing."
    )
    parser.add_argument("--profile", default="profiles.demo", help="dotted profile module path (default: profiles.demo)")
    # Deliberately not "cli_tester" — that's terminal_client_commander.py's
    # default; a different default avoids the two tools accidentally
    # sharing one identity (and one cleanup's assumptions) by coincidence.
    parser.add_argument("--identity", default="viewer_tester", help="viewer-level test identity to act as (default: viewer_tester)")
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

    created_this_session = ensure_test_identity(args.profile, args.identity, "viewer", profile_module)

    http_client = HttpApiClient(base_url)
    observing_client = ObservingApiClient(http_client)
    deps = BotDeps(loaded_profile=None, telegram_client=ConsoleTelegramClient(), api_client=observing_client)

    try:
        try:
            asyncio.run(_run_repl(deps, observing_client, base_url, args.identity, args.poll_interval))
        except (KeyboardInterrupt, EOFError):
            # EOFError alongside KeyboardInterrupt: every `input()` call in
            # this REPL can raise it (closed stdin, a piped/scripted input
            # stream running dry, Ctrl+D/Ctrl+Z) — same graceful-shutdown
            # treatment as Ctrl+C, not a crash.
            pass
    finally:
        # Runs on every exit path, including an unexpected exception — a
        # deliberately wider net than just the three named cases, so a
        # genuine crash never leaves a stale test identity behind either.
        cleanup_test_identity(args.profile, args.identity, created_this_session)

    print("\nGoodbye.")


if __name__ == "__main__":
    sys.exit(main())
