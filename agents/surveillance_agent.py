"""Visual surveillance, camera monitoring, and aerial drone tactical specialist."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import threading
import uuid

from agents.contracts import AgentResult, InvocationPolicy
from agents.runtime import Agent, tool
from persistence import (
    SurveillancePersistenceError,
    open_surveillance_persistence,
)
from tools import get_trace_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_recall_invocation_key: ContextVar[str | None] = ContextVar("surveillance_recall_invocation_key", default=None)
_recall_results: dict[str, str] = {}
_recall_results_lock = threading.Lock()


def _capture_recall_result(output: str) -> None:
    key = _recall_invocation_key.get() or get_trace_id()
    if key:
        with _recall_results_lock:
            _recall_results[key] = output


class SurveillanceAgent(Agent):
    """Specialist agent responsible for cameras, drone fleet operations, and tactical aerial dispatch."""

    name = "surveillance_agent"
    role = (
        "Maintains real-time visual surveillance and situational awareness across all sectors. Monitors security "
        "cameras, reports visual feeds, checks drone fleet availability, dispatches tactical drones "
        "to incident locations, and tracks active airborne missions."
    )
    system_prompt = (
        "You are the tactical visual surveillance and drone operations specialist. "
        "Keep every response strictly concise, direct, and operational (BLUF - Bottom Line Up Front). "
        "For status questions, output one short summary line followed by at most one short line per relevant drone, mission, or camera. "
        "Use at most 6 lines total. Never use Markdown tables, report sections, decorative headings, repeated summaries, conclusions, "
        "recommendations, future-action lists, or narrative analysis unless the user explicitly asks for detail. "
        "Answer in Hebrew when the request is in Hebrew. Preserve IDs and operational status values exactly. "
        "The drone statuses in the fleet are: 'ready' (available for immediate dispatch), 'in_flight' (airborne on mission), 'charging', and 'maintenance'. "
        "When asked for drone fleet status or availability, call get_drone_fleet_status with an empty status_filter to see the full fleet and available ready units. "
        "When asked about cameras: state general status, then list only relevant cameras in compact single-line bullets. "
        "If asked about a specific camera or area, report ONLY on that camera or area. "
        "When asked about drones or dispatch: give only essential tactical facts (Callsign, Status, Battery, Location/Target, ETA). "
        "A specific drone ID or callsign is OPTIONAL for dispatch. If none was explicitly requested, leave specific_drone_id empty; "
        "dispatch_drone_to_area will deterministically select the best ready drone. Never ask for a drone ID merely because it was omitted. "
        "For a recall, call return_drone_to_base exactly once. If it reports multiple active drones, reproduce its list and ask the user "
        "to choose one; never choose a drone yourself. If exactly one drone is active, the tool returns it automatically. "
        "Call exactly one tool unless the task explicitly requests multiple distinct data sets. "
        "Never broaden an area, camera, drone, or mission filter beyond the scope explicitly requested. "
        "Never dispatch a drone or update an observation unless the task explicitly requests that exact state change. "
        "Do not repeat a tool call with the same arguments; treat the first successful result as authoritative for this request. "
        "Highlight anomalies or security events first."
    )

    surveillance_db_path = ""

    def __init__(self, model: str, api_key: str | None = None):
        if not self.surveillance_db_path:
            raise TypeError("SurveillanceAgent requires a class-level surveillance_db_path")
        self.surveillance_store = open_surveillance_persistence(self.surveillance_db_path)
        super().__init__(model, api_key)

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=220, reasoning_effort="none")
        key = get_trace_id() or uuid.uuid4().hex
        token = _recall_invocation_key.set(key)
        with _recall_results_lock:
            _recall_results.pop(key, None)
        try:
            model_result = super().process(text, allowed_tools, invocation_policy=invocation_policy)
            with _recall_results_lock:
                exact_recall_result = _recall_results.pop(key, None)
            if exact_recall_result is not None:
                return AgentResult(status="success", text=exact_recall_result)
            return model_result
        finally:
            with _recall_results_lock:
                _recall_results.pop(key, None)
            _recall_invocation_key.reset(token)

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
        "Dispatches an available tactical drone to an incident area for visual coverage/recon, calculating ETA and tracking mission status. "
        "specific_drone_id is optional: when omitted or empty, the system deterministically selects a ready drone by target-area proximity "
        "and then highest battery. Do not request a drone ID unless the user explicitly asked for a particular drone.",
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
        "return_drone_to_base",
        "Safely recalls a drone from an active mission. drone_or_mission_id is optional only when exactly one mission is active. "
        "If multiple drones are active and no identifier is supplied, no state changes and the tool returns the exact choices. "
        "Accepts a Drone ID, callsign, or Mission ID.",
        side_effecting=True,
        idempotent=True,
    )
    def return_drone_to_base(self, drone_or_mission_id: str = "") -> str:
        requested = drone_or_mission_id.strip()
        normalized = requested.casefold()
        if normalized in {
            "all", "all drones", "\u05db\u05d5\u05dc\u05dd", "\u05db\u05d5\u05dc\u05df",
            "\u05db\u05dc \u05d4\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd",
        } or "\u05db\u05d5\u05dc\u05dd" in normalized or "\u05db\u05dc \u05d4\u05e8\u05d7\u05e4" in normalized:
            result = self.surveillance_store.recall_all_drones()
        else:
            result = self.surveillance_store.recall_drone(requested or None)
        status = result["status"]
        if status == "no_active":
            output = "No active drone missions; no drone was returned."
            _capture_recall_result(output)
            return output
        if status == "returned_all":
            names = ", ".join(mission["callsign"] for mission in result["missions"])
            output = f"All active drones returned to base: {names}. Missions closed as operator recall."
            _capture_recall_result(output)
            return output
        if status in {"selection_required", "not_found"}:
            heading = (
                "DRONE_SELECTION_REQUIRED:\nMultiple drones are currently on active missions. "
                "Specify one Drone ID, callsign, or Mission ID:"
                if status == "selection_required"
                else f"DRONE_SELECTION_REQUIRED:\nNo active drone matched '{result.get('requested', '')}'. "
                "Choose one of these active drones:"
            )
            lines = [heading]
            for mission in result["missions"]:
                lines.append(
                    f"- {mission['callsign']} ({mission['drone_id']}) | Mission {mission['mission_id']} | "
                    f"Target {mission['target_area']} | Status {mission['status'].upper()}"
                )
            lines.append("No drone state was changed.")
            output = "\n".join(lines)
            _capture_recall_result(output)
            return output

        mission = result["mission"]
        drone = result["drone"]
        output = (
            "Drone returned to base successfully:\n"
            f"- Drone: {drone['callsign']} ({drone['drone_id']})\n"
            f"- Mission: {mission['mission_id']} closed as operator recall\n"
            f"- Current Area: {drone['current_area']}\n"
            f"- Fleet Status: {drone['status'].upper()}"
        )
        _capture_recall_result(output)
        return output

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
