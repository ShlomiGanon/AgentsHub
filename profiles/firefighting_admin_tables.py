"""Firefighting admin-table adapters."""

from datetime import datetime, timezone
from pathlib import Path

from agents import Agent, InvocationPolicy, NeighboringForcesAgent, SurveillanceAgent, TeamStatusAgent, failed_tool_result, get_authenticated_request_identity, tool
from messages import get_catalog
from persistence import (
    ApparatusStoreError,
    open_apparatus_store,
    open_incident_responder_store,
    open_response_team_surveillance_store,
    open_team_status_persistence,
)
from profiles.admin_tables import AdminColumn, AdminTable
from profiles.contracts import AgentSpec, OptimizationPolicy
from profiles.simulation import SimulationGroup, SimulationPersona, SimulationRoster, SimulationScenario
from protocols import CriticalityLevel, Protocol, Step

import profiles.firefighting as _facade
globals().update({name: getattr(_facade, name) for name in dir(_facade) if not name.startswith("__")})

def _drones_list(deps) -> list:
    return deps.registry.get("surveillance_agent").surveillance_store.list_drones()


def _drones_get(deps, drone_id: str):
    return deps.registry.get("surveillance_agent").surveillance_store.get_drone(drone_id)


def _drones_write(deps, row: dict) -> None:
    store = deps.registry.get("surveillance_agent").surveillance_store
    store.admin_update_drone(row["drone_id"], **{k: v for k, v in row.items() if k != "drone_id"})


def _attendance_list(deps) -> list:
    return deps.registry.get("team_status_agent").status_store.list_responses()


def _attendance_get(deps, response_id: str):
    return deps.registry.get("team_status_agent").status_store.get_response(response_id)


def _attendance_write(deps, row: dict) -> None:
    store = deps.registry.get("team_status_agent").status_store
    store.admin_update_attendance_fields(row["response_id"], **{k: v for k, v in row.items() if k != "response_id"})


def _forces_list(deps) -> list:
    return deps.registry.get("neighboring_forces_agent").dispatch_store.list_dispatches()


def _forces_get(deps, request_id: str):
    return deps.registry.get("neighboring_forces_agent").dispatch_store.get_dispatch(request_id)


def _forces_write(deps, row: dict) -> None:
    store = deps.registry.get("neighboring_forces_agent").dispatch_store
    store.admin_update_dispatch(row["request_id"], **{k: v for k, v in row.items() if k != "request_id"})


ADMIN_TABLES = (
    AdminTable(
        key="drones",
        label="Drones",
        primary_key="drone_id",
        columns=(
            AdminColumn("drone_id", "Drone ID", editable=False),
            AdminColumn("callsign", "Callsign", required=True),
            AdminColumn("model", "Model"),
            AdminColumn(
                "status", "Status", kind="select",
                choices=("ready", "in_flight", "charging", "maintenance"), required=True,
            ),
            AdminColumn("battery_percent", "Battery %", kind="number"),
            AdminColumn("current_area", "Current area"),
            AdminColumn("assigned_mission_id", "Assigned mission ID"),
            AdminColumn("last_updated", "Last updated", editable=False),
        ),
        list_fn=_drones_list, get_fn=_drones_get, write_fn=_drones_write,
    ),
    AdminTable(
        key="attendance",
        label="Crew Shift Attendance",
        primary_key="response_id",
        columns=(
            AdminColumn("response_id", "Response ID", editable=False),
            AdminColumn("telegram_identity", "Member", editable=False),
            AdminColumn("availability", "Availability", kind="select", choices=("available", "unavailable")),
            AdminColumn("reason", "Reason"),
            AdminColumn("unavailable_until", "Unavailable until"),
            AdminColumn(
                "approval_status", "Approval status", kind="select",
                choices=("accepted", "pending", "rejected"),
            ),
            AdminColumn("reviewed_by", "Reviewed by", editable=False),
            AdminColumn("reviewed_at", "Reviewed at", editable=False),
            AdminColumn("original_text", "Original text", editable=False),
            AdminColumn("received_at", "Received at", editable=False),
        ),
        list_fn=_attendance_list, get_fn=_attendance_get, write_fn=_attendance_write,
    ),
    AdminTable(
        key="forces",
        label="Friendly Forces",
        primary_key="request_id",
        columns=(
            AdminColumn("request_id", "Request ID", editable=False),
            AdminColumn("force_kind", "Force kind", required=True),
            AdminColumn("unit_count", "Unit count", kind="number", required=True),
            AdminColumn("origin_area", "Origin area"),
            AdminColumn("target_area", "Target area"),
            AdminColumn("status", "Status", kind="select", choices=("en_route", "arrived")),
            AdminColumn("dispatched_at", "Dispatched at", editable=False),
            AdminColumn("eta_seconds", "ETA (seconds)", kind="number"),
            AdminColumn("arrived_at", "Arrived at"),
            AdminColumn("note", "Note"),
            AdminColumn("event_id", "Event ID", editable=False),
        ),
        list_fn=_forces_list, get_fn=_forces_get, write_fn=_forces_write,
    ),
)

