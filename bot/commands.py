"""Profile commands (work_plan.md §8.7).

Reads are open to any registered user (commander or viewer — §8.7's own
"allow viewers to read"); there is no dedicated action key for "view the
profile" in `auth.permissions.ACTION_REQUIREMENTS` because §1.9's table
lists none, so a read here checks only that the caller is registered
(`bot.users.resolve_caller`), the same baseline every bot interaction
already requires. Every write is restricted to commander level via the
existing `"edit_profile"` action, and every write reply states — the same
sentence every time — that the running system is unchanged and the edit
applies from the next start, so a write reply is never mistaken for a
"the system just changed" confirmation the way `bot.commands`'s
replies are.
"""

from typing import TYPE_CHECKING, Literal

from bot.users import CallerContext, check_permission

if TYPE_CHECKING:
    from bot.api_client import ProfileView
    from bot.deps import BotDeps

NOTHING_CHANGED_NOTICE = "Nothing has changed in the running system — this edit applies from the next start."


def format_profile_view(view: "ProfileView") -> str:
    lines = [
        f"Profile: {view.profile_name}",
        "",
        "Agents:",
        *[f"- {name}" for name in view.agent_names],
        "",
        "Protocols:",
    ]

    for protocol in view.protocols:
        flag = "requires approval" if protocol.approval_flag else "no approval required"
        lines.append(f"- {protocol.name} (criticality: {protocol.criticality}, {flag}): {protocol.description}")

    lines += ["", "Event types: " + ", ".join(view.event_types), "Areas: " + ", ".join(view.areas)]

    return "\n".join(lines)


async def view_profile(deps: "BotDeps", caller_identity: str) -> str:
    view = await deps.api_client.get_profile_view(caller_identity)
    return format_profile_view(view)


async def profile_diff_status(deps: "BotDeps") -> str:
    status = await deps.api_client.get_profile_diff_status()

    if status:
        return "The profile file on disk differs from what is running. A restart is pending to pick up the change."

    return "The profile file on disk matches what is running. No restart is pending."


def _validate_protocol_write_payload(action: Literal["add", "edit", "remove"], payload: dict) -> str | None:
    """None if `payload` is acceptable to send on; otherwise the refusal
    message. Only `add`/`edit` carry an `approval_flag` at all — `remove`
    identifies a protocol by name and has nothing to flag. Where it does
    apply, it must be given explicitly — never defaulted — since it is
    the field that decides whether an action runs unattended.
    """

    if action == "remove":
        return None

    if "approval_flag" not in payload or not isinstance(payload.get("approval_flag"), bool):
        return "Refused: 'approval_flag' must be given explicitly as true or false — it is never defaulted."

    return None


async def write_protocol(
    deps: "BotDeps", caller: CallerContext, action: Literal["add", "edit", "remove"], protocol_payload: dict
) -> str:
    refusal = check_permission(caller, "edit_profile")
    if refusal is not None:
        return refusal

    validation_refusal = _validate_protocol_write_payload(action, protocol_payload)
    if validation_refusal is not None:
        return validation_refusal

    result = await deps.api_client.write_protocol(action, protocol_payload, caller.telegram_identity)

    if not result.accepted:
        return f"Rejected: {result.message}"

    return f"{result.message}\n\n{NOTHING_CHANGED_NOTICE}"

"""Settings commands (work_plan.md §8.8).

Reads are open to any registered user, same reasoning as
`bot.commands` (no dedicated "view settings" action key exists
in `auth.permissions.ACTION_REQUIREMENTS`). Writes require commander
level via the existing `"change_settings"` action and are validated
before ever reaching the API — a negative retry count or a zero-length
lookback window is a configuration error, not an operator preference,
per work_plan.md §7.8's own framing (mirrored here since the bot is the
first place such a value is typed in). Every successful write is
confirmed as having taken effect *at once* — the direct opposite of
`bot.commands`'s "nothing changed until restart" wording, stated
explicitly so a commander using both never confuses which is which.
"""

from typing import TYPE_CHECKING, Literal

from bot.users import CallerContext, check_permission

if TYPE_CHECKING:
    from bot.api_client import SettingsView
    from bot.deps import BotDeps

SettingField = Literal["retry_count", "risk_threshold", "lookback_window_days"]


def format_settings_view(view: "SettingsView") -> str:
    return (
        f"Retry count: {view.retry_count}\n"
        f"Risk threshold: {view.risk_threshold}\n"
        f"Lookback window (days): {view.lookback_window_days}"
    )


async def view_settings(deps: "BotDeps", caller_identity: str) -> str:
    view = await deps.api_client.get_settings_view(caller_identity)
    return format_settings_view(view)


def _validate_value(field: SettingField, raw_value: str) -> tuple[object | None, str | None]:
    """Returns (parsed_value, refusal_message) — exactly one is not None."""

    if field == "retry_count":
        try:
            value = int(raw_value)
        except ValueError:
            return None, f"Refused: 'retry_count' must be a whole number, got {raw_value!r}."
        if value < 0:
            return None, "Refused: 'retry_count' cannot be negative."
        return value, None

    if field == "risk_threshold":
        try:
            value = float(raw_value)
        except ValueError:
            return None, f"Refused: 'risk_threshold' must be a number, got {raw_value!r}."
        if not (0.0 <= value <= 1.0):
            return None, "Refused: 'risk_threshold' must be between 0.0 and 1.0."
        return value, None

    if field == "lookback_window_days":
        try:
            value = int(raw_value)
        except ValueError:
            return None, f"Refused: 'lookback_window_days' must be a whole number, got {raw_value!r}."
        if value <= 0:
            return None, "Refused: 'lookback_window_days' must be at least 1 — a zero-length window is a configuration error."
        return value, None

    return None, f"Refused: unknown setting {field!r}. Only retry_count, risk_threshold, and lookback_window_days may be changed."


async def change_setting(deps: "BotDeps", caller: CallerContext, field: str, raw_value: str) -> str:
    refusal = check_permission(caller, "change_settings")
    if refusal is not None:
        return refusal

    value, validation_refusal = _validate_value(field, raw_value)  # type: ignore[arg-type]
    if validation_refusal is not None:
        return validation_refusal

    result = await deps.api_client.write_setting(field, value, caller.telegram_identity)

    if not result.accepted:
        return f"Rejected: {result.message}"

    return f"{result.message}\n\nThis took effect immediately and has been saved — unlike a profile edit, no restart is needed."



