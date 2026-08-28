"""Passive API runtime context."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import Agent
    from history import SummaryScheduler
    from orchestrator.flows import FlowDeps, SerialEventQueue
    from profiles.loader import LoadedProfile


@dataclass(frozen=True)
class ApiContext:
    deps: "FlowDeps"
    main_agent: "Agent"
    insights_agent: "Agent"
    loaded_profile: "LoadedProfile"
    queue: "SerialEventQueue"
    scheduler: "SummaryScheduler"


class ApiError(Exception):
    error_class = "invalid_input"
    status_code = 400

    def __init__(self, message: str, field: str | None = None):
        self.message = message
        self.field = field
        super().__init__(message)


class InvalidInputError(ApiError):
    error_class = "invalid_input"
    status_code = 400


class NotFoundError(ApiError):
    error_class = "invalid_input"
    status_code = 404


class ConflictError(ApiError):
    error_class = "invalid_input"
    status_code = 409


class AuthenticationError(ApiError):
    error_class = "invalid_input"
    status_code = 401


class AuthorizationError(ApiError):
    error_class = "invalid_input"
    status_code = 403


class RunFailureError(ApiError):
    error_class = "run_failure"
    status_code = 422
