"""Canonical trusted runtime context resolution."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from persistence.operational_scope import OperationalScope
from persistence.operational_unit_store import LiveOperationalContext, OperationalUnit


@dataclass(frozen=True)
class RuntimeOperationalContext:
    """The trusted identity and operational world for one request."""

    identity: dict | None
    membership: dict | None
    operational_unit: OperationalUnit | None
    operational_profile: object | None
    operational_scope: OperationalScope | None
    role: str | None
    permission_level: str | None
    scenario_id: str | None
    scenario_run_id: str | None
    provenance: str
    status: str

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"


_CURRENT_CONTEXT: ContextVar[RuntimeOperationalContext | None] = ContextVar(
    "current_runtime_operational_context", default=None
)


def current_runtime_context() -> RuntimeOperationalContext | None:
    return _CURRENT_CONTEXT.get()


@contextmanager
def runtime_context(context: RuntimeOperationalContext):
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


def _unresolved(
    *, identity: dict | None, scope: OperationalScope | None, status: str, provenance: str
) -> RuntimeOperationalContext:
    return RuntimeOperationalContext(
        identity=identity,
        membership=None,
        operational_unit=None,
        operational_profile=None,
        operational_scope=scope,
        role=None,
        permission_level=(str(identity.get("permission_level")) if identity else None),
        scenario_id=getattr(scope, "scenario_id", None),
        scenario_run_id=getattr(scope, "scenario_run_id", None),
        provenance=provenance,
        status=status,
    )


def resolve_runtime_context(
    *,
    identity_id: str,
    users_persistence,
    unit_store=None,
    loaded_profile=None,
    simulation_context=None,
) -> RuntimeOperationalContext:
    """Resolve one trusted context; never silently turns missing data into LIVE."""

    identity = users_persistence.read_user(str(identity_id)) if users_persistence is not None else None
    if identity is None:
        return _unresolved(identity=None, scope=None, status="unknown_user", provenance="users")

    if simulation_context is not None:
        scenario_id = str(getattr(simulation_context, "scenario_id", "") or "")
        run_id = str(getattr(simulation_context, "scenario_run_id", "") or "")
        if not scenario_id or not run_id:
            return _unresolved(identity=identity, scope=None, status="invalid_simulation_context", provenance="simulator")
        scope = OperationalScope.simulation(scenario_id, run_id)
        from profiles import profile_for_scope

        profile = profile_for_scope(loaded_profile, scope) if loaded_profile is not None else None
        return RuntimeOperationalContext(
            identity=identity,
            membership=None,
            operational_unit=None,
            operational_profile=profile,
            operational_scope=scope,
            role=None,
            permission_level=str(identity.get("permission_level") or ""),
            scenario_id=scenario_id,
            scenario_run_id=run_id,
            provenance="trusted_simulation_context",
            status="resolved" if profile is not None else "missing_profile",
        )

    scope = OperationalScope.live()
    if unit_store is None:
        # Narrow compatibility mode for isolated legacy test doubles and
        # single-purpose deployments that have no Membership store.  The
        # production unified runtime always wires an OperationalUnit store.
        from profiles import profile_for_scope

        profile = profile_for_scope(loaded_profile, scope) if loaded_profile is not None else None
        return RuntimeOperationalContext(
            identity=identity,
            membership=None,
            operational_unit=None,
            operational_profile=profile,
            operational_scope=scope,
            role=None,
            permission_level=str(identity.get("permission_level") or ""),
            scenario_id=None,
            scenario_run_id=None,
            provenance="legacy_compatibility_no_unit_store",
            status="resolved" if profile is not None else "missing_operational_unit_store",
        )

    live: LiveOperationalContext = unit_store.resolve_live(str(identity_id))
    if live.status != "resolved":
        return _unresolved(identity=identity, scope=scope, status=live.status, provenance="live_membership")

    from profiles import operational_profile

    profile = operational_profile(live.profile_id)
    return RuntimeOperationalContext(
        identity=identity,
        membership=live.membership,
        operational_unit=live.unit,
        operational_profile=profile,
        operational_scope=scope,
        role=(str(live.membership.get("role")) if live.membership else None),
        permission_level=str(identity.get("permission_level") or ""),
        scenario_id=None,
        scenario_run_id=None,
        provenance="live_membership",
        status="resolved",
    )


__all__ = ["RuntimeOperationalContext", "current_runtime_context", "resolve_runtime_context", "runtime_context"]
