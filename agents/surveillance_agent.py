"""Visual surveillance, camera monitoring, and aerial drone tactical specialist."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from agents.contracts import AgentResult, InvocationPolicy, ToolResult, failed_tool_result
from agents.neighboring_forces_agent import resolve_event_links
from agents.runtime import Agent, make_exact_result_capture, tool
from messages import SUPPORTED_LANGUAGES, get_catalog, get_current_catalog
from persistence import (
    SurveillancePersistenceError,
    open_surveillance_persistence,
)


def _utc_now() -> str:
    """Utc now."""

    return datetime.now(timezone.utc).isoformat()


# Language-neutral words that always mean "no area filter"; localized phrases come from the
# profile catalog's `<stem>.area_scope.whole_site_aliases`.
_WHOLE_SITE_WORDS = frozenset({"all", "*"})


def _fold_area_text(text: str) -> str:
    """Casefold and treat underscores, hyphens, and repeated spaces alike for area matching."""

    return " ".join(text.replace("_", " ").replace("-", " ").casefold().split())


def _accepts_parameters(method, names: tuple[str, ...]) -> bool:
    """True when `method` takes every name in `names` (or **kwargs); older stores lack new ones."""

    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return True
    return all(name in parameters for name in names)


# `ContextVar` + lock + capture function + `process()`-override helper for
# forcing `return_drone_to_base`'s exact tool output back to the caller
# instead of the model's own paraphrase of it — see `agents.runtime.
# ExactResultCapture`. Profile subclasses build their own instances for the same reason.
_recall_capture = make_exact_result_capture("surveillance_recall")
_capture_recall_result = _recall_capture.capture


# A read-only status turn needs room for the tool result and a short operational
# summary. 220 tokens truncated live answers; keep this budget in one place so
# tests assert the current value instead of a stale literal.
READ_ONLY_DEFAULT_MAX_OUTPUT_TOKENS = 600


class SurveillanceAgent(Agent):
    """Specialist agent responsible for cameras, drone fleet operations, and tactical aerial dispatch."""

    name = "surveillance_agent"
    snapshot_tools = ("get_surveillance_overview",)
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
        "When the user names a drone ID or callsign, pass it as drone_id_or_callsign and report only that exact drone. "
        "If the tool says that named drone was not found, state that plainly and never replace it with the full fleet. "
        "When asked about cameras generally, leave both area and camera_id empty; words such as general, current, requested, or overall are not area names. "
        "State general status, then list only relevant cameras in compact single-line bullets. "
        "If asked about a specific camera or area, report ONLY on that camera or area. "
        "When asked about drones or dispatch: give only essential tactical facts (Callsign, Status, Battery, Location/Target, ETA). "
        "A specific drone ID or callsign is OPTIONAL for dispatch. If none was explicitly requested, leave specific_drone_id empty; "
        "dispatch_drone_to_area will deterministically select the best ready drone. Never ask for a drone ID merely because it was omitted. "
        "If the task is to return one drone, call return_drone_to_base exactly once. Pass a Drone ID, callsign, or Mission ID only when the task names one; otherwise pass an empty identifier. "
        "If it reports multiple active drones, reproduce its list and ask the user to choose one; never choose a drone yourself. "
        "If the task is to return every active drone, call return_all_drones_to_base exactly once. Never put that choice into drone_or_mission_id. "
        "Call exactly one tool unless the task explicitly requests multiple distinct data sets. "
        "Never broaden an area, camera, drone, or mission filter beyond the scope explicitly requested. "
        "Never dispatch a drone or update an observation unless the task explicitly requests that exact state change. "
        "Do not repeat a tool call with the same arguments; treat the first successful result as authoritative for this request. "
        "Highlight anomalies or security events first."
    )

    surveillance_db_path = ""
    # A profile subclass declares its areas and the catalog stem holding their labels
    # (`<stem>.area.<id>`). With no declared areas, area arguments pass through unchanged.
    declared_areas: tuple[str, ...] = ()
    area_catalog_stem = ""

    def __init__(self, model: str, api_key: str | None = None):
        """Init."""

        if not self.surveillance_db_path:
            raise TypeError("SurveillanceAgent requires a class-level surveillance_db_path")
        self.surveillance_store = open_surveillance_persistence(self.surveillance_db_path)
        super().__init__(model, api_key)

    def _area_label(self, area_id: str) -> str:
        """Localized label for one declared area in the current catalog, or the id itself."""

        key = f"{self.area_catalog_stem}.area.{area_id}"
        catalog = get_current_catalog()
        return catalog.text(key) if self.area_catalog_stem and key in catalog.messages else area_id

    def _area_aliases(self) -> dict[str, set[str]]:
        """Every accepted spelling per declared area: the id and its label in each catalog."""

        aliases: dict[str, set[str]] = {}
        for area_id in self.declared_areas:
            spellings = {_fold_area_text(area_id)}
            key = f"{self.area_catalog_stem}.area.{area_id}"
            for language in SUPPORTED_LANGUAGES:
                catalog = get_catalog(language)
                if self.area_catalog_stem and key in catalog.messages:
                    spellings.add(_fold_area_text(catalog.text(key)))
            aliases[area_id] = spellings
        return aliases

    def _whole_site_aliases(self) -> set[str]:
        """Phrases meaning "the whole site" in every catalog, plus the language-neutral words."""

        phrases = set(_WHOLE_SITE_WORDS)
        key = f"{self.area_catalog_stem}.area_scope.whole_site_aliases"
        for language in SUPPORTED_LANGUAGES:
            catalog = get_catalog(language)
            if self.area_catalog_stem and key in catalog.messages:
                phrases.update(_fold_area_text(part) for part in catalog.text(key).split("|") if part.strip())
        return phrases

    def _resolve_area(self, raw: str) -> tuple[str | None, str | None]:
        """Map an area argument to (declared area id or None for the whole site, error or None).

        Accepts the id, its catalog label in any language, or a unique partial match. An
        unrecognized or ambiguous value is an error listing the valid areas, so a lookup can
        never silently come back empty because of a spelling the store does not use.
        """

        cleaned = " ".join((raw or "").split())
        if not self.declared_areas:
            return (cleaned or None), None
        if not cleaned:
            return None, None
        folded = _fold_area_text(cleaned)
        if folded in self._whole_site_aliases():
            return None, None
        aliases = self._area_aliases()
        exact = [area_id for area_id, spellings in aliases.items() if folded in spellings]
        if len(exact) == 1:
            return exact[0], None
        # A label inside a longer phrase ("the west gate area"), or a shortened label missing
        # its article, still names one area; three characters keeps stray letters from matching.
        partial = [
            area_id for area_id, spellings in aliases.items()
            if any(spelling in folded or (len(folded) >= 3 and folded in spelling) for spelling in spellings)
        ]
        if len(partial) == 1:
            return partial[0], None
        valid = ", ".join(f"{area_id} ({self._area_label(area_id)})" for area_id in self.declared_areas)
        return None, (
            f"Clarification required: unknown or ambiguous area '{cleaned}'. Valid areas: {valid}. "
            "Leave area empty for the whole site."
        )

    @staticmethod
    def _camera_line_suffix(camera: dict) -> str:
        """Camera kind wording, so a mast or drone feed is never mistaken for a fixed fence camera."""

        kind = camera.get("kind")
        return f", {kind} camera" if kind else ""

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        """Process."""

        if invocation_policy is None:
            # A tool turn and its final operational summary must both fit; 220 tokens
            # truncated a live read-only answer before the Main Agent composed it.
            invocation_policy = InvocationPolicy(
                max_output_tokens=READ_ONLY_DEFAULT_MAX_OUTPUT_TOKENS, reasoning_effort="none"
            )
        return _recall_capture.run(super().process, text, allowed_tools, invocation_policy=invocation_policy)

    def _recall(self, drone_or_mission_id: str) -> dict:
        """Resolve one named-or-empty recall against the store for return_drone_to_base."""

        requested = drone_or_mission_id.strip()
        return self.surveillance_store.recall_drone(requested or None)

    def _render_recall(self, result: dict) -> str | ToolResult:
        """Format a recall console line."""

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
                "Multiple drones are currently on active missions. "
                "Specify one Drone ID, callsign, or Mission ID:"
                if status == "selection_required"
                else (
                    f"No active drone matched '{result.get('requested', '')}'. "
                    "Choose one of these active drones:"
                )
            )
            lines = [heading]
            for mission in result["missions"]:
                lines.append(
                    f"- {mission['callsign']} ({mission['drone_id']}) | Mission {mission['mission_id']} | "
                    f"Target {mission['target_area']} | Status {mission['status'].upper()}"
                )
            lines.append("No drone state was changed.")
            output = "\n".join(lines)
            _recall_capture.capture(output, selection_required=True)
            return ToolResult(text=output, selection_required=True)

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
        "get_camera_feeds",
        "Returns current visual feed descriptions, azimuth, and status for security cameras, optionally filtered by area or specific camera ID.",
        side_effecting=False,
    )
    def get_camera_feeds(self, area: str = "", camera_id: str = "") -> str | ToolResult:
        """Return the camera feeds."""

        cleaned_area = ""
        if camera_id.strip():
            camera = self.surveillance_store.get_camera(camera_id.strip())
            if not camera:
                return f"Camera '{camera_id}' was not found in the surveillance registry."
            cameras = [camera]
        else:
            resolved_area, area_error = self._resolve_area(area)
            if area_error:
                return failed_tool_result(area_error)
            cleaned_area = resolved_area or ""
            cameras = self.surveillance_store.list_cameras(area=resolved_area)

        if not cameras:
            if cleaned_area:
                # Deterministic, from the store's own list_cameras count — never inferred from
                # any wording — so an incident-handling step that relies on camera coverage for
                # an area gets the same resource-unavailable treatment as a failed dispatch.
                self.signal_resource_unavailable("camera", cleaned_area, "no active camera covering the area")
            scope = f"in area '{cleaned_area}'" if cleaned_area else "in the surveillance registry"
            return f"No cameras found {scope}."

        lines = [f"Camera feeds ({len(cameras)} cameras):"]
        for c in cameras:
            lines.append(
                f"- [{c['camera_id']}] {c['name']} ({c['area']}, {c['azimuth_degrees']}°{self._camera_line_suffix(c)}): "
                f"{c['feed_summary']} [{c['status'].upper()}]"
            )
        return "\n".join(lines)

    @tool(
        "get_drone_fleet_status",
        "Returns current operational status, battery levels, locations, and mission assignments for the tactical drone fleet. Leave status_filter empty to return all drones. Valid status filters: 'ready' (available for dispatch), 'in_flight', 'charging', 'maintenance'.",
        side_effecting=False,
    )
    def get_drone_fleet_status(self, status_filter: str = "", drone_id_or_callsign: str = "") -> str:
        """Return the drone fleet status."""

        requested = drone_id_or_callsign.strip()
        if requested:
            normalized = requested.casefold()
            drones = [
                drone for drone in self.surveillance_store.list_drones()
                if str(drone["drone_id"]).casefold() == normalized
                or str(drone["callsign"]).casefold() == normalized
            ]
            if not drones:
                return f"Drone '{requested}' was not found in the tactical fleet registry."
        else:
            drones = []
        cleaned = status_filter.strip().lower()
        if cleaned in {"all", "*"}:
            cleaned = ""
        if not requested:
            drones = self.surveillance_store.list_drones(status=cleaned or None)
        if not drones:
            # An unsupported status filter should not make the real fleet disappear.
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
        "and then highest battery. Do not request a drone ID unless the user explicitly asked for a particular drone. "
        "If a drone mission already covers the target area, that mission is reported instead of launching a second drone. "
        "event_id and incident_id are optional links to the report being handled.",
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
        event_id: str = "",
        incident_id: str = "",
    ) -> str:
        """Dispatch drone to area."""

        if not target_area.strip():
            return failed_tool_result("Clarification required: target_area must be specified to dispatch a drone.")
        if not incident_description.strip():
            return failed_tool_result("Clarification required: incident_description is required for drone mission dispatch.")

        # A recognizable label becomes the declared id so ETA and coverage checks match; an
        # unknown value still dispatches as given, because refusing a launch over a spelling
        # would be worse than an approximate ETA.
        resolved_area, _area_error = self._resolve_area(target_area)
        cleaned_area = resolved_area or target_area.strip()

        _SENTINEL_IDS = {"auto", "none", "null", "n/a", "-", "automatic", "any", "best", "default"}
        cleaned_drone_id = specific_drone_id.strip()
        if cleaned_drone_id.lower() in _SENTINEL_IDS:
            cleaned_drone_id = ""
        link_kwargs: dict = {}
        linked_event, linked_incident = resolve_event_links(event_id, incident_id)
        if (linked_event or linked_incident) and _accepts_parameters(
            self.surveillance_store.dispatch_drone, ("event_id", "incident_id")
        ):
            link_kwargs = {"event_id": linked_event, "incident_id": linked_incident}
        try:
            mission = self.surveillance_store.dispatch_drone(
                target_area=cleaned_area,
                incident_description=incident_description.strip(),
                mission_type=mission_type.strip() or "recon",
                dispatched_by=dispatched_by.strip() or "commander",
                specific_drone_id=cleaned_drone_id or None,
                **link_kwargs,
            )
        except SurveillancePersistenceError as exc:
            self.signal_resource_unavailable("drone", cleaned_area, str(exc))
            return f"Drone dispatch failed: {exc}"

        drone = mission["drone"]
        if mission.get("reused"):
            return (
                "Drone already covering the area; no additional drone was launched:\n"
                f"- Mission ID: {mission['mission_id']}\n"
                f"- Drone: {drone['callsign']} ({drone['drone_id']}, Model: {drone['model']})\n"
                f"- Status: {mission['status'].upper()}\n"
                f"- Target Area: {mission['target_area']}\n"
                f"- Dispatched At: {mission['dispatched_at']} (ETA {mission['eta_seconds']}s from dispatch)\n"
                f"- Original Task: {mission['incident_description']}"
            )
        eta_sec = mission["eta_seconds"]
        eta_min = round(eta_sec / 60, 1)
        retasked = mission.get("retasked_from")
        retask_line = (
            f"- Retasked from: {retasked['target_area']} (mission {retasked['mission_id']} closed; "
            "no drone was free, and this mission outranks routine recon)\n"
            if retasked
            else ""
        )

        return (
            f"Drone dispatched successfully:\n"
            f"{retask_line}"
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
        """Return the active missions."""

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
        "Safely recalls one drone from an active mission. drone_or_mission_id is optional only when exactly one mission is active. "
        "If multiple drones are active and no identifier is supplied, no state changes and the tool returns the exact choices. "
        "Accepts a Drone ID, callsign, or Mission ID. To recall every active drone, call return_all_drones_to_base instead.",
        side_effecting=True,
        idempotent=True,
    )
    def return_drone_to_base(self, drone_or_mission_id: str = "") -> str:
        """Return drone to base."""

        return self._render_recall(self._recall(drone_or_mission_id))

    @tool(
        "return_all_drones_to_base",
        "Safely recalls every drone that is currently on an active mission. No identifier is required. "
        "To recall one named drone, call return_drone_to_base instead.",
        side_effecting=True,
        idempotent=True,
    )
    def return_all_drones_to_base(self) -> str:
        """Return all drones to base."""

        return self._render_recall(self.surveillance_store.recall_all_drones())

    @tool(
        "get_surveillance_overview",
        "Returns a combined tactical picture of all visual assets (cameras, drone fleet, and active airborne missions). "
        "Leave area empty for the whole site; otherwise pass one area id or its name. An unknown area returns an error listing the valid areas.",
        side_effecting=False,
    )
    def get_surveillance_overview(self, area: str = "") -> str | ToolResult:
        """Return the surveillance overview."""

        resolved_area, area_error = self._resolve_area(area)
        if area_error:
            return failed_tool_result(area_error)
        overview = self.surveillance_store.surveillance_overview(area=resolved_area)
        target = f"Sector '{resolved_area}'" if resolved_area else "All Sectors"

        lines = [
            f"=== Tactical Surveillance Overview: {target} ===",
            f"Report Timestamp: {overview['as_of']}",
            "",
            f"Cameras ({overview['active_camera_count']}/{len(overview['cameras'])} Active):",
        ]
        for c in overview["cameras"]:
            lines.append(
                f"  - [{c['camera_id']}] {c['name']} ({c['area']}{self._camera_line_suffix(c)}): "
                f"{c['status'].upper()} — {c['feed_summary']}"
            )

        lines.extend(
            (
                "",
                f"Drones ({overview['ready_drone_count']} Ready, {overview['in_flight_drone_count']} In Flight):",
            )
        )
        if resolved_area and "fleet_ready_drone_count" in overview:
            lines.append(f"  Fleet-wide ready for dispatch: {overview['fleet_ready_drone_count']}")
        for d in overview["drones"]:
            # A drone in flight with its mission id is on assignment, not an anomaly.
            mission_info = f", Mission: {d['assigned_mission_id']}" if d.get("assigned_mission_id") else ""
            lines.append(
                f"  - [{d['drone_id']}] {d['callsign']}: {d['status'].upper()} "
                f"(Battery: {d['battery_percent']}%, Area: {d['current_area']}{mission_info})"
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
        """Update camera observation."""

        from messages.camera_names import resolve_camera_id

        camera_id = resolve_camera_id(
            self.surveillance_store,
            camera_id,
            getattr(self, "camera_catalog_stem", ""),
        )
        if not camera_id.strip():
            return failed_tool_result("Clarification required: camera_id is required.")
        if not new_observation.strip():
            return failed_tool_result("Clarification required: new_observation must not be empty.")

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
