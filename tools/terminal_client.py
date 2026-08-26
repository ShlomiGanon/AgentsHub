"""Terminal stand-in for the Telegram bot (manual end-to-end testing tool).

Lets you exercise a running `api.app` server by typing in a terminal
instead of using Telegram, while going through exactly the same code the
real bot goes through:

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

This process is a CLIENT. It does not start the server. Run the API
separately first, e.g.:

    python -m api.app profiles.demo

(needs CORE_MODEL_*/SUB_MODEL_* environment variables set — the same ones
`api.app.main` itself requires; see `.env.example`.) Then, in another
terminal, with the same environment variables available (needed only for
the one-time `cli.user_admin` provisioning step below):

    python -m tools.terminal_client --profile profiles.demo

Sensor events (§7.3's `POST /Event`) have no bot-side equivalent to reuse
— the bot never submits one, only sensors do — so event mode sends that
request the same minimal way `bot.http_api_client._do_request` sends
every other request, reusing that exact helper rather than a second
hand-rolled HTTP call.
"""

import argparse
import asyncio
import importlib
import sys
import uuid
from typing import Sequence

from bot import approval, clarification
from bot.api_client import BOT_SERVICE_IDENTITY
from bot.deps import BotDeps
from bot.entrypoint import handle_incoming_message
from bot.errors import ApiRequestError, BotError
from bot.formatting import split_message
from bot.http_api_client import HttpApiClient, _do_request
from bot.notifications import dispatch_notification
from bot.telegram_client import TelegramClient
from cli.user_admin import main as user_admin_main
from persistence.interface import open_persistence

CONSOLE_CHAT_ID = "terminal"

# Reuses the demo profile's own event types/areas (profiles/demo.py) so
# these actually exercise real classification instead of tripping a
# clarification hold by accident. Editable at the prompt for any other
# profile, or for deliberately driving a clarification hold.
SAMPLE_EVENT_TEXTS = [
    ("Fire report — north sector", "Smoke and rising temperature reported in north_sector."),
    ("Medical report — south sector", "A person has collapsed and is unconscious in south_sector."),
    (
        "Unclassifiable reading (drives a clarification hold)",
        "Readings received that do not match any known pattern in this deployment.",
    ),
    ("Custom — type your own text", None),
]


class ConsoleTelegramClient(TelegramClient):
    """The one substitution this tool makes: prints exactly what the real
    `PTBTelegramClient` would have sent to Telegram, instead of sending
    it. Every caller (bot.clarification, bot.approval, bot.results,
    bot.failures, bot.precedent_notify, bot.notifications) is unmodified
    real bot code — none of it knows or cares that this isn't Telegram.
    """

    async def validate_token(self) -> bool:
        return True

    async def send_text(self, chat_id: str, text: str) -> None:
        for chunk in split_message(text):
            print(f"\n{chunk}")

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        chunks = split_message(text)
        for chunk in chunks[:-1]:
            print(f"\n{chunk}")
        print(f"\n{chunks[-1] if chunks else ''}")

    async def send_reply(self, chat_id: str, text: str, reply_to_message_id: str | None) -> None:
        await self.send_text(chat_id, text)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        pass

    def run_polling(self, register_handlers) -> None:
        raise NotImplementedError("the terminal client never runs a polling loop of this kind")


class _ObservingApiClient:
    """Delegates every call to the real `HttpApiClient` unchanged, and
    additionally remembers the last `submit_message` result — so the REPL
    can learn the job ID `handle_incoming_message` was given without
    calling `submit_message` a second time itself (which would submit the
    message twice) or re-deriving it by parsing the reply text (which
    would depend on that text's exact wording rather than the real
    return value).
    """

    def __init__(self, inner: HttpApiClient):
        self._inner = inner
        self.last_submission = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def submit_message(self, *args, **kwargs):
        result = await self._inner.submit_message(*args, **kwargs)
        self.last_submission = result
        return result


def _new_message_id() -> str:
    return f"cli-{uuid.uuid4().hex[:8]}"


def ensure_commander_users(profile_module_path: str, test_identity: str, profile_module) -> None:
    """Check the profile's own database directly (persistence layer) for
    a commander-level test user and the bot's own service identity;
    create whichever is missing via the existing `cli.user_admin`
    command — never a new user-creation path.
    """

    store = open_persistence(profile_module.DB_PATH)
    try:
        missing = []
        for identity in (test_identity, BOT_SERVICE_IDENTITY):
            user = store.read_user(identity)
            if user is None or user["permission_level"] != "commander":
                missing.append(identity)
        if not missing:
            print(f"Commander users already present: {test_identity!r}, {BOT_SERVICE_IDENTITY!r}.")
            return
    finally:
        store.close()

    print(f"Provisioning commander user(s) via `cli.user_admin`: {', '.join(missing)}")
    for identity in missing:
        rc = user_admin_main(["--profile", profile_module_path, "add", "--telegram-id", identity, "--level", "commander"])
        if rc != 0:
            raise SystemExit(
                f"failed to provision '{identity}' via cli.user_admin — check that CORE_MODEL_*/SUB_MODEL_* "
                "environment variables are set (the same ones `python -m api.app` itself requires)."
            )


def _submit_event(base_url: str, text: str, sender_identity: str) -> tuple[int, dict]:
    return _do_request(f"{base_url}/Event", "POST", sender_identity, {"text": text, "sender_identity": sender_identity})


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
    .run_notification_poll_once` does, dispatching every notification
    through the real bot code, until the one that finishes `job_id`
    arrives. Interactive holds are answered inline — the terminal
    equivalent of pressing the Telegram inline button.
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
            await dispatch_notification(deps, note)

            if note.kind == "clarification_hold" and note.payload.event_id == job_id:
                await _prompt_clarification(deps, answering_identity, note.payload)
            elif note.kind == "approval_hold" and note.payload.event_id == job_id:
                await _prompt_approval(deps, answering_identity, note.payload)
            elif note.kind == "job_finished" and note.payload.job_id == job_id:
                return cursor
            elif note.kind == "job_failed" and note.payload.event_id == job_id:
                return cursor

        if not notifications:
            await asyncio.sleep(poll_interval)


def _choose_mode() -> str | None:
    while True:
        raw = input("\nMode — [m]essage, [e]vent, or [q]uit? ").strip().lower()
        if raw in ("m", "message"):
            return "message"
        if raw in ("e", "event"):
            return "event"
        if raw in ("q", "quit", "exit"):
            return None
        print("Please type 'm', 'e', or 'q'.")


def _choose_event_payload(default_sender: str) -> tuple[str, str] | None:
    print("\nSample sensor events:")
    for i, (label, _) in enumerate(SAMPLE_EVENT_TEXTS, start=1):
        print(f"  [{i}] {label}")
    print("  [q] back to mode selection")

    raw = input("choose> ").strip().lower()
    if raw in ("q", "quit", "exit"):
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(SAMPLE_EVENT_TEXTS)):
        print("Invalid choice.")
        return _choose_event_payload(default_sender)

    _label, preset_text = SAMPLE_EVENT_TEXTS[int(raw) - 1]
    if preset_text is None:
        text = input("event text> ").strip()
    else:
        typed = input(f"event text [{preset_text}]> ").strip()
        text = typed or preset_text

    typed_sender = input(f"sender identity [{default_sender}]> ").strip()
    sender = typed_sender or default_sender
    return text, sender


async def _run_repl(deps: BotDeps, observing_client: _ObservingApiClient, base_url: str, test_identity: str, poll_interval: float) -> None:
    mode = _choose_mode()
    cursor = 0

    while mode is not None:
        if mode == "message":
            text = input("\nmessage> ").strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/mode":
                mode = _choose_mode()
                continue

            try:
                reply = await handle_incoming_message(deps, test_identity, text, _new_message_id())
            except (ApiRequestError, BotError) as exc:
                print(f"(request failed: {exc})")
                continue

            print(reply)

            submission = observing_client.last_submission
            if submission is not None and submission.kind != "question" and submission.job_id:
                cursor = await _wait_for_completion(deps, cursor, submission.job_id, test_identity, poll_interval)

        else:  # event mode
            payload = _choose_event_payload(test_identity)
            if payload is None:
                mode = _choose_mode()
                continue

            text, sender = payload
            try:
                status, body = _submit_event(base_url, text, sender)
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
        description="Terminal stand-in for the Telegram bot — talks to a running `api.app` server "
        "through the same code the real bot uses (work_plan.md §8), for manual end-to-end testing."
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

    ensure_commander_users(args.profile, args.identity, profile_module)

    http_client = HttpApiClient(base_url)
    observing_client = _ObservingApiClient(http_client)
    deps = BotDeps(loaded_profile=None, telegram_client=ConsoleTelegramClient(), api_client=observing_client)

    try:
        asyncio.run(_run_repl(deps, observing_client, base_url, args.identity, args.poll_interval))
    except KeyboardInterrupt:
        pass

    print("\nGoodbye.")


if __name__ == "__main__":
    sys.exit(main())
