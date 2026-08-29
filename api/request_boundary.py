"""Authentication, authorization, and HTTP error translation."""

import logging
from typing import TYPE_CHECKING

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from tools import get_trace_id

if TYPE_CHECKING:
    from persistence import PersistenceInterface

logger = logging.getLogger(__name__)


class ApiError(Exception):
    error_class = "invalid_input"
    status_code = 400

    def __init__(self, message: str, field: str | None = None):
        self.message = message
        self.field = field
        super().__init__(message)


class InvalidInputError(ApiError):
    status_code = 400


class NotFoundError(ApiError):
    status_code = 404


class ConflictError(ApiError):
    status_code = 409


class AuthenticationError(ApiError):
    status_code = 401


class AuthorizationError(ApiError):
    status_code = 403


class RunFailureError(ApiError):
    error_class = "run_failure"
    status_code = 422


class ServiceUnavailableError(ApiError):
    error_class = "service_unavailable"
    status_code = 503
















_GENERIC_INTERNAL_MESSAGE = "an internal error occurred"

IDENTITY_HEADER = "X-Identity"


def authenticate(persistence: "PersistenceInterface", identity: str | None) -> PermissionLevel:
    if not identity:
        raise AuthenticationError("no identity supplied")

    user = persistence.read_user(identity)
    if user is None:
        raise AuthenticationError(f"'{identity}' is not a registered identity")

    return PermissionLevel[user["permission_level"].upper()]


def require(level: PermissionLevel, operation: RequestedOperation) -> None:
    if not is_permitted(level, operation):
        raise AuthorizationError(f"level {level.name} may not {operation.value.replace('_', ' ')}")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError):
        logger.warning(
            "API request refused",
            extra={
                "event": "api_error", "error_class": error.error_class, "status_code": error.status_code,
                "error_message": error.message, "field": error.field, "trace_id": get_trace_id(),
            },
        )
        error_payload = {"error_class": error.error_class, "message": error.message}
        if error.field is not None:
            error_payload["field"] = error.field
        response = jsonify(error_payload)
        if error.status_code == 503:
            response.headers["Retry-After"] = "1"
        return response, error.status_code

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException):
        error_payload = {"error_class": "invalid_input", "message": error.description or error.name}
        return jsonify(error_payload), error.code or 500

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):
        logger.exception(
            "unhandled exception in an API request",
            extra={"event": "api_unexpected_error", "trace_id": get_trace_id()},
        )
        return jsonify({"error_class": "internal_error", "message": _GENERIC_INTERNAL_MESSAGE}), 500
