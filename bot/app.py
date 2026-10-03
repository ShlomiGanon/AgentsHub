"""Telegram bot process facade: dispatch, handlers, and wiring re-exports."""

from bot.dispatch import (
    handle_incoming_message,
    present_incoming_message,
)
from bot.handlers import (
    _bot_commands,
    _guarded,
    _on_callback_query,
    _on_my_chat_member,
    _on_profile_command,
    _on_settings_command,
    _on_start_command,
    _on_text_message,
    _parse_protocol_write_command,
    register_handlers,
)
from bot.runtime_state import (
    ATTENDANCE_CHECK_INTERVAL_SECONDS,
    GROUP_CHAT_TYPES,
    NOTIFICATION_POLL_INTERVAL_SECONDS,
    REGISTERED_COMMANDS,
    _PENDING_UNAVAILABILITY,
    clear_caller_cache,
)
from bot.wiring import (
    _resolve_bot_token,
    _validate_bot_token,
    build_deps,
    main,
    run_bot,
)

__all__ = [
    "ATTENDANCE_CHECK_INTERVAL_SECONDS",
    "GROUP_CHAT_TYPES",
    "NOTIFICATION_POLL_INTERVAL_SECONDS",
    "REGISTERED_COMMANDS",
    "build_deps",
    "clear_caller_cache",
    "handle_incoming_message",
    "main",
    "present_incoming_message",
    "register_handlers",
    "run_bot",
]


if __name__ == "__main__":
    main()
