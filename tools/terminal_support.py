"""Shared support for the commander and viewer terminal clients."""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Sequence

from bot.transports import HttpApiClient, TelegramClient, _do_request
from bot.contracts import BOT_SERVICE_IDENTITY
from bot.interactions import split_message
from messages import MessageCatalog, get_catalog
from persistence import open_persistence

CONSOLE_CHAT_ID = "terminal"
do_request = _do_request


def _run_user_admin(argv: list[str]) -> int:
    from cli.user_admin import main

    return main(argv)


async def ainput(prompt: str = "") -> str:
    """`input()`, off the event loop."""

    return await asyncio.to_thread(input, prompt)

SAMPLE_EVENT_TEXTS = [
    ("terminal.sample_fire", "Smoke and rising temperature reported in north_sector."),
    ("terminal.sample_medical", "A person has collapsed and is unconscious in south_sector."),
    (
        "terminal.sample_unknown",
        "Readings received that do not match any known pattern in this deployment.",
    ),
    ("terminal.sample_custom", None),
]


def _catalog(catalog: MessageCatalog | None = None) -> MessageCatalog:
    return catalog or get_catalog("en")


class ConsoleTelegramClient(TelegramClient):
    """The one substitution either tool makes: prints exactly what the real `PTBTelegramClient` would have sent to Telegram, instead of sending it."""

    def __init__(self, owning_identity: str):
        self._owning_identity = owning_identity
        self._next_status_id = 1

    def _addressed_to_this_console(self, chat_id: str) -> bool:
        return chat_id in (self._owning_identity, CONSOLE_CHAT_ID)

    async def validate_token(self) -> bool:
        return True

    async def send_text(self, chat_id: str, text: str) -> None:
        if not self._addressed_to_this_console(chat_id):
            return
        for chunk in split_message(text):
            print(f"\n{chunk}")

    async def send_status(self, chat_id: str, text: str) -> str:
        status_id = f"console-status-{self._next_status_id}"
        self._next_status_id += 1
        if self._addressed_to_this_console(chat_id):
            print(f"\n{text}", end="", flush=True)
        return status_id

    async def edit_status(self, chat_id: str, message_id: str, text: str) -> None:
        if self._addressed_to_this_console(chat_id):
            print(f"\r\x1b[2K{text}")

    async def delete_status(self, chat_id: str, message_id: str) -> None:
        if self._addressed_to_this_console(chat_id):
            print("\r\x1b[2K", end="", flush=True)

    async def send_with_buttons(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]]) -> None:
        if not self._addressed_to_this_console(chat_id):
            return
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
    """Delegates every call to the real `HttpApiClient` unchanged, and additionally remembers the last `submit_message` result — so the REPL can learn the job ID `handle_incoming_messa..."""

    def __init__(self, inner: HttpApiClient):
        self._inner = inner
        self.last_submission = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def submit_message(self, *args, **kwargs):
        submission_result = await self._inner.submit_message(*args, **kwargs)
        self.last_submission = submission_result
        return submission_result


def new_message_id() -> str:
    return f"cli-{uuid.uuid4().hex[:8]}"


def notification_subject_id(note) -> str | None:
    """The event/job this notification is about, whatever its kind calls that field — `event_id` for holds, closures, and `job_failed`'s `FailureNotice` payload; `job_id` for `job_fini..."""

    return getattr(note.payload, "event_id", None) or getattr(note.payload, "job_id", None)


def submit_event(base_url: str, text: str, sender_identity: str) -> tuple[int, dict]:
    return do_request(f"{base_url}/Event", "POST", sender_identity, {"text": text, "sender_identity": sender_identity})


async def choose_mode(catalog: MessageCatalog | None = None) -> str | None:
    messages = _catalog(catalog)
    while True:
        selected_option = (await ainput(messages.text("terminal.mode_prompt"))).strip().lower()
        if selected_option in ("m", "message"):
            return "message"
        if selected_option in ("e", "event"):
            return "event"
        if selected_option in ("q", "quit", "exit"):
            return None
        print(messages.text("terminal.mode_invalid"))


async def choose_event_payload(
    default_sender: str, catalog: MessageCatalog | None = None
) -> tuple[str, str] | None:
    messages = _catalog(catalog)
    print(messages.text("terminal.sample_events"))
    for choice_number, (label_key, _) in enumerate(SAMPLE_EVENT_TEXTS, start=1):
        print(f"  [{choice_number}] {messages.text(label_key)}")
    print(messages.text("terminal.back"))

    selected_option = (await ainput(messages.text("terminal.choose_prompt"))).strip().lower()
    if selected_option in ("q", "quit", "exit"):
        return None
    if not selected_option.isdigit() or not (1 <= int(selected_option) <= len(SAMPLE_EVENT_TEXTS)):
        print(messages.text("terminal.invalid_choice"))
        return await choose_event_payload(default_sender, messages)

    _label, preset_text = SAMPLE_EVENT_TEXTS[int(selected_option) - 1]
    if preset_text is None:
        text = (await ainput(messages.text("terminal.event_text"))).strip()
    else:
        typed = (await ainput(messages.text("terminal.event_text_default", default=preset_text))).strip()
        text = typed or preset_text

    typed_sender = (await ainput(messages.text("terminal.sender_default", default=default_sender))).strip()
    sender = typed_sender or default_sender
    return text, sender


def ensure_bot_service_commander(
    profile_module_path: str, profile_module, catalog: MessageCatalog | None = None
) -> None:
    """`bot-service` is a durable, deployment-wide fixture both CLIs need at commander level (`list_commander_chat_ids`/`poll_pending_notifications` always authenticate as it, regardle..."""

    store = open_persistence(profile_module.DB_PATH)
    try:
        user = store.read_user(BOT_SERVICE_IDENTITY)
        if user is not None and user["permission_level"] == "commander":
            return
    finally:
        store.close()

    messages = _catalog(catalog)
    print(messages.text("terminal.provision_service", identity=repr(BOT_SERVICE_IDENTITY)))
    exit_code = _run_user_admin(["--profile", profile_module_path, "add", "--telegram-id", BOT_SERVICE_IDENTITY, "--level", "commander"])
    if exit_code != 0:
        raise SystemExit(
            f"failed to provision {BOT_SERVICE_IDENTITY!r} via cli.user_admin — check that CORE_MODEL_*/SUB_MODEL_* "
            "environment variables are set (the same ones `python -m api.app` itself requires)."
        )


def ensure_test_identity(
    profile_module_path: str,
    test_identity: str,
    level: str,
    profile_module,
    catalog: MessageCatalog | None = None,
) -> bool:
    """Check the real database directly (persistence layer) for `test_identity` at `level`; provision it via the existing `cli.user_admin` command if missing or at the wrong level — ne..."""

    messages = _catalog(catalog)
    ensure_bot_service_commander(profile_module_path, profile_module, messages)

    store = open_persistence(profile_module.DB_PATH)
    try:
        user = store.read_user(test_identity)
        already_correct = user is not None and user["permission_level"] == level
    finally:
        store.close()

    if already_correct:
        print(
            messages.text(
                "terminal.identity_exists", level=level.capitalize(), identity=repr(test_identity)
            )
        )
        return False

    print(
        messages.text(
            "terminal.provision_identity", level=level, identity=repr(test_identity)
        )
    )
    exit_code = _run_user_admin(["--profile", profile_module_path, "add", "--telegram-id", test_identity, "--level", level])
    if exit_code != 0:
        raise SystemExit(
            f"failed to provision {test_identity!r} via cli.user_admin — check that CORE_MODEL_*/SUB_MODEL_* "
            "environment variables are set (the same ones `python -m api.app` itself requires)."
        )
    return True


def cleanup_test_identity(profile_module_path: str, test_identity: str, created_this_session: bool, cursor_path: Path | None = None) -> None:
    """Remove `test_identity` via the same real `cli.user_admin` command used to create it (`remove`, not a new deletion path) — but only when `created_this_session` is `True` (an iden..."""

    if not created_this_session:
        return

    exit_code = _run_user_admin(["--profile", profile_module_path, "remove", "--telegram-id", test_identity])
    if exit_code != 0:
        print(
            f"WARNING: failed to remove test identity {test_identity!r} — it has been left behind. "
            f"Remove it manually with: python -m cli.user_admin --profile {profile_module_path} "
            f"remove --telegram-id {test_identity}",
            file=sys.stderr,
        )

    if cursor_path is not None:
        try:
            cursor_path.unlink()
        except FileNotFoundError:
            pass
