"""Authentication, authorization, and HTTP error translation."""

import hmac
import logging
import os
from typing import TYPE_CHECKING

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from auth.permissions import PermissionLevel, RequestedOperation, is_permitted
from messages import get_current_catalog
from tools import get_trace_id, record_telegram_security_metric

if TYPE_CHECKING:
    from persistence import PersistenceInterface

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """HTTP-facing API error with a stable error_class, status, and optional field."""

    error_class = "invalid_input"
    status_code = 400

    def __init__(self, message: str, field: str | None = None, details: dict | None = None):
        """Store the public message plus optional field name and extra details."""

        self.message = message
        self.field = field
        self.details = dict(details or {})
        super().__init__(message)


class InvalidInputError(ApiError):
    """400: the request body or query is malformed or missing a required field."""

    status_code = 400


class NotFoundError(ApiError):
    """404: the named resource does not exist."""

    status_code = 404


class ConflictError(ApiError):
    """409: the resource exists but is in a conflicting state."""

    status_code = 409


class AuthenticationError(ApiError):
    """401: the caller did not present a registered identity."""

    status_code = 401


class AuthorizationError(ApiError):
    """403: the caller is known but not allowed to perform this operation."""

    status_code = 403


class RunFailureError(ApiError):
    """422: orchestration could not produce a usable result for this run."""

    error_class = "run_failure"
    status_code = 422


class ServiceUnavailableError(ApiError):
    """503: the queue or another required service cannot accept work now."""

    error_class = "service_unavailable"
    status_code = 503
















IDENTITY_HEADER = "X-Identity"
SERVICE_KEY_HEADER = "X-Service-Key"

# Duplicated rather than imported from bot.contracts.BOT_SERVICE_IDENTITY: api may not import
# bot (tests/test_architecture.py enforces the package boundary — bot calls api over HTTP, not
# api importing bot's Python code), the same reason IDENTITY_HEADER's "X-Identity" string is
# already independently duplicated on the bot side rather than shared. Keep this in sync with
# bot.contracts.BOT_SERVICE_IDENTITY and BOT_SERVICE_KEY_ENV_VAR if either ever changes.
BOT_SERVICE_IDENTITY = "bot-service"
BOT_SERVICE_KEY_ENV_VAR = "BOT_SERVICE_KEY"
SYSTEM_ADMIN_IDENTITY = "Admin"
SYSTEM_ADMIN_FULL_NAME = "Admin"


def secrets_equal(provided: str | None, expected: str | None) -> bool:
    """Constant-time UTF-8 compare. Empty or missing values never match.

    Compare as bytes, not str: hmac.compare_digest raises TypeError on a non-ASCII str
    (a malformed/garbage header would then 500 instead of the intended 401/reject).
    """

    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _bot_service_key_matches(provided: str | None) -> bool:
    """Whether the presented service key matches BOT_SERVICE_KEY."""

    return secrets_equal(provided, os.environ.get(BOT_SERVICE_KEY_ENV_VAR))


def is_authenticated_bot_request() -> bool:
    """Whether this request carries the bot's service proof, regardless of the human X-Identity."""

    return _bot_service_key_matches(request.headers.get(SERVICE_KEY_HEADER))


def enforce_safe_mode_for_bot_request(persistence, settings_store) -> None:
    """Block auto-registered Telegram entities immediately when safe mode is active.

    Generic API clients are intentionally unaffected by this source-specific gate;
    they still pass through the normal registered-identity authentication policy.
    """

    if not is_authenticated_bot_request() or not settings_store.get_safe_mode():
        return
    identity = request.headers.get(IDENTITY_HEADER)
    if not identity or identity == BOT_SERVICE_IDENTITY:
        return
    user = persistence.read_user(identity)
    if user is None:
        raise AuthenticationError(get_current_catalog().text("api.identity_unregistered", identity=identity))
    if bool(user.get("auto_register", False)):
        record_telegram_security_metric("blocked", "user")
        raise AuthorizationError(get_current_catalog().text("auth.safe_mode_blocked"))

    chat_type = request.headers.get("X-Telegram-Chat-Type")
    chat_id = request.headers.get("X-Telegram-Chat-ID")
    if chat_type in {"group", "supergroup"}:
        group = persistence.read_group(chat_id or "")
        if group is None or bool(group.get("auto_register", False)):
            record_telegram_security_metric("blocked", "group")
            raise AuthorizationError(get_current_catalog().text("auth.safe_mode_group_blocked"))


def authenticate(persistence: "PersistenceInterface", identity: str | None) -> PermissionLevel:
    """Resolve X-Identity to a permission level, or raise AuthenticationError."""

    if not identity:
        raise AuthenticationError(get_current_catalog().text("api.identity_required"))

    # BOT_SERVICE_IDENTITY ("bot-service") is a fixed, public string — visible in source,
    # docs, and this very error message — not a secret an unregistered-identity check alone
    # can protect. A caller claiming it must also present the matching X-Service-Key. A
    # missing/wrong key is rejected with the exact same message as a genuinely unregistered
    # identity (below), so a caller can't tell "bot-service isn't registered" apart from
    # "bot-service is registered but you don't have its key."
    if identity == BOT_SERVICE_IDENTITY and not _bot_service_key_matches(request.headers.get(SERVICE_KEY_HEADER)):
        raise AuthenticationError(
            get_current_catalog().text("api.identity_unregistered", identity=identity)
        )

    user = persistence.read_user(identity)
    if user is None:
        raise AuthenticationError(
            get_current_catalog().text("api.identity_unregistered", identity=identity)
        )

    return PermissionLevel[user["permission_level"].upper()]


def require(level: PermissionLevel, operation: RequestedOperation) -> None:
    """Raise AuthorizationError when this permission level may not perform the operation."""

    if not is_permitted(level, operation):
        raise AuthorizationError(
            get_current_catalog().text(
                "api.operation_forbidden",
                level=level.name,
                operation=operation.value.replace("_", " "),
            )
        )


def register_error_handlers(app: Flask) -> None:
    """Translate ApiError, HTTPException, and unexpected exceptions into JSON responses."""

    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError):
        """Return the public JSON body for a known API error."""
        logger.warning(
            "API request refused",
            extra={
                "event": "api_error", "error_class": error.error_class, "status_code": error.status_code,
                "error_message": error.message, "field": error.field, "trace_id": get_trace_id(),
            },
        )
        error_payload = {
            "error_class": error.error_class,
            "error_code": error.error_class,
            "message": error.message,
        }
        if error.field is not None:
            error_payload["field"] = error.field
        error_payload.update(error.details)
        response = jsonify(error_payload)
        if error.status_code == 503:
            response.headers["Retry-After"] = "1"
        return response, error.status_code

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException):
        """Return JSON for a Flask/Werkzeug HTTP error."""
        error_payload = {
            "error_class": "invalid_input",
            "error_code": "invalid_input",
            "message": error.description or error.name,
        }
        return jsonify(error_payload), error.code or 500

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):
        """Log an unexpected exception and return a generic 500 JSON body."""
        logger.exception(
            "unhandled exception in an API request",
            extra={"event": "api_unexpected_error", "trace_id": get_trace_id()},
        )
        return jsonify(
            {
                "error_class": "internal_error",
                "error_code": "internal_error",
                "message": get_current_catalog().text("api.internal_error"),
            }
        ), 500
