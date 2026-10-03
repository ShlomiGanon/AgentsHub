"""Profile, protocol, and settings commands sent through the bot."""

from typing import TYPE_CHECKING, Literal

from auth.permissions import RequestedOperation
from messages import MessageCatalog, get_catalog

from bot.interaction_format import _catalog, message_catalog_for
from bot.interaction_users import CallerContext, check_permission

if TYPE_CHECKING:
    from bot.contracts import BotDeps, ProfileView, SettingsView


# --- profile ---

NOTHING_CHANGED_NOTICE = get_catalog("en").text("profile.nothing_changed")



def format_profile_view(view: "ProfileView", catalog: MessageCatalog | None = None) -> str:
    """Agents and protocols are commander-only (`view_system_internals`); a viewer's
    `ProfileView` simply arrives with those fields empty, so their sections are
    omitted entirely here rather than shown as an empty, misleading heading."""

    messages = _catalog(catalog)
    lines = [messages.text("profile.name", profile_name=view.profile_name)]

    if view.agent_names:
        lines += ["", messages.text("profile.agents"), *[f"- {name}" for name in view.agent_names]]

    if view.protocols:
        lines += ["", messages.text("profile.protocols")]
        for protocol in view.protocols:
            flag_key = "profile.protocol_requires_approval" if protocol.approval_flag else "profile.protocol_no_approval"
            lines.append(
                messages.text(
                    "profile.protocol_line",
                    name=protocol.name,
                    criticality=protocol.criticality,
                    approval=messages.text(flag_key),
                    description=protocol.description,
                )
            )

    lines += [
        "",
        messages.text("profile.event_types", event_types=", ".join(view.event_types)),
        messages.text("profile.areas", areas=", ".join(view.areas)),
    ]

    return "\n".join(lines)



async def view_profile(deps: "BotDeps", caller_identity: str) -> str:
    """Fetch and format the live profile view for this caller."""

    view = await deps.api_client.get_profile_view(caller_identity)
    return format_profile_view(view, message_catalog_for(deps))



async def profile_diff_status(deps: "BotDeps") -> str:
    """Whether a profile write is waiting for the next restart."""

    status = await deps.api_client.get_profile_diff_status()

    if status:
        return message_catalog_for(deps).text("profile.restart_pending")

    return message_catalog_for(deps).text("profile.restart_not_pending")



def _validate_protocol_write_payload(
    action: Literal["add", "edit", "remove"], payload: dict, catalog: MessageCatalog | None = None
) -> str | None:
    """None if `payload` is acceptable to send on; otherwise the refusal message."""

    if action == "remove":
        return None

    if "approval_flag" not in payload or not isinstance(payload.get("approval_flag"), bool):
        return _catalog(catalog).text("protocol.approval_flag_required")

    return None



_PROTOCOL_WRITE_OPERATIONS: dict[str, RequestedOperation] = {
    "add": RequestedOperation.CREATE_PROTOCOL,
    "edit": RequestedOperation.UPDATE_PROTOCOL,
    "remove": RequestedOperation.DELETE_PROTOCOL,
}



async def write_protocol(
    deps: "BotDeps", caller: CallerContext, action: Literal["add", "edit", "remove"], protocol_payload: dict
) -> str:
    """Add, edit, or remove a protocol after permission and payload checks."""

    messages = message_catalog_for(deps)
    refusal = check_permission(caller, _PROTOCOL_WRITE_OPERATIONS[action], messages)
    if refusal is not None:
        return refusal

    validation_refusal = _validate_protocol_write_payload(action, protocol_payload, messages)
    if validation_refusal is not None:
        return validation_refusal

    protocol_write_result = await deps.api_client.write_protocol(action, protocol_payload, caller.telegram_identity)

    if not protocol_write_result.accepted:
        return messages.text("common.rejected", message=protocol_write_result.message)

    return f"{protocol_write_result.message}\n\n{messages.text('profile.nothing_changed')}"


# --- settings ---

SettingField = Literal["retry_count", "risk_threshold", "lookback_window_days", "safe_mode"]



def format_settings_view(view: "SettingsView", catalog: MessageCatalog | None = None) -> str:
    """Readable listing of the four live settings."""

    messages = _catalog(catalog)
    base = messages.text(
        "settings.view",
        retry_count=view.retry_count,
        risk_threshold=view.risk_threshold,
        lookback_window_days=view.lookback_window_days,
    )
    return f"{base}\n{messages.text('settings.safe_mode_state', value=str(view.safe_mode).lower())}"



async def view_settings(deps: "BotDeps", caller_identity: str) -> str:
    """Fetch and format live settings for this caller."""

    view = await deps.api_client.get_settings_view(caller_identity)
    return format_settings_view(view, message_catalog_for(deps))



def _validate_value(
    field: SettingField, raw_value: str, catalog: MessageCatalog | None = None
) -> tuple[object | None, str | None]:
    """Returns (parsed_value, refusal_message) — exactly one is not None."""

    if field == "retry_count":
        try:
            parsed_value = int(raw_value)
        except ValueError:
            return None, _catalog(catalog).text("settings.retry_whole", value=repr(raw_value))
        if parsed_value < 0:
            return None, _catalog(catalog).text("settings.retry_nonnegative")
        return parsed_value, None

    if field == "risk_threshold":
        try:
            parsed_value = float(raw_value)
        except ValueError:
            return None, _catalog(catalog).text("settings.risk_number", value=repr(raw_value))
        if not (0.0 <= parsed_value <= 1.0):
            return None, _catalog(catalog).text("settings.risk_range")
        return parsed_value, None

    if field == "lookback_window_days":
        try:
            parsed_value = int(raw_value)
        except ValueError:
            return None, _catalog(catalog).text("settings.lookback_whole", value=repr(raw_value))
        if parsed_value <= 0:
            return None, _catalog(catalog).text("settings.lookback_positive")
        return parsed_value, None

    if field == "safe_mode":
        normalized = raw_value.strip().casefold()
        if normalized == "true":
            return True, None
        if normalized == "false":
            return False, None
        return None, _catalog(catalog).text("settings.safe_mode_boolean")

    return None, _catalog(catalog).text("settings.unknown", field=repr(field))



async def change_setting(deps: "BotDeps", caller: CallerContext, field: str, raw_value: str) -> str:
    """Parse, authorize, and persist one settings field. Returns the saved or refused message."""

    messages = message_catalog_for(deps)
    refusal = check_permission(caller, RequestedOperation.CHANGE_SETTINGS, messages)
    if refusal is not None:
        return refusal

    setting_value, validation_refusal = _validate_value(field, raw_value, messages)  # type: ignore[arg-type]
    if validation_refusal is not None:
        return validation_refusal

    setting_write_result = await deps.api_client.write_setting(field, setting_value, caller.telegram_identity)

    if not setting_write_result.accepted:
        return messages.text("common.rejected", message=setting_write_result.message)

    return messages.text("settings.saved", message=setting_write_result.message)
