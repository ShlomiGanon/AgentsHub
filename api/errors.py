"""The error contract (work_plan.md §7.10).

One shape for every failure, on every endpoint: a class, a human-readable
message, and — where relevant — the field or protocol at fault. Never an
internal exception, a stack trace, or an engine-specific error.

`ApiError` and its subclasses are the *only* sanctioned way a route
raises an HTTP-visible failure; nothing in `api/` builds a Flask response
by hand for an error case. `register_error_handlers` is the one place
that turns any of them (or anything unexpected) into that one shape.
"""

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    """Base for every API error. Subclasses fix `error_class` and
    `status_code`; `message` and `field` are supplied per raise.
    """

    error_class = "invalid_input"
    status_code = 400

    def __init__(self, message: str, field: str | None = None):
        self.message = message
        self.field = field
        super().__init__(message)


class InvalidInputError(ApiError):
    """Bad payload, unknown field, a value that fails validation, an
    unregistered identity, an authorization refusal — §7.10's
    `"invalid_input"` class.
    """

    error_class = "invalid_input"
    status_code = 400


class NotFoundError(ApiError):
    """A named resource (a job, a hold) does not exist. Still the
    `"invalid_input"` class per §7.10's three-class list — only the HTTP
    status differs.
    """

    error_class = "invalid_input"
    status_code = 404


class ConflictError(ApiError):
    """An answer to a hold that was already resolved — §7.11's own
    "naming who resolved it and when" case.
    """

    error_class = "invalid_input"
    status_code = 409


class AuthenticationError(ApiError):
    """No identity, or an identity not in the user table (§7.9 — never
    treated as a viewer).
    """

    error_class = "invalid_input"
    status_code = 401


class AuthorizationError(ApiError):
    """A registered identity whose level doesn't permit the action."""

    error_class = "invalid_input"
    status_code = 403


class RunFailureError(ApiError):
    """The Main Agent couldn't produce a usable response somewhere with
    no job to report it against (e.g. intent classification or
    question-answer routing failing synchronously inside `POST /Msg`) —
    §7.10's `"run_failure"` class. A protocol run that exhausts its
    retries is *not* this — that's a normal `GET /Job/<event_id>`
    response reporting `status: "failed"`, `200 OK`.
    """

    error_class = "run_failure"
    status_code = 422


class InternalError(ApiError):
    """Anything unexpected. `message` is always the fixed string below —
    never the real exception's text, never a stack trace.
    """

    error_class = "internal_error"
    status_code = 500


_GENERIC_INTERNAL_MESSAGE = "an internal error occurred"


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError):
        body = {"error_class": error.error_class, "message": error.message}
        if error.field is not None:
            body["field"] = error.field
        return jsonify(body), error.status_code

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException):
        # Routing-level failures Flask/Werkzeug raise before any api/
        # route body runs (an unmapped path, a wrong method) — reported
        # in the same shape as everything else, never Werkzeug's own
        # HTML error page.
        body = {"error_class": "invalid_input", "message": error.description or error.name}
        return jsonify(body), error.code or 500

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):
        app.logger.exception("unhandled exception in an API request")
        return jsonify({"error_class": "internal_error", "message": _GENERIC_INTERNAL_MESSAGE}), 500
