"""Shared building blocks for `tools/terminal_client_commander.py` and
`tools/terminal_client_viewer.py` — not a governed entry point (same status
`tools/terminal_client.py` itself had), imported only by those two files.

Holds everything genuinely identical between the two roles: the console
`TelegramClient` substitute, the observing API-client wrapper, event-mode
submission, and test-identity provisioning/cleanup. Anything whose behavior
actually differs by permission level (what happens when a hold shows up,
whether an interactive answer is even offered) stays in each role's own
file, not here — see those files' own module docstrings for why.
"""

import sys
import uuid
from typing import Sequence

from bot.api_client import BOT_SERVICE_IDENTITY
from bot.formatting import split_message
from bot.http_api_client import HttpApiClient, _do_request
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
    """The one substitution either tool makes: prints exactly what the
    real `PTBTelegramClient` would have sent to Telegram, instead of
    sending it. Every caller (bot.clarification, bot.approval,
    bot.results, bot.failures, bot.precedent_notify, bot.notifications)
    is unmodified real bot code — none of it knows or cares that this
    isn't Telegram.
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


class ObservingApiClient:
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


def new_message_id() -> str:
    return f"cli-{uuid.uuid4().hex[:8]}"


def notification_subject_id(note) -> str | None:
    """The event/job this notification is about, whatever its kind calls
    that field — `event_id` for holds, closures, and `job_failed`'s
    `FailureNotice` payload; `job_id` for `job_finished`'s `JobResult`
    payload. Never assume which attribute a given kind's payload carries;
    both CLIs check membership/relevance against whatever this returns.
    """

    return getattr(note.payload, "event_id", None) or getattr(note.payload, "job_id", None)


def submit_event(base_url: str, text: str, sender_identity: str) -> tuple[int, dict]:
    return _do_request(f"{base_url}/Event", "POST", sender_identity, {"text": text, "sender_identity": sender_identity})


def choose_mode() -> str | None:
    while True:
        raw = input("\nMode — [m]essage, [e]vent, or [q]uit? ").strip().lower()
        if raw in ("m", "message"):
            return "message"
        if raw in ("e", "event"):
            return "event"
        if raw in ("q", "quit", "exit"):
            return None
        print("Please type 'm', 'e', or 'q'.")


def choose_event_payload(default_sender: str) -> tuple[str, str] | None:
    print("\nSample sensor events:")
    for i, (label, _) in enumerate(SAMPLE_EVENT_TEXTS, start=1):
        print(f"  [{i}] {label}")
    print("  [q] back to mode selection")

    raw = input("choose> ").strip().lower()
    if raw in ("q", "quit", "exit"):
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(SAMPLE_EVENT_TEXTS)):
        print("Invalid choice.")
        return choose_event_payload(default_sender)

    _label, preset_text = SAMPLE_EVENT_TEXTS[int(raw) - 1]
    if preset_text is None:
        text = input("event text> ").strip()
    else:
        typed = input(f"event text [{preset_text}]> ").strip()
        text = typed or preset_text

    typed_sender = input(f"sender identity [{default_sender}]> ").strip()
    sender = typed_sender or default_sender
    return text, sender


def ensure_bot_service_commander(profile_module_path: str, profile_module) -> None:
    """`bot-service` is a durable, deployment-wide fixture both CLIs need
    at commander level (`list_commander_chat_ids`/`poll_pending_notifications`
    always authenticate as it, regardless of which role's test identity is
    driving the session) — never a per-session identity either CLI owns.
    Checked and provisioned the same way as any test identity (real
    persistence read, real `cli.user_admin` write), but its creation
    status is never tracked or reported for cleanup — see
    `cleanup_test_identity`'s own docstring for why it's never a cleanup
    target at all, by construction.
    """

    store = open_persistence(profile_module.DB_PATH)
    try:
        user = store.read_user(BOT_SERVICE_IDENTITY)
        if user is not None and user["permission_level"] == "commander":
            return
    finally:
        store.close()

    print(f"Provisioning the bot's own service identity via `cli.user_admin`: {BOT_SERVICE_IDENTITY!r}")
    rc = user_admin_main(["--profile", profile_module_path, "add", "--telegram-id", BOT_SERVICE_IDENTITY, "--level", "commander"])
    if rc != 0:
        raise SystemExit(
            f"failed to provision {BOT_SERVICE_IDENTITY!r} via cli.user_admin — check that CORE_MODEL_*/SUB_MODEL_* "
            "environment variables are set (the same ones `python -m api.app` itself requires)."
        )


def ensure_test_identity(profile_module_path: str, test_identity: str, level: str, profile_module) -> bool:
    """Check the real database directly (persistence layer) for
    `test_identity` at `level`; provision it via the existing
    `cli.user_admin` command if missing or at the wrong level — never a
    new user-creation path. Also ensures `bot-service` (see
    `ensure_bot_service_commander`), independently of `test_identity`.

    Returns whether *this call* had to create or change `test_identity`
    — `True` only when it did. The caller's cleanup-on-exit must act only
    when this is `True`: an identity that already existed, at the right
    level, before this session started is not this session's to remove.
    """

    ensure_bot_service_commander(profile_module_path, profile_module)

    store = open_persistence(profile_module.DB_PATH)
    try:
        user = store.read_user(test_identity)
        already_correct = user is not None and user["permission_level"] == level
    finally:
        store.close()

    if already_correct:
        print(f"{level.capitalize()}-level identity already present: {test_identity!r}.")
        return False

    print(f"Provisioning {level}-level identity via `cli.user_admin`: {test_identity!r}")
    rc = user_admin_main(["--profile", profile_module_path, "add", "--telegram-id", test_identity, "--level", level])
    if rc != 0:
        raise SystemExit(
            f"failed to provision {test_identity!r} via cli.user_admin — check that CORE_MODEL_*/SUB_MODEL_* "
            "environment variables are set (the same ones `python -m api.app` itself requires)."
        )
    return True


def cleanup_test_identity(profile_module_path: str, test_identity: str, created_this_session: bool) -> None:
    """Remove `test_identity` via the same real `cli.user_admin` command
    used to create it (`remove`, not a new deletion path) — but only when
    `created_this_session` is `True` (an identity this session found
    already present, at the right level, is left untouched on exit; see
    `ensure_test_identity`'s own docstring). Never called with
    `BOT_SERVICE_IDENTITY`: it's a durable, shared fixture no single
    session owns, provisioned by `ensure_bot_service_commander` but never
    passed to this function by either CLI's `main()`.

    A failed removal is never silent: it prints exactly which identity is
    left behind and the command to remove it by hand.
    """

    if not created_this_session:
        return

    rc = user_admin_main(["--profile", profile_module_path, "remove", "--telegram-id", test_identity])
    if rc != 0:
        print(
            f"WARNING: failed to remove test identity {test_identity!r} — it has been left behind. "
            f"Remove it manually with: python -m cli.user_admin --profile {profile_module_path} "
            f"remove --telegram-id {test_identity}",
            file=sys.stderr,
        )
