"""The error contract (work_plan.md §7.10)."""

import logging
from typing import TYPE_CHECKING

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from auth.permissions import PermissionLevel, is_permitted
from api.contracts import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    RunFailureError,
)
from tools import get_trace_id

if TYPE_CHECKING:
    from persistence import PersistenceInterface

logger = logging.getLogger(__name__)
















_GENERIC_INTERNAL_MESSAGE = "an internal error occurred"

IDENTITY_HEADER = "X-Identity"


def authenticate(persistence: "PersistenceInterface", identity: str | None) -> PermissionLevel:
    if not identity:
        raise AuthenticationError("no identity supplied")

    user = persistence.read_user(identity)
    if user is None:
        raise AuthenticationError(f"'{identity}' is not a registered identity")

    return PermissionLevel[user["permission_level"].upper()]


def require(level: PermissionLevel, action: str) -> None:
    if not is_permitted(level, action):
        raise AuthorizationError(f"level {level.name} may not {action.replace('_', ' ')}")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError):
        # Every refused/failed request used to produce zero log records at
        # all (§1.8 follow-up coverage audit gap) — nothing to debug a
        # 400/401/403/404/409/422 from except the response Flask already
        # sent the caller. `trace_id` is best-effort here: a request that
        # fails authentication or input validation typically hasn't
        # entered `trace_context` yet (both checks run before a route body
        # generates one) and legitimately has none — `get_trace_id()`
        # returns "" in that case, same as any other untraced record, not
        # a bug in this handler. `RunFailureError` is the one class raised
        # from *inside* an active trace_context; note in the module
        # docstring's own known-limits section that the trace context has
        # already unwound (and the ID with it) by the time Flask's error
        # handling machinery reaches here, so even that case logs without
        # one today — a correlation gap, not a crash risk, and out of
        # scope for this pass.
        logger.warning(
            "API request refused",
            extra={
                "event": "api_error", "error_class": error.error_class, "status_code": error.status_code,
                "error_message": error.message, "field": error.field, "trace_id": get_trace_id(),
            },
        )
        body = {"error_class": error.error_class, "message": error.message}
        if error.field is not None:
            body["field"] = error.field
        return jsonify(body), error.status_code

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException):
        body = {"error_class": "invalid_input", "message": error.description or error.name}
        return jsonify(body), error.code or 500

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):
        logger.exception(
            "unhandled exception in an API request",
            extra={"event": "api_unexpected_error", "trace_id": get_trace_id()},
        )
        return jsonify({"error_class": "internal_error", "message": _GENERIC_INTERNAL_MESSAGE}), 500
