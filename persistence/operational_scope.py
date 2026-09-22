"""Canonical operational-world identity for current authoritative state."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping


ScopeKind = Literal["LIVE", "SIMULATION_RUN"]


@dataclass(frozen=True)
class OperationalScope:
    kind: ScopeKind
    scenario_id: str | None = None
    scenario_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "LIVE":
            if self.scenario_id is not None or self.scenario_run_id is not None:
                raise ValueError("LIVE scope cannot carry simulation identity")
            return
        if self.kind != "SIMULATION_RUN":
            raise ValueError("operational scope kind must be LIVE or SIMULATION_RUN")
        if not self.scenario_id or not self.scenario_run_id:
            raise ValueError("simulation scope requires scenario_id and scenario_run_id")

    @classmethod
    def live(cls) -> "OperationalScope":
        return cls("LIVE")

    @classmethod
    def simulation(cls, scenario_id: str, scenario_run_id: str) -> "OperationalScope":
        return cls("SIMULATION_RUN", str(scenario_id), str(scenario_run_id))

    @property
    def key(self) -> str:
        if self.kind == "LIVE":
            return "LIVE"
        return f"SIMULATION_RUN:{self.scenario_id}:{self.scenario_run_id}"

    @property
    def is_simulation(self) -> bool:
        return self.kind == "SIMULATION_RUN"

    def metadata(self) -> dict[str, str | None]:
        return {
            "scope_key": self.key,
            "scope_kind": self.kind,
            "scenario_id": self.scenario_id,
            "scenario_run_id": self.scenario_run_id,
        }


_CURRENT_SCOPE: ContextVar[OperationalScope] = ContextVar(
    "current_operational_scope", default=OperationalScope.live()
)


def current_operational_scope() -> OperationalScope:
    return _CURRENT_SCOPE.get()


def resolve_operational_scope(scope: OperationalScope | None = None) -> OperationalScope:
    return scope if scope is not None else current_operational_scope()


def scope_from_event(event: Mapping[str, object] | None) -> OperationalScope:
    event = event or {}
    scenario_id = event.get("scenario_id")
    scenario_run_id = event.get("scenario_run_id")
    # Scenario identity is only a simulation scope when the trusted pair is
    # complete.  Historical/direct callers may carry a scenario label without
    # a run identity; that metadata must not manufacture an invalid scope or
    # silently select a different operational world.
    if scenario_id and scenario_run_id:
        return OperationalScope.simulation(str(scenario_id or ""), str(scenario_run_id or ""))
    return current_operational_scope()


def scope_from_simulation_context(context: object | None) -> OperationalScope:
    scenario_id = getattr(context, "scenario_id", None)
    scenario_run_id = getattr(context, "scenario_run_id", None)
    if scenario_id and scenario_run_id:
        return OperationalScope.simulation(str(scenario_id), str(scenario_run_id))
    return current_operational_scope()


def scoped_conversation_id(conversation_id: str, scope: OperationalScope | None = None) -> str:
    """Return the canonical conversation identity for a trusted operational scope.

    LIVE keeps the transport identity for backward compatibility. Simulation
    identities include both the declared scenario and run, so a reused chat id
    cannot address another run's event or conversation state.
    """

    if not conversation_id:
        return conversation_id

    resolved = resolve_operational_scope(scope)
    if resolved.kind == "LIVE":
        return conversation_id
    return f"{conversation_id}::scope={resolved.scenario_id}::run={resolved.scenario_run_id}"


@contextmanager
def operational_scope_context(scope: OperationalScope):
    token = _CURRENT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CURRENT_SCOPE.reset(token)


def scope_created_at() -> str:
    return datetime.now(timezone.utc).isoformat()
