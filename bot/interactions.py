"""Bot commands, holds, user checks, and message formatting."""

from typing import TYPE_CHECKING, Literal

from dataclasses import dataclass

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.contracts import FailureNotice, JobResult

TELEGRAM_MESSAGE_LIMIT = 4096

MessageKind = Literal[
    "clarification_needed",
    "approval_needed",
    "precedent_closure",
    "uncertain_verdict",
    "no_match",
    "result",
    "failed",
    "declined",
]

_HEADERS: dict[MessageKind, str] = {
    "clarification_needed": "[CLARIFICATION NEEDED — please reply]",
    "approval_needed": "[APPROVAL NEEDED — please reply]",
    "precedent_closure": "[NOTICE — closed on precedent — no reply needed]",
    "uncertain_verdict": "[NOTICE — uncertain verdict — no reply needed]",
    "no_match": "[NOTICE — no protocol available — no reply needed]",
    "result": "[RESULT]",
    "failed": "[RUN FAILED]",
    "declined": "[DECLINED]",
}


def format_header(kind: MessageKind) -> str:
    return _HEADERS[kind]


def _split_on(text: str, separator: str, limit: int) -> list[str] | None:
    """Greedily pack `text` into chunks no longer than `limit`, breaking only at `separator` boundaries."""

    units = text.split(separator)
    chunks: list[str] = []
    current = ""

    for unit in units:
        candidate = unit if not current else current + separator + unit

        if len(candidate) <= limit:
            current = candidate
            continue

        if not current:
            return None

        chunks.append(current)
        current = unit

        if len(current) > limit:
            return None

    if current:
        chunks.append(current)

    return chunks


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split `text` into chunks that each fit in one Telegram message, breaking at paragraph boundaries first, then sentence boundaries, then plain newlines, and only as a last resort..."""

    if len(text) <= limit:
        return [text] if text else [""]

    for separator in ("\n\n", ". ", "\n"):
        chunks = _split_on(text, separator, limit)
        if chunks is not None:
            return chunks

    return [text[i : i + limit] for i in range(0, len(text), limit)]


def format_job_result(result: "JobResult") -> str:
    kind: MessageKind = "result" if result.outcome != "declined" else "declined"
    lines = [format_header(kind), "", f"Verdict: {result.outcome}"]

    if result.failure_reason:
        lines += ["", result.failure_reason]

    if result.steps_completed:
        lines += ["", "What was done:"]
        lines += [f"- {step}" for step in result.steps_completed]

    if result.insight_text:
        lines += ["", "Insight:", result.insight_text]

    return "\n".join(lines)


def format_failure_notice(notice: "FailureNotice") -> str:
    lines = [format_header("failed"), "", f"Failed step: {notice.failed_step_agent_name or '(unknown)'}", f"Reason: {notice.failure_reason}"]

    if notice.steps_completed_before_failure:
        lines += ["", "Completed before the failure:"]
        lines += [f"- {step}" for step in notice.steps_completed_before_failure]
    else:
        lines += ["", "Nothing completed before the failure."]

    return "\n".join(lines)

if TYPE_CHECKING:
    from bot.contracts import BotApiClient

_LEVEL_BY_NAME: dict[str, PermissionLevel] = {"viewer": PermissionLevel.VIEWER, "commander": PermissionLevel.COMMANDER}


@dataclass(frozen=True)
class CallerContext:
    telegram_identity: str
    level: PermissionLevel


@dataclass(frozen=True)
class UserResolutionResult:
    status: Literal["ok", "unregistered"]
    caller: CallerContext | None = None
    refusal_message: str = ""


def _unregistered_message(telegram_identity: str) -> str:
    return (
        f"You are not a registered user of this system (identity: {telegram_identity}). "
        f"An administrator must add you before you can use this bot."
    )


async def resolve_caller(api_client: "BotApiClient", telegram_identity: str) -> UserResolutionResult:
    lookup = await api_client.resolve_user(telegram_identity)

    if not lookup.registered or lookup.permission_level is None:
        return UserResolutionResult(status="unregistered", refusal_message=_unregistered_message(telegram_identity))

    level = _LEVEL_BY_NAME[lookup.permission_level]
    return UserResolutionResult(status="ok", caller=CallerContext(telegram_identity=telegram_identity, level=level))


def check_permission(caller: CallerContext, operation: RequestedOperation) -> str | None:
    """None when `caller` may perform `operation`; otherwise a message naming the refused operation — never a silent no-op (§8.2: "A silent no-op leaves a commander believing they approved s..."""

    if is_permitted(caller.level, operation):
        return None

    return (
        f"Refused: '{operation.value}' requires commander level; your account "
        f"({caller.telegram_identity}) is registered as {caller.level.name.lower()}."
    )

if TYPE_CHECKING:
    from bot.contracts import BotDeps, ProfileView

NOTHING_CHANGED_NOTICE = "Nothing has changed in the running system — this edit applies from the next start."


def format_profile_view(view: "ProfileView") -> str:
    """Agents and protocols are commander-only (`view_system_internals`); a viewer's
    `ProfileView` simply arrives with those fields empty, so their sections are
    omitted entirely here rather than shown as an empty, misleading heading."""

    lines = [f"Profile: {view.profile_name}"]

    if view.agent_names:
        lines += ["", "Agents:", *[f"- {name}" for name in view.agent_names]]

    if view.protocols:
        lines += ["", "Protocols:"]
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
    """None if `payload` is acceptable to send on; otherwise the refusal message."""

    if action == "remove":
        return None

    if "approval_flag" not in payload or not isinstance(payload.get("approval_flag"), bool):
        return "Refused: 'approval_flag' must be given explicitly as true or false — it is never defaulted."

    return None


_PROTOCOL_WRITE_OPERATIONS: dict[str, RequestedOperation] = {
    "add": RequestedOperation.CREATE_PROTOCOL,
    "edit": RequestedOperation.UPDATE_PROTOCOL,
    "remove": RequestedOperation.DELETE_PROTOCOL,
}


async def write_protocol(
    deps: "BotDeps", caller: CallerContext, action: Literal["add", "edit", "remove"], protocol_payload: dict
) -> str:
    refusal = check_permission(caller, _PROTOCOL_WRITE_OPERATIONS[action])
    if refusal is not None:
        return refusal

    validation_refusal = _validate_protocol_write_payload(action, protocol_payload)
    if validation_refusal is not None:
        return validation_refusal

    protocol_write_result = await deps.api_client.write_protocol(action, protocol_payload, caller.telegram_identity)

    if not protocol_write_result.accepted:
        return f"Rejected: {protocol_write_result.message}"

    return f"{protocol_write_result.message}\n\n{NOTHING_CHANGED_NOTICE}"


if TYPE_CHECKING:
    from bot.contracts import BotDeps, SettingsView

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
            parsed_value = int(raw_value)
        except ValueError:
            return None, f"Refused: 'retry_count' must be a whole number, got {raw_value!r}."
        if parsed_value < 0:
            return None, "Refused: 'retry_count' cannot be negative."
        return parsed_value, None

    if field == "risk_threshold":
        try:
            parsed_value = float(raw_value)
        except ValueError:
            return None, f"Refused: 'risk_threshold' must be a number, got {raw_value!r}."
        if not (0.0 <= parsed_value <= 1.0):
            return None, "Refused: 'risk_threshold' must be between 0.0 and 1.0."
        return parsed_value, None

    if field == "lookback_window_days":
        try:
            parsed_value = int(raw_value)
        except ValueError:
            return None, f"Refused: 'lookback_window_days' must be a whole number, got {raw_value!r}."
        if parsed_value <= 0:
            return None, "Refused: 'lookback_window_days' must be at least 1 — a zero-length window is a configuration error."
        return parsed_value, None

    return None, f"Refused: unknown setting {field!r}. Only retry_count, risk_threshold, and lookback_window_days may be changed."


async def change_setting(deps: "BotDeps", caller: CallerContext, field: str, raw_value: str) -> str:
    refusal = check_permission(caller, RequestedOperation.CHANGE_SETTINGS)
    if refusal is not None:
        return refusal

    setting_value, validation_refusal = _validate_value(field, raw_value)  # type: ignore[arg-type]
    if validation_refusal is not None:
        return validation_refusal

    setting_write_result = await deps.api_client.write_setting(field, setting_value, caller.telegram_identity)

    if not setting_write_result.accepted:
        return f"Rejected: {setting_write_result.message}"

    return f"{setting_write_result.message}\n\nThis took effect immediately and has been saved — unlike a profile edit, no restart is needed."

if TYPE_CHECKING:
    from bot.contracts import BotDeps, HeldApprovalNotice, NoMatchNotice, UncertainVerdictNotice

CALLBACK_PREFIX = "approve"


def build_callback_data(event_id: str, choice: str) -> str:
    return f"{CALLBACK_PREFIX}:{event_id}:{choice}"


def parse_callback_data(data: str) -> tuple[str, str]:
    _, event_id, choice = data.split(":", 2)
    return event_id, choice


def format_approval_prompt(notice: "HeldApprovalNotice") -> tuple[str, list[tuple[str, str]]]:
    """Return (message text, buttons) — buttons differ by hold reason."""

    header = format_header("approval_needed")
    common = f"Risk: {notice.risk_level} ({notice.risk_reason})"

    if notice.reason == "flagged_protocol":
        text = (
            f"{header}\n\n"
            f"Protocol flagged for approval: {notice.selected_protocol_name}\n"
            f"{common}\n\n"
            f"Should this run?"
        )
        buttons = [("Approve", build_callback_data(notice.event_id, "approved")), ("Reject", build_callback_data(notice.event_id, "rejected"))]
        return text, buttons

    if notice.reason == "ambiguous_selection":
        candidates = ", ".join(notice.candidate_protocol_names) or "(none)"
        text = f"{header}\n\nMultiple protocols fit equally well:\n{candidates}\n{common}\n\nWhich should run?"
        buttons = [(name, build_callback_data(notice.event_id, name)) for name in notice.candidate_protocol_names]
        return text, buttons

    raise ValueError(f"unrecognized approval hold reason: {notice.reason!r}")


async def push_approval_prompt(deps: "BotDeps", notice: "HeldApprovalNotice") -> None:
    text, buttons = format_approval_prompt(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def format_uncertain_verdict_notice(notice: "UncertainVerdictNotice") -> str:
    return f"{format_header('uncertain_verdict')}\n\nEvent {notice.event_id} finished with an uncertain verdict.\n\nInsight:\n{notice.insight_text}"


async def notify_uncertain_verdict(deps: "BotDeps", notice: "UncertainVerdictNotice") -> None:
    text = format_uncertain_verdict_notice(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


def format_no_match_notice(notice: "NoMatchNotice") -> str:
    why = notice.reason or "(no reason given)"
    return (
        f"{format_header('no_match')}\n\n"
        f"No existing protocol can fulfill this request.\n"
        f"Raw text: {notice.raw_text}\n"
        f"{why}\n"
        f"Risk: {notice.risk_level} ({notice.risk_reason})"
    )


async def notify_no_match(deps: "BotDeps", notice: "NoMatchNotice") -> None:
    text = format_no_match_notice(notice)

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


def _describe_outcome(outcome) -> str:
    if outcome.status == "approved":
        return "Recorded — the protocol has been resumed."

    if outcome.status == "rejected":
        return "Recorded — declined; the event will not run."

    if outcome.status in ("unauthorized", "invalid_classification", "invalid_candidate"):
        return outcome.message

    who = f" by {outcome.resolved_by}" if outcome.resolved_by else ""
    return f"This approval was already answered{who}. {outcome.message}".strip()


async def handle_approval_answer(deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, choice: str) -> None:
    """`choice` is already "approved"/"rejected" for a flagged-protocol hold (the button's callback data), or the chosen candidate's protocol name for an ambiguous-selection hold — see..."""

    resolution = await resolve_caller(deps.api_client, answering_identity)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, RequestedOperation.APPROVE_RUN)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_approval_hold(event_id, choice, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_outcome(outcome))


if TYPE_CHECKING:
    from bot.contracts import BotDeps, HeldClarificationNotice

CLARIFICATION_CALLBACK_PREFIX = "clarify"


def build_clarification_callback_data(event_id: str, classification: str) -> str:
    return f"{CLARIFICATION_CALLBACK_PREFIX}:{event_id}:{classification}"


def parse_clarification_callback_data(data: str) -> tuple[str, str]:
    _, event_id, classification = data.split(":", 2)
    return event_id, classification


def format_clarification_prompt(notice: "HeldClarificationNotice") -> str:
    return (
        f"{format_header('clarification_needed')}\n\n"
        f"Raw report:\n{notice.raw_text}\n\n"
        f"Could not resolve: {notice.unresolved_field}.\n"
        f"Choose the correct classification below."
    )


async def push_clarification_prompt(deps: "BotDeps", notice: "HeldClarificationNotice") -> None:
    text = format_clarification_prompt(notice)
    buttons = [(choice, build_clarification_callback_data(notice.event_id, choice)) for choice in notice.available_classifications]

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def _describe_clarification_outcome(outcome) -> str:
    if outcome.status == "resolved":
        return "Recorded — the flow has resumed with your choice."

    if outcome.status == "unauthorized":
        return outcome.message

    if outcome.status == "invalid_classification":
        return outcome.message

    # "not_found": already resolved, by this same race or someone else —
    # never silently re-accepted as if it were the first answer (§8.4).
    who = f" by {outcome.resolved_by}" if outcome.resolved_by else ""
    return f"This clarification was already resolved{who}. {outcome.message}".strip()


async def handle_clarification_answer(
    deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, chosen_classification: str
) -> None:
    resolution = await resolve_caller(deps.api_client, answering_identity)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, RequestedOperation.RESOLVE_CLARIFICATION)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_clarification_hold(event_id, chosen_classification, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_clarification_outcome(outcome))
