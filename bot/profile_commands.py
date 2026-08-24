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
"the system just changed" confirmation the way `bot.settings_commands`'s
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


async def view_profile(deps: "BotDeps") -> str:
    view = await deps.api_client.get_profile_view()
    return format_profile_view(view)


async def profile_diff_status(deps: "BotDeps") -> str:
    status = await deps.api_client.get_profile_diff_status()

    if status.differs_from_running:
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

    result = await deps.api_client.write_protocol(action, protocol_payload)

    if not result.accepted:
        return f"Rejected: {result.message}"

    return f"{result.message}\n\n{NOTHING_CHANGED_NOTICE}"
