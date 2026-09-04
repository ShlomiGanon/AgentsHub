"""Visual surveillance, camera monitoring, and aerial drone tactical specialist."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.runtime import Agent, tool
from persistence import (
    SurveillancePersistenceError,
    open_surveillance_persistence,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SurveillanceAgent(Agent):
    """Specialist agent responsible for cameras, drone fleet operations, and tactical aerial dispatch."""

    name = "surveillance_agent"
    role = (
        "Maintains real-time visual situational awareness across all sectors. Monitors security "
        "cameras, reports visual feeds, checks drone fleet availability, dispatches tactical drones "
        "to incident locations, and tracks active airborne missions."
    )
    system_prompt = (
        "You are the tactical visual surveillance and drone operations specialist. "
        "Keep all responses strictly concise, direct, and operational (BLUF - Bottom Line Up Front). "
        "The drone statuses in the fleet are: 'ready' (available for immediate dispatch), 'in_flight' (airborne on mission), 'charging', and 'maintenance'. "
        "When asked for drone fleet status or availability, call get_drone_fleet_status with an empty status_filter to see the full fleet and available ready units. "
        "When asked about cameras: state general status, then list only relevant cameras in compact single-line bullets. "
        "If asked about a specific camera or area, report ONLY on that camera or area. "
        "When asked about drones or dispatch: give only essential tactical facts (Callsign, Status, Battery, Location/Target, ETA). "
        "Highlight anomalies or security events first."
    )

    surveillance_db_path = ""

    def __init__(self, model: str, api_key: str | None = None):
        if not self.surveillance_db_path:
            raise TypeError("SurveillanceAgent requires a class-level surveillance_db_path")
        self.surveillance_store = open_surveillance_persistence(self.surveillance_db_path)
        super().__init__(model, api_key)

    @tool(
        "get_camera_feeds",
        "Returns current visual feed descriptions, azimuth, and status for security cameras, optionally filtered by area or specific camera ID.",
        side_effecting=False,
    )
    def get_camera_feeds(self, area: str = "", camera_id: str = "") -> str:
        if camera_id.strip():
            camera = self.surveillance_store.get_camera(camera_id.strip())
            if not camera:
                return f"Camera '{camera_id}' was not found in the surveillance registry."
            cameras = [camera]
        else:
            cameras = self.surveillance_store.list_cameras(area=area.strip() or None)

        if not cameras:
            scope = f"in area '{area}'" if area.strip() else "in the surveillance registry"
            return f"No cameras found {scope}."

        lines = [f"Camera feeds ({len(cameras)} cameras):"]
        for c in cameras:
            lines.append(
                f"- [{c['camera_id']}] {c['name']} ({c['area']}, {c['azimuth_degrees']}°): {c['feed_summary']} [{c['status'].upper()}]"
            )
        return "\n".join(lines)

    @tool(
        "get_drone_fleet_status",
        "Returns current operational status, battery levels, locations, and mission assignments for the tactical drone fleet. Leave status_filter empty to return all drones. Valid status filters: 'ready' (available for dispatch), 'in_flight', 'charging', 'maintenance'.",
        side_effecting=False,
    )
    def get_drone_fleet_status(self, status_filter: str = "") -> str:
        cleaned = status_filter.strip().lower()
        if cleaned in {"all", "*"}:
            cleaned = ""
        drones = self.surveillance_store.list_drones(status=cleaned or None)
        if not drones:
            # Fallback to all drones so a restrictive or unexpected filter never hides the fleet
            all_drones = self.surveillance_store.list_drones()
            if all_drones:
                drones = all_drones
            else:
                return "No drones found in fleet."

        lines = [f"Drone Fleet Status ({len(drones)} drones):"]
        counts = {"ready": 0, "in_flight": 0, "charging": 0, "maintenance": 0}
        for d in drones:
            st = d["status"]
            counts[st] = counts.get(st, 0) + 1
            mission_info = f", Mission: {d['assigned_mission_id']}" if d["assigned_mission_id"] else ""
            lines.append(
                f"- [{d['drone_id']}] {d['callsign']} ({d['model']}): {st.upper()} | Batt: {d['battery_percent']}% | Loc: {d['current_area']}{mission_info}"
            )

        lines.append(
            f"Fleet Summary: {counts.get('ready', 0)} Ready | {counts.get('in_flight', 0)} In-Flight | {counts.get('charging', 0)} Charging"
        )
        return "\n".join(lines)

    @tool(
        "dispatch_drone_to_area",
        "Dispatches an available tactical drone to an incident area for visual coverage/recon, calculating ETA and tracking mission status.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_drone_to_area(
        self,
        target_area: str,
        incident_description: str,
        mission_type: str = "recon",
        specific_drone_id: str = "",
        dispatched_by: str = "commander",
    ) -> str:
        if not target_area.strip():
            return "Clarification required: target_area must be specified to dispatch a drone."
        if not incident_description.strip():
            return "Clarification required: incident_description is required for drone mission dispatch."

        try:
            mission = self.surveillance_store.dispatch_drone(
                target_area=target_area.strip(),
                incident_description=incident_description.strip(),
                mission_type=mission_type.strip() or "recon",
                dispatched_by=dispatched_by.strip() or "commander",
                specific_drone_id=specific_drone_id.strip() or None,
            )
        except SurveillancePersistenceError as exc:
            return f"Drone dispatch failed: {exc}"

        drone = mission["drone"]
        eta_sec = mission["eta_seconds"]
        eta_min = round(eta_sec / 60, 1)

        return (
            f"Drone dispatched successfully:\n"
            f"- Mission ID: {mission['mission_id']}\n"
            f"- Drone: {drone['callsign']} ({drone['drone_id']}, Model: {drone['model']})\n"
            f"- Status: {mission['status'].upper()}\n"
            f"- Target Area: {mission['target_area']}\n"
            f"- Mission Type: {mission['mission_type'].upper()}\n"
            f"- Battery Level: {drone['battery_percent']}%\n"
            f"- Estimated Arrival (ETA): ~{eta_sec}s ({eta_min} min)\n"
            f"- Dispatched At: {mission['dispatched_at']}\n"
            f"- Incident Logged: {mission['incident_description']}"
        )

    @tool(
        "get_active_missions",
        "Returns all currently active tactical drone missions (dispatched, en route, or on station).",
        side_effecting=False,
    )
    def get_active_missions(self) -> str:
        missions = self.surveillance_store.get_active_missions()
        if not missions:
            return "No active drone missions currently in flight."

        lines = [f"Active Drone Missions ({len(missions)}):"]
        for m in missions:
            lines.append(
                f"- [{m['mission_id']}] Drone: {m['callsign']} ({m['drone_id']}) -> Area: {m['target_area']} "
                f"| Status: {m['status'].upper()} | Type: {m['mission_type']} | Battery: {m['battery_percent']}% | "
                f"ETA: {m['eta_seconds']}s | Dispatched: {m['dispatched_at']}"
                f"\n  Task: {m['incident_description']}"
            )
        return "\n".join(lines)

    @tool(
        "get_surveillance_overview",
        "Returns a combined tactical picture of all visual assets (cameras, drone fleet, and active airborne missions) for a specific sector or entire perimeter.",
        side_effecting=False,
    )
    def get_surveillance_overview(self, area: str = "") -> str:
        overview = self.surveillance_store.surveillance_overview(area=area.strip() or None)
        target = f"Sector '{area}'" if area.strip() else "All Sectors"

        lines = [
            f"=== Tactical Surveillance Overview: {target} ===",
            f"Report Timestamp: {overview['as_of']}",
            "",
            f"Cameras ({overview['active_camera_count']}/{len(overview['cameras'])} Active):",
        ]
        for c in overview["cameras"]:
            lines.append(
                f"  - [{c['camera_id']}] {c['name']} ({c['area']}): {c['status'].upper()} — {c['feed_summary']}"
            )

        lines.extend(
            (
                "",
                f"Drones ({overview['ready_drone_count']} Ready, {overview['in_flight_drone_count']} In Flight):",
            )
        )
        for d in overview["drones"]:
            lines.append(
                f"  - [{d['drone_id']}] {d['callsign']}: {d['status'].upper()} (Battery: {d['battery_percent']}%, Area: {d['current_area']})"
            )

        lines.extend(
            (
                "",
                f"Active Drone Missions: {len(overview['active_missions'])}",
            )
        )
        for m in overview["active_missions"]:
            lines.append(
                f"  - [{m['mission_id']}] {m['callsign']} -> {m['target_area']} ({m['status'].upper()}) - ETA: {m['eta_seconds']}s"
            )

        return "\n".join(lines)

    @tool(
        "update_camera_observation",
        "Updates a security camera's visual observation feed and timestamp when new video analysis or operator report is logged.",
        side_effecting=True,
        idempotent=True,
    )
    def update_camera_observation(self, camera_id: str, new_observation: str, status: str = "") -> str:
        if not camera_id.strip():
            return "Clarification required: camera_id is required."
        if not new_observation.strip():
            return "Clarification required: new_observation must not be empty."

        try:
            updated = self.surveillance_store.update_camera_feed(
                camera_id=camera_id.strip(),
                feed_summary=new_observation.strip(),
                status=status.strip() or None,
            )
        except SurveillancePersistenceError as exc:
            return f"Failed to update camera feed: {exc}"

        return (
            f"Camera '{updated['camera_id']}' feed successfully updated.\n"
            f"- Status: {updated['status'].upper()}\n"
            f"- Observation: {updated['feed_summary']}\n"
            f"- Last updated: {updated['last_updated']}"
        )
