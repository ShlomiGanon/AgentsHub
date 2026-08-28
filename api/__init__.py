"""Public API facade."""

import sys
from types import ModuleType

from api import request_boundary
from api.request_boundary import (
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

auth = request_boundary
errors = request_boundary
http = request_boundary
sys.modules[f"{__name__}.auth"] = request_boundary
sys.modules[f"{__name__}.errors"] = request_boundary
sys.modules[f"{__name__}.http"] = request_boundary

from api import routes

ingestion = routes
management = routes
operations = routes
sys.modules[f"{__name__}.ingestion"] = routes
sys.modules[f"{__name__}.management"] = routes
sys.modules[f"{__name__}.operations"] = routes

from api import app
from api.app import ApiContext, build_app, build_context, create_app

contracts = ModuleType(f"{__name__}.contracts")
contracts.ApiContext = ApiContext
for error_type in (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    RunFailureError,
):
    setattr(contracts, error_type.__name__, error_type)
sys.modules[contracts.__name__] = contracts

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
