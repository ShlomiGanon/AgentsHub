"""Firefighting admin-panel table adapters for drones, attendance, forces, and fires."""

from profiles.admin_tables import AdminColumn, AdminTable


def _drones_list(deps) -> list:
    """List every drone row from this profile's surveillance store."""

    return deps.registry.get("surveillance_agent").surveillance_store.list_drones()


def _drones_get(deps, drone_id: str):
    """Return one drone row by id, or None if it is missing."""

    return deps.registry.get("surveillance_agent").surveillance_store.get_drone(drone_id)


def _drones_write(deps, row: dict) -> None:
    """Apply an admin edit to one drone, leaving store-side cascades intact."""

    store = deps.registry.get("surveillance_agent").surveillance_store
    store.admin_update_drone(row["drone_id"], **{k: v for k, v in row.items() if k != "drone_id"})


def _attendance_list(deps) -> list:
    """List crew-shift attendance responses from the team-status store."""

    return deps.registry.get("team_status_agent").status_store.list_responses()


def _attendance_get(deps, response_id: str):
    """Return one attendance response by id, or None if it is missing."""

    return deps.registry.get("team_status_agent").status_store.get_response(response_id)


def _attendance_write(deps, row: dict) -> None:
    """Apply an admin edit to one attendance response."""

    store = deps.registry.get("team_status_agent").status_store
    store.admin_update_attendance_fields(row["response_id"], **{k: v for k, v in row.items() if k != "response_id"})


def _forces_list(deps) -> list:
    """List mutual-aid dispatch rows from the neighboring-force store."""

    return deps.registry.get("neighboring_forces_agent").dispatch_store.list_dispatches()


def _forces_get(deps, request_id: str):
    """Return one dispatch row by request id, or None if it is missing."""

    return deps.registry.get("neighboring_forces_agent").dispatch_store.get_dispatch(request_id)


def _forces_write(deps, row: dict) -> None:
    """Apply an admin edit to one mutual-aid dispatch."""

    store = deps.registry.get("neighboring_forces_agent").dispatch_store
    store.admin_update_dispatch(row["request_id"], **{k: v for k, v in row.items() if k != "request_id"})


def _fires_list(deps) -> list:
    """List fire-registry rows from the crew-status agent's fire store."""

    return deps.registry.get("team_status_agent").fire_store.list_fires()


def _fires_get(deps, fire_id: str):
    """Return one fire row by id, or None if it is missing."""

    return deps.registry.get("team_status_agent").fire_store.get_fire(fire_id)


def _fires_write(deps, row: dict) -> None:
    """Apply an admin edit to one fire-registry row."""

    store = deps.registry.get("team_status_agent").fire_store
    store.admin_update_fire(row["fire_id"], **{k: v for k, v in row.items() if k != "fire_id"})


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
    AdminTable(
        key="fires",
        label="Fires",
        primary_key="fire_id",
        columns=(
            AdminColumn("fire_id", "Fire ID", editable=False),
            AdminColumn("area", "Area", required=True),
            AdminColumn(
                "status", "Status", kind="select",
                choices=("burning", "extinguished"), required=True,
            ),
            AdminColumn("source_event_id", "Source event ID", editable=False),
            AdminColumn("last_updated", "Last updated", editable=False),
            AdminColumn("extinguished_at", "Extinguished at", editable=False),
            AdminColumn("expiry_reason", "Expiry reason", editable=False),
        ),
        list_fn=_fires_list, get_fn=_fires_get, write_fn=_fires_write,
    ),
)

__all__ = ["ADMIN_TABLES"]

