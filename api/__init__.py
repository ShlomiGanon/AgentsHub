"""Public API facade."""

import sys

from api import http
from api.http import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    RunFailureError,
    authenticate,
    register_error_handlers,
    require,
)

auth = http
errors = http
sys.modules[f"{__name__}.auth"] = http
sys.modules[f"{__name__}.errors"] = http

from api import routes

ingestion = routes
management = routes
operations = routes
sys.modules[f"{__name__}.ingestion"] = routes
sys.modules[f"{__name__}.management"] = routes
sys.modules[f"{__name__}.operations"] = routes

from api import app
from api.app import build_app, build_context, create_app
from api.contracts import ApiContext

__all__ = [
    "ApiContext",
    "ApiError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "InvalidInputError",
    "NotFoundError",
    "RunFailureError",
    "authenticate",
    "build_app",
    "build_context",
    "create_app",
    "require",
]
