"""Neighboring/external-force dispatch-log specialist -- shared, reusable infrastructure
(docs/Admin_Tables_Plan.md sections 3/3.3), extracted from `profiles/response_team.py`'s
original profile-only `NeighboringForcesAgent` so a second profile (`profiles/firefighting.py`)
can get the exact same persisted dispatch-log + computed-remaining-capacity mechanism, not just
an in-memory stand-in.

A subclass supplies its own class-level `dispatch_db_path`, `force_bases` (kind -> home area),
`force_pool_size`, and `force_busy_seconds`, and gets `dispatch_neighboring_force`/
`list_neighboring_force_dispatches` for free. `_resolve_kind`/`_check_capacity` are the two
override points: `response_team.py`'s own subclass extends both to add its own roster
("squad") as a dispatchable kind that isn't a real external force at all and is checked against
live roster availability instead of the busy-window pool -- every other subclass (e.g.
firefighting's) uses the defaults below unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from agents.runtime import Agent, tool
from agents.contracts import failed_tool_result
from persistence import open_neighboring_force_store


class NeighboringForcesAgent(Agent):
    name = "neighboring_forces_agent"
    role = (
        "Records requests to dispatch a neighboring/external force into one of this site's "
        "areas, and answers read-only questions about the current dispatch log. A dispatch's "
        "status advances from en_route to arrived automatically once its computed ETA has "
        "elapsed -- never from a human report."
    )
    system_prompt = (
        "You are the neighboring-forces dispatch specialist. You have two tools: "
        "dispatch_neighboring_force records a dispatch request for one force kind to a named "
        "target area, with the unit count and any note given; list_neighboring_force_dispatches "
        "returns the current dispatch log, optionally filtered by status ('en_route' or "
        "'arrived'). Neither tool contacts a real response unit -- each only logs the request "
        "and its computed ETA. Each force kind has a limited number of units currently "
        "available; if a dispatch fails for that reason, state that plainly and do not retry. "
        "Report back plainly what was recorded; never claim a dispatched force has arrived on "
        "scene yourself -- that transition is computed automatically from elapsed time, not "
        "something you report."
    )

    dispatch_db_path = ""
    force_bases: dict[str, str] = {}
    force_pool_size: int = 2
    force_busy_seconds: int = 2 * 60 * 60
    # A plain 2-arg function (origin_area, target_area) -> seconds. Assign it wrapped in
    # `staticmethod(...)` on a subclass, exactly like a module-level function assigned as a
    # class attribute anywhere else in this codebase (e.g. ResponseTeamSurveillanceStore's own
    # `eta_fn` parameter is passed the same underlying function) -- otherwise Python's normal
    # attribute-lookup binds it as a method and silently passes `self` as its first argument.
    eta_fn: "Callable[[str, str], int] | None" = None

    def __init__(self, model: str, api_key: str | None = None):
        if not self.dispatch_db_path:
            raise TypeError("NeighboringForcesAgent requires a class-level dispatch_db_path")
        self.dispatch_store = open_neighboring_force_store(self.dispatch_db_path)
        super().__init__(model, api_key)

    def _eta_seconds(self, origin_area: str, target_area: str) -> int:
        if origin_area == target_area:
            return 45
        if self.eta_fn is not None:
            return self.eta_fn(origin_area, target_area)
        return 180

    def _resolve_kind(self, kind_norm: str) -> "tuple[str, str] | None":
        """(origin_area, resource_kind_for_signal) for a known force kind, or None if
        `kind_norm` isn't one this agent knows about. `resource_kind_for_signal` is normally
        just `kind_norm` itself (the default here), but a subclass with an additional non-force
        kind that should signal resource-unavailability under a *different* name than its own
        dispatch `kind` (e.g. response_team's own roster, dispatched as `kind="squad"` but
        signaled as `"squad_member"` to match its resource-label catalog) overrides this to
        return that name instead -- and must also override `_check_capacity` for that kind, the
        two always change together."""

        origin_area = self.force_bases.get(kind_norm)
        if origin_area is None:
            return None
        return origin_area, kind_norm

    def _check_capacity(self, kind_norm: str, unit_count: int) -> "tuple[bool, int]":
        """(ok, remaining) -- `remaining` is the number of currently-available units for this
        kind, regardless of `ok`, so a caller can report exactly how short the request was.
        Default: the busy-window pool check -- a dispatched unit stays busy for
        `force_busy_seconds` regardless of en_route/arrived status, checked against
        `force_pool_size`. A subclass whose capacity for a kind comes from somewhere else
        entirely (e.g. a live roster count) overrides this for that kind."""

        busy_since = (datetime.now(timezone.utc) - timedelta(seconds=self.force_busy_seconds)).isoformat()
        busy_units = sum(
            dispatch["unit_count"] for dispatch in self.dispatch_store.list_dispatches()
            if dispatch["force_kind"] == kind_norm and dispatch["dispatched_at"] > busy_since
        )
        remaining = max(self.force_pool_size - busy_units, 0)
        return remaining >= unit_count, remaining

    def _capacity_shortage_text(self, kind_norm: str, remaining: int, unit_count: int) -> str:
        """Plain-English shortage wording by default -- a subclass with its own localized
        catalog text (e.g. response_team.py's Hebrew) overrides this entirely."""

        return f"only {remaining} of {self.force_pool_size} {kind_norm} unit(s) currently available, {unit_count} requested"

    def _valid_kinds(self) -> "tuple[str, ...]":
        return tuple(sorted(self.force_bases))

    @tool(
        "dispatch_neighboring_force",
        "Records a request to dispatch a neighboring/external force to a named target area, "
        "with the unit count and an optional note. Returns the recorded request, its en_route "
        "status, and computed ETA -- or a clear statement that too few units are currently "
        "available. Side-effecting and not idempotent -- running it twice records two dispatch "
        "requests, not one.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_neighboring_force(self, kind: str, target_area: str, unit_count: int = 1, note: str = "") -> str:
        kind_norm = kind.strip().lower()
        resolved = self._resolve_kind(kind_norm)
        if resolved is None:
            return failed_tool_result(
                f"Clarification required: unknown force kind '{kind}'. "
                f"Valid kinds: {', '.join(self._valid_kinds())}."
            )
        if not target_area.strip():
            return failed_tool_result("Clarification required: target_area is required.")
        if unit_count < 1:
            return failed_tool_result("Clarification required: unit_count must be at least 1.")

        origin_area, signal_kind = resolved
        cleaned_area = target_area.strip()
        ok, remaining = self._check_capacity(kind_norm, unit_count)
        if not ok:
            reason = self._capacity_shortage_text(kind_norm, remaining, unit_count)
            self.signal_resource_unavailable(signal_kind, cleaned_area, reason)
            return f"{kind_norm} dispatch failed: {reason}"

        eta = self._eta_seconds(origin_area, cleaned_area)
        record = self.dispatch_store.dispatch(
            force_kind=kind_norm,
            origin_area=origin_area,
            target_area=cleaned_area,
            unit_count=unit_count,
            eta_seconds=eta,
            note=note.strip(),
        )
        return (
            f"{kind_norm} dispatch recorded, en route to {record['target_area']}, "
            f"ETA={record['eta_seconds']}s (request {record['request_id']})."
        )

    @tool(
        "list_neighboring_force_dispatches",
        "Returns the current neighboring-force dispatch log (request id, kind, unit count, "
        "origin/target area, status, ETA, dispatched-at), optionally filtered to one status "
        "('en_route' or 'arrived'). A dispatch already shows 'arrived' once its ETA has elapsed, "
        "with no separate report needed for that transition.",
        side_effecting=False,
    )
    def list_neighboring_force_dispatches(self, status: str = "") -> str:
        cleaned = status.strip().lower()
        rows = self.dispatch_store.list_dispatches(status=cleaned or None)
        if not rows:
            return "No neighboring-force dispatches recorded."
        lines = [f"Neighboring-force dispatches ({len(rows)}):"]
        for row in rows:
            lines.append(
                f"- [{row['request_id']}] {row['force_kind']} x{row['unit_count']}: "
                f"{row['origin_area']} -> {row['target_area']} ({row['status'].upper()}, "
                f"ETA {row['eta_seconds']}s, dispatched {row['dispatched_at']})"
            )
        return "\n".join(lines)
