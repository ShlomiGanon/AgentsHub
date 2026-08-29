"""Public API facade."""

import importlib
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

_APP_EXPORTS = frozenset({"ApiContext", "build_app", "build_context", "create_app"})


def __getattr__(name: str):
    """Load API application wiring only when a facade export is requested.

    Keeping ``api.app`` out of package initialization is required for the
    supported ``python -m api.app`` entry point: runpy must be the first code
    to load that module.
    """

    if name not in _APP_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    app_module = importlib.import_module("api.app")
    value = getattr(app_module, name)
    globals()[name] = value
    return value


class _ContractsModule(ModuleType):
    def __getattr__(self, name: str):
        if name != "ApiContext":
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")
        value = __getattr__(name)
        setattr(self, name, value)
        return value


contracts = _ContractsModule(f"{__name__}.contracts")
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
contracts.__all__ = ["ApiContext", *(error_type.__name__ for error_type in (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    RunFailureError,
))]
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
