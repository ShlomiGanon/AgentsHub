"""Deterministic current-state reads for fixed operational UI controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from history import storage_timestamp
from orchestrator.situational_picture import (
    SituationalQueryScope,
    build_typed_snapshot,
    render_typed_snapshot,
)
from persistence import OperationalScope, operational_scope_context, operational_time_context
from profiles import operational_profile_context


FIXED_STATE_PROTOCOLS = frozenset(
    {
        "report_team_availability",
        "query_camera_status",
        "query_drone_fleet_status",
        "query_historical_incidents",
        "overall_situational_picture",
    }
)


@dataclass(frozen=True)
class FixedStateRead:
    """One freshly acquired, scope-bound fixed-button result."""

    answer: str
    sources: tuple[str, ...]
    domains: tuple[str, ...]
    result_status: str
    fetched_at: str
    source_updated_at: str | None = None
    provenance: dict | None = None


def _history_answer(events: tuple[dict, ...], messages) -> str:
    visible = tuple(
        event
        for event in events
        if str(event.get("description") or event.get("raw_text") or "").strip()
    )
    if not visible:
        return messages.text("operational_button.history_empty")

    lines = [messages.text("operational_button.history_header", count=len(visible))]
    lines.extend(
        messages.text(
            "operational_button.history_line",
            text=str(event.get("description") or event.get("raw_text")).strip(),
        )
        for event in visible
    )
    return "\n".join(lines)


def read_fixed_operational_state(
    protocol_name: str,
    *,
    registry,
    history_query_service,
    messages,
    operational_scope: OperationalScope,
    scenario_time: str | None,
    sender_identity_filter: str | None,
    operational_profile=None,
) -> FixedStateRead:
    """Read the canonical current state for one known fixed control.

    This path deliberately calls no model and accepts no conversation context.
    Every invocation enters the already trusted scope before touching a store.
    """

    if protocol_name not in FIXED_STATE_PROTOCOLS:
        raise ValueError(f"unsupported fixed state protocol: {protocol_name}")

    fetched_at = storage_timestamp(datetime.now(timezone.utc))

    with operational_scope_context(operational_scope), operational_time_context(scenario_time), operational_profile_context(operational_profile):
        if protocol_name == "report_team_availability":
            agent = registry.get("team_status_agent")
            return FixedStateRead(
                answer=agent.report_team_availability(view="summary"),
                sources=("team_status_store.availability_snapshot",),
                domains=("team",),
                result_status="ok",
                fetched_at=fetched_at,
            )

        if protocol_name == "query_camera_status":
            agent = registry.get("surveillance_agent")
            return FixedStateRead(
                answer=agent.get_camera_feeds(),
                sources=("surveillance_store.list_cameras",),
                domains=("cameras",),
                result_status="ok",
                fetched_at=fetched_at,
            )

        if protocol_name == "query_drone_fleet_status":
            agent = registry.get("surveillance_agent")
            fleet = agent.get_drone_fleet_status()
            missions = agent.get_active_missions()
            return FixedStateRead(
                answer=f"{fleet}\n{missions}",
                sources=("surveillance_store.list_drones", "surveillance_store.get_active_missions"),
                domains=("drones",),
                result_status="ok",
                fetched_at=fetched_at,
            )

        if protocol_name == "query_historical_incidents":
            if history_query_service is None:
                return FixedStateRead(
                    answer=messages.text("operational_button.state_unavailable"),
                    sources=("events",),
                    domains=("history",),
                    result_status="unavailable",
                    fetched_at=fetched_at,
                )
            events = history_query_service.recent_committed_events(
                now=datetime.now(timezone.utc),
                sender_identity_filter=sender_identity_filter,
                scenario_id=operational_scope.scenario_id,
                scenario_run_id=operational_scope.scenario_run_id,
                limit=20,
            )
            updated = max((str(event.get("received_at")) for event in events if event.get("received_at")), default=None)
            return FixedStateRead(
                answer=_history_answer(events, messages),
                sources=("events",),
                domains=("history",),
                result_status="ok",
                fetched_at=fetched_at,
                source_updated_at=updated,
            )

        snapshot = build_typed_snapshot(
            registry,
            history_query_service=history_query_service,
            sender_identity_filter=sender_identity_filter,
            scenario_id=operational_scope.scenario_id,
            scenario_run_id=operational_scope.scenario_run_id,
            scenario_time=scenario_time,
            scope=SituationalQueryScope.overall_scope(),
            operational_scope=operational_scope,
        )
        if snapshot is None:
            return FixedStateRead(
                answer=messages.text("operational_button.state_unavailable"),
                sources=(),
                domains=("team", "cameras", "drones", "history"),
                result_status="unavailable",
                fetched_at=fetched_at,
            )

        provenance = snapshot.provenance()
        sections = provenance.get("sections", {})
        sources = tuple(
            f"state:{name}:{metadata['source']}"
            for name, metadata in sections.items()
        )
        sources += tuple(report["source_ref"] for report in provenance.get("recent_reports", ()))
        return FixedStateRead(
            answer=render_typed_snapshot(snapshot, include_findings=True, include_recent_reports=True),
            sources=sources,
            domains=("team", "cameras", "drones", "history"),
            result_status="ok",
            fetched_at=fetched_at,
            provenance=provenance,
        )


if TYPE_CHECKING:
    from history.query import HistoryQueryService
