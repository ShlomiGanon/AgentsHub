"""Permission model (work_plan.md §1.9; docs/Next_Plan.md §2, §6)."""

from enum import Enum, IntEnum


class PermissionLevel(IntEnum):
    VIEWER = 1
    COMMANDER = 2


class RequestedOperation(Enum):
    """Complete vocabulary of externally requestable operations (docs/vocabulary.md).

    Not itself an allowlist — see `ViewerAllowedAction` for the viewer
    policy. Every API route, bot command/callback, and resolved message
    intent maps to exactly one member; see docs/allowed_calls.md's
    "Operation matrix" for the full entry-point mapping.
    """

    SUBMIT_EVENT = "submit_event"
    SUBMIT_MESSAGE = "submit_message"
    CONVERSE = "converse"
    ASK_QUESTION = "ask_question"
    REPORT_EVENT = "report_event"
    REQUEST_ACTION = "request_action"
    LIST_PROTOCOLS = "list_protocols"
    CREATE_PROTOCOL = "create_protocol"
    UPDATE_PROTOCOL = "update_protocol"
    DELETE_PROTOCOL = "delete_protocol"
    VIEW_PROFILE_OVERVIEW = "view_profile_overview"
    VIEW_SYSTEM_INTERNALS = "view_system_internals"
    VIEW_SETTINGS = "view_settings"
    CHANGE_SETTINGS = "change_settings"
    VIEW_USER_REGISTRATION = "view_user_registration"
    VIEW_COMMANDER_ROSTER = "view_commander_roster"
    VIEW_JOB_STATUS = "view_job_status"
    RESOLVE_CLARIFICATION = "resolve_clarification"
    APPROVE_RUN = "approve_run"
    POLL_NOTIFICATIONS = "poll_notifications"
    VIEW_LIVE_TRACE = "view_live_trace"


class ViewerAllowedAction(Enum):
    """The complete viewer authorization policy (docs/Next_Plan.md §2.2, §5 decision record).

    A viewer may perform an operation only if it is a member here. A
    commander is unrestricted by this enum entirely — commander
    authorization never consults it. There is no separate viewer allowlist
    anywhere else in the codebase; route, bot, and prompt code all defer to
    `is_permitted`, so removing a member here denies both execution and
    self-description of that operation for a viewer.
    """

    SUBMIT_EVENT = RequestedOperation.SUBMIT_EVENT.value
    SUBMIT_MESSAGE = RequestedOperation.SUBMIT_MESSAGE.value
    CONVERSE = RequestedOperation.CONVERSE.value
    ASK_QUESTION = RequestedOperation.ASK_QUESTION.value
    REPORT_EVENT = RequestedOperation.REPORT_EVENT.value
    REQUEST_ACTION = RequestedOperation.REQUEST_ACTION.value
    VIEW_PROFILE_OVERVIEW = RequestedOperation.VIEW_PROFILE_OVERVIEW.value
    VIEW_USER_REGISTRATION = RequestedOperation.VIEW_USER_REGISTRATION.value
    VIEW_JOB_STATUS = RequestedOperation.VIEW_JOB_STATUS.value


def _validate_viewer_allowed_actions() -> None:
    """Every `ViewerAllowedAction` member must name a real `RequestedOperation` — an unmapped member is a startup-time configuration error, never a silent viewer grant."""

    known_operation_values = {operation.value for operation in RequestedOperation}
    for member in ViewerAllowedAction:
        if member.value not in known_operation_values:
            raise ValueError(f"ViewerAllowedAction.{member.name} does not map to a RequestedOperation")


_validate_viewer_allowed_actions()

_VIEWER_ALLOWED_OPERATION_VALUES = frozenset(member.value for member in ViewerAllowedAction)


def is_permitted(level: PermissionLevel, operation: RequestedOperation) -> bool:
    """A commander is authorized for every `RequestedOperation` unconditionally.

    A viewer is authorized exactly for `ViewerAllowedAction` members —
    nothing is inferred from `level` comparisons.
    """

    if not isinstance(operation, RequestedOperation):
        raise TypeError(f"operation must be a RequestedOperation, got {operation!r}")

    if level is PermissionLevel.COMMANDER:
        return True

    return operation.value in _VIEWER_ALLOWED_OPERATION_VALUES
