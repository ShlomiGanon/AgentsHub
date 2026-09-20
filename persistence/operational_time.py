"""Canonical clock ownership for one operational world.

Two clocks exist in this system and they are never comparable with one
another:

* **Operational (business) time** — when something happened, or is scheduled
  to happen, inside the operational world. In a simulation run this is the
  trusted `scenario_time` declared by the scenario step; in LIVE it is the
  real receipt time. It owns occurrence, availability intervals, operational
  windows, attendance cycles, and operational ordering.

* **Runtime time** — the real execution time of this process. It owns
  processing deadlines, queue TTLs, hold/approval expiry, provider timeouts,
  the expiry sweeper, and startup recovery.

A value produced by one clock must never be compared against a value produced
by the other. A historical scenario timestamp must never make a newly created
runtime deadline born expired, and a real runtime timestamp must never make an
in-window operational report look late.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Mapping

from persistence.operational_scope import OperationalScope, resolve_operational_scope


class OperationalTimeError(ValueError):
    """A timestamp could not be interpreted on either operational clock."""


def runtime_now() -> datetime:
    """The real execution clock — the only clock that owns lifecycle expiry."""

    return datetime.now(timezone.utc)


def parse_operational_timestamp(value: str | datetime | None) -> datetime | None:
    """Normalize an ISO-8601 instant to aware UTC, or None when absent."""

    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise OperationalTimeError(f"invalid ISO timestamp: {value!r}") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


_CURRENT_OPERATIONAL_TIME: ContextVar[datetime | None] = ContextVar(
    "current_operational_time", default=None
)


@contextmanager
def operational_time_context(value: str | datetime | None):
    """Bind the operational clock for everything executed inside this scope.

    A boundary that releases one trusted simulation step installs that step's
    scenario time here, so specialist reads and writes that take no explicit
    instant still resolve the same business clock instead of silently falling
    back to the runtime clock.
    """

    token = _CURRENT_OPERATIONAL_TIME.set(parse_operational_timestamp(value))
    try:
        yield
    finally:
        _CURRENT_OPERATIONAL_TIME.reset(token)


def current_operational_time() -> datetime | None:
    """The ambient operational instant, when a boundary declared one."""

    return _CURRENT_OPERATIONAL_TIME.get()


def operational_now(
    *,
    scenario_time: str | datetime | None = None,
    received_at: str | datetime | None = None,
    scope: OperationalScope | None = None,
) -> datetime:
    """Resolve the operational (business) clock for the active world.

    Inside a simulation run the trusted `scenario_time` owns business meaning.
    Everywhere else — and whenever a simulation step declared no scenario time
    — the real receipt time is the business time, which is exactly LIVE
    semantics. The runtime clock is the final fallback so this never invents a
    business instant out of nothing.
    """

    resolved_scope = resolve_operational_scope(scope)

    if resolved_scope.is_simulation:
        declared = parse_operational_timestamp(scenario_time)
        if declared is not None:
            return declared

        ambient = _CURRENT_OPERATIONAL_TIME.get()
        if ambient is not None:
            return ambient

    received = parse_operational_timestamp(received_at)
    if received is not None:
        return received

    return runtime_now()


def operational_time_of_event(
    event: Mapping[str, object] | None,
    *,
    scope: OperationalScope | None = None,
) -> datetime:
    """The operational (business) instant one persisted event belongs to."""

    event = event or {}

    return operational_now(
        scenario_time=event.get("scenario_time"),  # type: ignore[arg-type]
        received_at=event.get("received_at"),  # type: ignore[arg-type]
        scope=scope,
    )


def operational_timestamp_of_event(
    event: Mapping[str, object] | None,
    *,
    scope: OperationalScope | None = None,
) -> str:
    """`operational_time_of_event` rendered as a storable ISO-8601 string."""

    return operational_time_of_event(event, scope=scope).isoformat()
