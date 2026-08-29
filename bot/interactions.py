"""Bot commands, holds, user checks, and message formatting."""

from typing import TYPE_CHECKING, Literal

from dataclasses import dataclass

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from messages import MessageCatalog, get_catalog

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
    "event_data_needed",
]

_HEADER_KEYS: dict[MessageKind, str] = {
    "clarification_needed": "header.clarification_needed",
    "approval_needed": "header.approval_needed",
    "precedent_closure": "header.precedent_closure",
    "uncertain_verdict": "header.uncertain_verdict",
    "no_match": "header.no_match",
    "result": "header.result",
    "failed": "header.failed",
    "declined": "header.declined",
    "event_data_needed": "header.event_data_needed",
}


def _catalog(catalog: MessageCatalog | None = None) -> MessageCatalog:
    return catalog or get_catalog("en")


def message_catalog_for(deps) -> MessageCatalog:
    loaded_profile = getattr(deps, "loaded_profile", None)
    return getattr(loaded_profile, "message_catalog", None) or get_catalog("en")


def format_header(kind: MessageKind, catalog: MessageCatalog | None = None) -> str:
    return _catalog(catalog).text(_HEADER_KEYS[kind])


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


def format_job_result(result: "JobResult", catalog: MessageCatalog | None = None) -> str:
    messages = _catalog(catalog)
    kind: MessageKind = "result" if result.outcome != "declined" else "declined"
    lines = [format_header(kind, messages), "", messages.text("result.verdict", outcome=result.outcome)]

    if result.failure_reason:
        lines += ["", result.failure_reason]

    if result.steps_completed:
        lines += ["", messages.text("result.what_was_done")]
        lines += [f"- {step}" for step in result.steps_completed]

    if result.insight_text:
        lines += ["", messages.text("result.insight"), result.insight_text]

    return "\n".join(lines)


def format_failure_notice(notice: "FailureNotice", catalog: MessageCatalog | None = None) -> str:
    messages = _catalog(catalog)
    agent = notice.failed_step_agent_name or messages.text("common.unknown")
    lines = [
        format_header("failed", messages),
        "",
        messages.text("failure.failed_step", agent=agent),
        messages.text("failure.reason", reason=notice.failure_reason),
    ]

    if notice.steps_completed_before_failure:
        lines += ["", messages.text("failure.completed_before")]
        lines += [f"- {step}" for step in notice.steps_completed_before_failure]
    else:
        lines += ["", messages.text("failure.nothing_completed")]

    return "\n".join(lines)


def format_event_data_needed(notice, catalog: MessageCatalog | None = None) -> str:
    return f"{format_header('event_data_needed', catalog)}\n\n{notice.question}"

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


def _unregistered_message(telegram_identity: str, catalog: MessageCatalog | None = None) -> str:
    return _catalog(catalog).text("auth.unregistered", identity=telegram_identity)


async def resolve_caller(
    api_client: "BotApiClient", telegram_identity: str, catalog: MessageCatalog | None = None
) -> UserResolutionResult:
    lookup = await api_client.resolve_user(telegram_identity)

    if not lookup.registered or lookup.permission_level is None:
        return UserResolutionResult(
            status="unregistered",
            refusal_message=_unregistered_message(telegram_identity, catalog),
        )

    level = _LEVEL_BY_NAME[lookup.permission_level]
    return UserResolutionResult(status="ok", caller=CallerContext(telegram_identity=telegram_identity, level=level))


def check_permission(
    caller: CallerContext, operation: RequestedOperation, catalog: MessageCatalog | None = None
) -> str | None:
    """None when `caller` may perform `operation`; otherwise a message naming the refused operation — never a silent no-op (§8.2: "A silent no-op leaves a commander believing they approved s..."""

    if is_permitted(caller.level, operation):
        return None

    return _catalog(catalog).text(
        "auth.operation_refused",
        operation=operation.value,
        identity=caller.telegram_identity,
        level=caller.level.name.lower(),
    )

if TYPE_CHECKING:
    from bot.contracts import BotDeps, ProfileView

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
    view = await deps.api_client.get_profile_view(caller_identity)
    return format_profile_view(view, message_catalog_for(deps))


async def profile_diff_status(deps: "BotDeps") -> str:
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


if TYPE_CHECKING:
    from bot.contracts import BotDeps, SettingsView

SettingField = Literal["retry_count", "risk_threshold", "lookback_window_days"]


def format_settings_view(view: "SettingsView", catalog: MessageCatalog | None = None) -> str:
    return _catalog(catalog).text(
        "settings.view",
        retry_count=view.retry_count,
        risk_threshold=view.risk_threshold,
        lookback_window_days=view.lookback_window_days,
    )


async def view_settings(deps: "BotDeps", caller_identity: str) -> str:
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

    return None, _catalog(catalog).text("settings.unknown", field=repr(field))


async def change_setting(deps: "BotDeps", caller: CallerContext, field: str, raw_value: str) -> str:
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

if TYPE_CHECKING:
    from bot.contracts import BotDeps, HeldApprovalNotice, NoMatchNotice, UncertainVerdictNotice

CALLBACK_PREFIX = "approve"


def build_callback_data(event_id: str, choice: str) -> str:
    return f"{CALLBACK_PREFIX}:{event_id}:{choice}"


def parse_callback_data(data: str) -> tuple[str, str]:
    _, event_id, choice = data.split(":", 2)
    return event_id, choice


def format_approval_prompt(
    notice: "HeldApprovalNotice", catalog: MessageCatalog | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Return (message text, buttons) — buttons differ by hold reason."""

    messages = _catalog(catalog)
    header = format_header("approval_needed", messages)
    common = messages.text(
        "approval.risk", risk_level=notice.risk_level, risk_reason=notice.risk_reason
    )

    if notice.reason == "flagged_protocol":
        text = messages.text(
            "approval.flagged",
            header=header,
            protocol_name=notice.selected_protocol_name,
            risk=common,
        )
        buttons = [
            (messages.text("approval.approve"), build_callback_data(notice.event_id, "approved")),
            (messages.text("approval.reject"), build_callback_data(notice.event_id, "rejected")),
        ]
        return text, buttons

    if notice.reason == "ambiguous_selection":
        candidates = ", ".join(notice.candidate_protocol_names) or messages.text("common.none")
        text = messages.text(
            "approval.ambiguous", header=header, candidates=candidates, risk=common
        )
        buttons = [(name, build_callback_data(notice.event_id, name)) for name in notice.candidate_protocol_names]
        return text, buttons

    raise ValueError(f"unrecognized approval hold reason: {notice.reason!r}")


async def push_approval_prompt(deps: "BotDeps", notice: "HeldApprovalNotice") -> None:
    text, buttons = format_approval_prompt(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def format_uncertain_verdict_notice(
    notice: "UncertainVerdictNotice", catalog: MessageCatalog | None = None
) -> str:
    messages = _catalog(catalog)
    return messages.text(
        "notice.uncertain",
        header=format_header("uncertain_verdict", messages),
        event_id=notice.event_id,
        insight=notice.insight_text,
    )


async def notify_uncertain_verdict(deps: "BotDeps", notice: "UncertainVerdictNotice") -> None:
    text = format_uncertain_verdict_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


def format_no_match_notice(notice: "NoMatchNotice", catalog: MessageCatalog | None = None) -> str:
    messages = _catalog(catalog)
    why = notice.reason or messages.text("common.no_reason")
    return messages.text(
        "notice.no_match",
        header=format_header("no_match", messages),
        raw_text=notice.raw_text,
        reason=why,
        risk_level=notice.risk_level,
        risk_reason=notice.risk_reason,
    )


async def notify_no_match(deps: "BotDeps", notice: "NoMatchNotice") -> None:
    text = format_no_match_notice(notice, message_catalog_for(deps))

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_text(chat_id, text)


def _describe_outcome(outcome, catalog: MessageCatalog | None = None) -> str:
    messages = _catalog(catalog)
    if outcome.status == "approved":
        return messages.text("approval.resumed")

    if outcome.status == "rejected":
        return messages.text("approval.rejected")

    if outcome.status in ("unauthorized", "invalid_classification", "invalid_candidate"):
        return outcome.message

    who = messages.text("common.by_identity", identity=outcome.resolved_by) if outcome.resolved_by else ""
    return messages.text("approval.already_answered", who=who, message=outcome.message).strip()


async def handle_approval_answer(deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, choice: str) -> None:
    """`choice` is already "approved"/"rejected" for a flagged-protocol hold (the button's callback data), or the chosen candidate's protocol name for an ambiguous-selection hold — see..."""

    messages = message_catalog_for(deps)
    resolution = await resolve_caller(deps.api_client, answering_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, RequestedOperation.APPROVE_RUN, messages)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_approval_hold(event_id, choice, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_outcome(outcome, messages))


if TYPE_CHECKING:
    from bot.contracts import BotDeps, HeldClarificationNotice

CLARIFICATION_CALLBACK_PREFIX = "clarify"


def build_clarification_callback_data(event_id: str, classification: str) -> str:
    return f"{CLARIFICATION_CALLBACK_PREFIX}:{event_id}:{classification}"


def parse_clarification_callback_data(data: str) -> tuple[str, str]:
    _, event_id, classification = data.split(":", 2)
    return event_id, classification


def format_clarification_prompt(
    notice: "HeldClarificationNotice", catalog: MessageCatalog | None = None
) -> str:
    messages = _catalog(catalog)
    return messages.text(
        "clarification.prompt",
        header=format_header("clarification_needed", messages),
        raw_text=notice.raw_text,
        field=notice.unresolved_field,
    )


async def push_clarification_prompt(deps: "BotDeps", notice: "HeldClarificationNotice") -> None:
    text = format_clarification_prompt(notice, message_catalog_for(deps))
    buttons = [(choice, build_clarification_callback_data(notice.event_id, choice)) for choice in notice.available_classifications]

    for chat_id in await deps.api_client.list_commander_chat_ids():
        await deps.telegram_client.send_with_buttons(chat_id, text, buttons)


def _describe_clarification_outcome(outcome, catalog: MessageCatalog | None = None) -> str:
    messages = _catalog(catalog)
    if outcome.status == "resolved":
        return messages.text("clarification.resumed")

    if outcome.status == "unauthorized":
        return outcome.message

    if outcome.status == "invalid_classification":
        return outcome.message

    # "not_found": already resolved, by this same race or someone else —
    # never silently re-accepted as if it were the first answer (§8.4).
    who = messages.text("common.by_identity", identity=outcome.resolved_by) if outcome.resolved_by else ""
    return messages.text("clarification.already_resolved", who=who, message=outcome.message).strip()


async def handle_clarification_answer(
    deps: "BotDeps", chat_id: str, answering_identity: str, event_id: str, chosen_classification: str
) -> None:
    messages = message_catalog_for(deps)
    resolution = await resolve_caller(deps.api_client, answering_identity, messages)
    if resolution.status == "unregistered":
        await deps.telegram_client.send_text(chat_id, resolution.refusal_message)
        return

    refusal = check_permission(resolution.caller, RequestedOperation.RESOLVE_CLARIFICATION, messages)
    if refusal is not None:
        await deps.telegram_client.send_text(chat_id, refusal)
        return

    outcome = await deps.api_client.answer_clarification_hold(event_id, chosen_classification, answering_identity)
    await deps.telegram_client.send_text(chat_id, _describe_clarification_outcome(outcome, messages))
