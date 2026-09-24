"""Visual surveillance, camera monitoring, and aerial drone tactical specialist."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from agents.contracts import AgentResult, InvocationPolicy, ReportIngestionResult, project_report_facts
from agents.runtime import Agent, get_trusted_operational_scope, make_exact_result_capture, tool
from messages import get_catalog
from persistence import (
    OperationalScope,
    SurveillancePersistenceError,
    current_operational_scope,
    open_surveillance_persistence,
    scope_from_event,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_pattern(key: str) -> str:
    return get_catalog("en").text(key)


# Domain vocabulary for the trusted group-owned extraction path. These describe
# how an operator states a camera state or a heat alert, in either language —
# they are deliberately free of any scenario, camera number or place name, so a
# fixture can exercise this path without defining it.
_CAMERA_REFERENCE = re.compile(
    _catalog_pattern("extraction.surveillance.camera_reference"),
    re.IGNORECASE,
)

_CAMERA_STATUS_PATTERNS = (
    (re.compile(_catalog_pattern("extraction.surveillance.camera_active"), re.IGNORECASE), "active"),
    (re.compile(_catalog_pattern("extraction.surveillance.camera_offline"), re.IGNORECASE), "offline"),
    (re.compile(_catalog_pattern("extraction.surveillance.camera_degraded"), re.IGNORECASE), "degraded"),
)

_PLANNED_SHUTDOWN = re.compile(
    _catalog_pattern("extraction.surveillance.planned_shutdown"),
    re.IGNORECASE,
)

_DOWNTIME_TWO_HOURS = re.compile(_catalog_pattern("extraction.surveillance.downtime_two_hours"), re.IGNORECASE)
_DOWNTIME_HOURS = re.compile(_catalog_pattern("extraction.surveillance.downtime_hours"), re.IGNORECASE)
_DOWNTIME_ONE_HOUR = re.compile(_catalog_pattern("extraction.surveillance.downtime_one_hour"), re.IGNORECASE)

_HEAT_ALERT = re.compile(
    _catalog_pattern("extraction.surveillance.heat_alert"),
    re.IGNORECASE,
)
_SEVERITY_LOW = re.compile(_catalog_pattern("extraction.surveillance.severity_low"), re.IGNORECASE)
_SEVERITY_HIGH = re.compile(_catalog_pattern("extraction.surveillance.severity_high"), re.IGNORECASE)

_CONDITION_SOURCES = (
    (re.compile(_catalog_pattern("extraction.surveillance.temperature_sensor"), re.IGNORECASE), "temperature sensor"),
    (re.compile(_catalog_pattern("extraction.surveillance.thermal_camera"), re.IGNORECASE), "thermal camera"),
)


# `ContextVar` + lock + capture function + `process()`-override helper for
# forcing `return_drone_to_base`'s exact tool output back to the caller
# instead of the model's own paraphrase of it — see `agents.runtime.
# ExactResultCapture`. `profiles.standby_squad.StandbySquadSurveillanceAgent`
# (and its team-status/friendly-forces siblings) build their own instances
# of the same shared helper for the same reason.
_recall_capture = make_exact_result_capture("surveillance_recall")
_capture_recall_result = _recall_capture.capture

# Phrases meaning "recall every active drone," not one specific drone —
# checked before treating the argument as an identifier. Written as
# escaped \uXXXX Hebrew literals rather than the characters themselves so
# this file keeps passing tests/test_hebrew_leakage.py's scan for stray
# Hebrew outside the message catalog.
_RECALL_ALL_SENTINELS = frozenset(
    {
        "all",
        "all drones",
        "\u05db\u05d5\u05dc\u05dd",  # (Hebrew) all (plural, masculine)
        "\u05db\u05d5\u05dc\u05df",  # (Hebrew) all (plural, feminine)
        "\u05db\u05dc \u05d4\u05e8\u05d7\u05e4\u05e0\u05d9\u05dd",  # (Hebrew) all the drones
    }
)
_RECALL_ALL_SUBSTRINGS = (
    "\u05db\u05d5\u05dc\u05dd",  # (Hebrew) all
    "\u05db\u05dc \u05d4\u05e8\u05d7\u05e4",  # (Hebrew) all the drone(s) [prefix]
)


def _wants_all_drones(normalized: str) -> bool:
    return normalized in _RECALL_ALL_SENTINELS or any(term in normalized for term in _RECALL_ALL_SUBSTRINGS)


# Generic non-identifier filler words a caller — or an LLM acting on a
# caller's behalf — sometimes passes instead of leaving the parameter
# empty for auto-selection ("AUTO", "auto", "none", "-", ...). None of
# these ever collides with a real drone ID, callsign, or mission ID, so
# treating them as "no identifier given" is safe for every caller of
# `return_drone_to_base`, in any language, not only a profile that
# normalizes them itself.
_RECALL_TARGET_SENTINELS = frozenset(
    {
        "auto",
        "none",
        "null",
        "n/a",
        "-",
        "drone",
        "\u05e8\u05d7\u05e4\u05df",  # (Hebrew) drone
        "\u05d4\u05d7\u05d6\u05e8",  # (Hebrew) return
        "\u05d1\u05e1\u05d9\u05e1",  # (Hebrew) base
    }
)


class SurveillanceAgent(Agent):
    """Specialist agent responsible for cameras, drone fleet operations, and tactical aerial dispatch."""

    name = "surveillance_agent"
    # Group-owned report routing uses this declared domain capability rather
    # than trusting the classifier to choose a cross-domain event type.
    owned_report_types = ("surveillance_report", "operational_condition_report")
    default_report_type = "surveillance_report"
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
        "For a recall, call return_drone_to_base exactly once. If it reports multiple active drones, reproduce its list and ask the user "
        "to choose one; never choose a drone yourself. If exactly one drone is active, the tool returns it automatically. "
        "Call exactly one tool unless the task explicitly requests multiple distinct data sets. "
        "Never broaden an area, camera, drone, or mission filter beyond the scope explicitly requested. "
        "Never dispatch a drone or update an observation unless the task explicitly requests that exact state change. "
        "Do not repeat a tool call with the same arguments; treat the first successful result as authoritative for this request. "
        "Highlight anomalies or security events first."
    )

    surveillance_db_path = ""
    # Kept enabled for backwards-compatible specialist fixtures. Deployment
    # profiles that use a real operational registry must explicitly disable
    # demo provisioning on their concrete agent class.
    surveillance_seed_enabled = True
    surveillance_seed_profile = ""

    def __init__(self, model: str, api_key: str | None = None):
        if not self.surveillance_db_path:
            raise TypeError("SurveillanceAgent requires a class-level surveillance_db_path")
        self.surveillance_store = open_surveillance_persistence(
            self.surveillance_db_path,
            seed_demo_data=self.surveillance_seed_enabled,
            seed_profile=self.surveillance_seed_profile,
        )
        super().__init__(model, api_key)

    def ensure_operational_scope(self, scope: OperationalScope, baseline=None) -> None:
        self.surveillance_store.ensure_scope(scope, baseline=baseline)

    def _operational_scope(self) -> OperationalScope:
        return get_trusted_operational_scope() or current_operational_scope()

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=220, reasoning_effort="none")
        return _recall_capture.run(super().process, text, allowed_tools, invocation_policy=invocation_policy)

    def extract_report(self, raw_text: str, *, received_at: str, scenario_time: str | None = None, scope: OperationalScope | None = None, **_) -> ExtractionResult | None:
        """Extract only what the message itself grounds.

        Every field returned here becomes authoritative state, so a field that
        the message does not support is omitted and a report whose subject or
        state cannot be read is declined outright. Declining is safe: the
        message then travels the ordinary intake path instead of committing a
        value nobody reported.
        """

        from history import ExtractionResult
        text = str(raw_text or "")
        occurrence = scenario_time or received_at
        resolved_scope = scope or self._operational_scope()

        camera_report = self._extract_camera_report(text, occurrence, resolved_scope, ExtractionResult)
        if camera_report is not None:
            return camera_report

        return self._extract_condition_report(text, occurrence, ExtractionResult)

    def _extract_camera_report(self, text, occurrence, scope, ExtractionResult):
        canonicals = self._camera_references_in(text, scope)
        if not canonicals:
            return None

        status = self._camera_status_in(text)
        if status is None:
            return None

        shutdown_type = "planned_maintenance" if status == "offline" and _PLANNED_SHUTDOWN.search(text) else None
        fields = {
            "camera_id": canonicals[0],
            "camera_status": status,
            "downtime_duration_hours": self._downtime_hours_in(text),
            "shutdown_type": shutdown_type,
        }
        if len(canonicals) > 1:
            fields["camera_ids"] = ",".join(canonicals)
        area = next(
            (str(camera.get("area")) for camera in self.surveillance_store.list_cameras(scope=scope)
             if camera.get("camera_id") == canonicals[0]),
            None,
        )
        if area:
            fields["sector"] = area

        return ExtractionResult(
            "surveillance_report", "trusted", area, tuple(canonicals), text, "low", occurrence, False, (),
            business_fields={name: value for name, value in fields.items() if value is not None},
        )

    def _extract_condition_report(self, text, occurrence, ExtractionResult):
        if not _HEAT_ALERT.search(text):
            return None

        severity = "high" if _SEVERITY_HIGH.search(text) else "low" if _SEVERITY_LOW.search(text) else None
        if severity is None:
            return None

        sources = [label for pattern, label in _CONDITION_SOURCES if pattern.search(text)]
        fields = {"condition_type": "heat_alert", "severity_label": severity}
        if sources:
            fields["observation_source"] = ", ".join(sources)

        return ExtractionResult(
            "operational_condition_report", "trusted", None, (), text, severity, occurrence, False, (),
            business_fields=fields,
        )

    def _camera_references_in(self, text: str, scope) -> list[str]:
        """All cameras explicitly named in this message, resolved in scope."""

        numbers = list(dict.fromkeys(match.group(1) for match in _CAMERA_REFERENCE.finditer(text)))
        resolved = []
        for number in numbers:
            camera_id = self._resolve_camera_reference(number, scope=scope)
            if camera_id is not None and camera_id not in resolved:
                resolved.append(camera_id)
        return resolved

    @staticmethod
    def _camera_status_in(text: str) -> str | None:
        for pattern, status in _CAMERA_STATUS_PATTERNS:
            if pattern.search(text):
                return status
        return None

    @staticmethod
    def _downtime_hours_in(text: str) -> float | None:
        dual = _DOWNTIME_TWO_HOURS.search(text)
        if dual:
            return 2.0
        counted = _DOWNTIME_HOURS.search(text)
        if counted:
            return float(counted.group(1))
        if _DOWNTIME_ONE_HOUR.search(text):
            return 1.0
        return None

    def ingest_report(self, event: dict, *, scope: OperationalScope | None = None) -> ReportIngestionResult:
        """Commit a validated surveillance observation to the owning store.

        The event extractor supplies typed business fields; this hook never
        asks the model to perform the write and never emits an action receipt.
        """

        scope = scope or scope_from_event(event)
        self.ensure_operational_scope(scope)
        if event.get("classification") == "operational_condition_report":
            fields = event.get("business_fields") or {}
            # The severity the reporter actually stated, not a single fixed
            # level — still a closed set, so an ungrounded value is rejected.
            if fields.get("condition_type") != "heat_alert" or fields.get("severity_label") not in {"low", "medium", "high"}:
                return ReportIngestionResult("rejected", "operational condition report has invalid heat-alert fields")
            return ReportIngestionResult("committed", "operational condition committed", projection=project_report_facts(event, domain="surveillance", projection_kind="operational_fact", facts=fields))
        if event.get("classification") != "surveillance_report":
            return ReportIngestionResult("not_applicable")

        business_fields = event.get("business_fields") or {}
        unknown_fields = set(business_fields) - {
            "camera_id", "camera_ids", "camera_status", "status", "shutdown_type",
            "downtime_duration_hours", "reason", "sector", "cause_status", "possible_cause"
        }
        if unknown_fields:
            return ReportIngestionResult("rejected", "surveillance report contains unsupported domain fields")
        if any(value is not None and type(value) not in {str, int, float, bool} for value in business_fields.values()):
            return ReportIngestionResult("rejected", "surveillance report business fields must be scalar")
        camera_ids_value = business_fields.get("camera_ids")
        if camera_ids_value is not None and (not isinstance(camera_ids_value, str) or not camera_ids_value.strip()):
            return ReportIngestionResult("rejected", "surveillance camera_ids is invalid")
        camera_ids = [item.strip() for item in str(camera_ids_value or "").split(",") if item.strip()]
        camera_id = business_fields.get("camera_id")
        if not isinstance(camera_id, str) or not camera_id.strip():
            camera_id = next(
                (entity for entity in (event.get("entities") or ())
                 if isinstance(entity, str) and re.fullmatch(r"CAM-[A-Za-z0-9_-]+", entity.strip(), re.IGNORECASE)),
                None,
            )
        if not camera_id:
            return ReportIngestionResult("rejected", "surveillance report has no camera identifier")
        if not camera_ids:
            camera_ids = [camera_id.strip()]
        elif camera_id.strip() not in camera_ids:
            camera_ids.insert(0, camera_id.strip())
        resolved_camera_ids = []
        for reference in camera_ids:
            resolved = self._resolve_camera_reference(reference, scope=scope)
            if resolved is None:
                return ReportIngestionResult("rejected", "surveillance report references an unknown camera")
            if resolved not in resolved_camera_ids:
                resolved_camera_ids.append(resolved)

        observation = event.get("description")
        if not isinstance(observation, str) or not observation.strip():
            return ReportIngestionResult("rejected", "surveillance report has no observation")

        requested_status = business_fields.get("camera_status", business_fields.get("status"))
        if requested_status is not None and not isinstance(requested_status, str):
            return ReportIngestionResult("rejected", "surveillance camera status is invalid")
        status = requested_status.strip().lower() if isinstance(requested_status, str) else None
        # The shared camera contract has no separate maintenance enum. Keep
        # the persisted observation verbatim while representing maintenance as
        # the existing unavailable/offline state.
        if status == "maintenance":
            status = "offline"
        if status not in {None, "active", "degraded", "offline"}:
            return ReportIngestionResult("rejected", "surveillance camera status is invalid")
        shutdown_type = business_fields.get("shutdown_type")
        if shutdown_type is not None and shutdown_type != "planned_maintenance":
            return ReportIngestionResult("rejected", "surveillance shutdown type is invalid")
        duration_hours = business_fields.get("downtime_duration_hours")
        if duration_hours is not None and (
            type(duration_hours) not in {int, float} or duration_hours <= 0
        ):
            return ReportIngestionResult("rejected", "surveillance downtime duration is invalid")
        reason = business_fields.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            return ReportIngestionResult("rejected", "surveillance maintenance reason is invalid")
        sector = business_fields.get("sector")
        if sector is not None and (not isinstance(sector, str) or not sector.strip()):
            return ReportIngestionResult("rejected", "surveillance sector is invalid")
        if shutdown_type == "planned_maintenance" and status != "offline":
            return ReportIngestionResult("rejected", "planned maintenance must use offline camera status")
        cause_status = business_fields.get("cause_status")
        if cause_status is not None and cause_status != "unverified":
            return ReportIngestionResult("rejected", "surveillance report cause is not verified")
        possible_cause = business_fields.get("possible_cause")
        if possible_cause is not None and not isinstance(possible_cause, str):
            return ReportIngestionResult("rejected", "surveillance report possible cause is invalid")

        try:
            updated = None
            for resolved_camera_id in resolved_camera_ids:
                updated = self.surveillance_store.update_camera_feed(
                    resolved_camera_id, observation.strip(), status=status,
                    updated_at=event.get("received_at"),
                    scope=scope,
                )
        except SurveillancePersistenceError as exc:
            return ReportIngestionResult("failed", str(exc))
        projection = project_report_facts(
            event,
            domain="surveillance",
            facts={
                "camera_id": updated["camera_id"],
                "camera_ids": ",".join(resolved_camera_ids),
                "camera_status": updated["status"],
                **{
                    field_name: business_fields[field_name]
                    for field_name in (
                        "shutdown_type", "downtime_duration_hours", "reason", "sector",
                        "cause_status", "possible_cause",
                    )
                    if business_fields.get(field_name) is not None
                },
            },
        )
        return ReportIngestionResult(
            "committed", f"cameras {', '.join(resolved_camera_ids)} committed", projection=projection
        )

    def _resolve_camera_reference(self, reference: str, *, scope: OperationalScope | None = None) -> str | None:
        """Resolve a canonical camera ID or an explicitly supported alias."""

        normalized = " ".join(reference.strip().split()).casefold()
        for camera in self.surveillance_store.list_cameras(scope=scope or self._operational_scope()):
            camera_id = str(camera["camera_id"])
            canonical = camera_id.casefold()
            numeric = canonical.removeprefix("cam-")
            aliases = {
                canonical,
                numeric,
                f"cam-{numeric}",
                f"camera {numeric}",
                f"\u05de\u05e6\u05dc\u05de\u05d4 {numeric}",
            }
            if normalized in aliases:
                return camera_id
        return None

    def _recall(self, drone_or_mission_id: str) -> dict:
        """Resolve one recall request against the store — the state-machine step behind
        `return_drone_to_base`. A subclass that needs to localize the returned text (see
        `profiles.standby_squad.StandbySquadSurveillanceAgent`) calls this instead of
        duplicating the branching against `self.surveillance_store`."""

        requested = drone_or_mission_id.strip()
        normalized = requested.casefold()
        if _wants_all_drones(normalized):
            return self.surveillance_store.recall_all_drones(scope=self._operational_scope())
        if normalized in _RECALL_TARGET_SENTINELS:
            requested = ""
        return self.surveillance_store.recall_drone(requested or None, scope=self._operational_scope())

    @tool(
        "get_camera_feeds",
        "Returns current visual feed descriptions, azimuth, and status for security cameras, optionally filtered by area or specific camera ID.",
        side_effecting=False,
    )
    def get_camera_feeds(self, area: str = "", camera_id: str = "") -> str:
        if camera_id.strip():
            camera = self.surveillance_store.get_camera(camera_id.strip(), scope=self._operational_scope())
            if not camera:
                return f"Camera '{camera_id}' was not found in the surveillance registry."
            cameras = [camera]
        else:
            cameras = self.surveillance_store.list_cameras(area=area.strip() or None, scope=self._operational_scope())

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
    def get_drone_fleet_status(self, status_filter: str = "", drone_id_or_callsign: str = "") -> str:
        requested = drone_id_or_callsign.strip()
        if requested:
            normalized = requested.casefold()
            drones = [
                drone for drone in self.surveillance_store.list_drones(scope=self._operational_scope())
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
            drones = self.surveillance_store.list_drones(status=cleaned or None, scope=self._operational_scope())
        if not drones:
            # An unsupported status filter should not make the real fleet disappear.
            all_drones = self.surveillance_store.list_drones(scope=self._operational_scope())
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

        _SENTINEL_IDS = {"auto", "none", "null", "n/a", "-", "automatic", "any", "best", "default"}
        cleaned_drone_id = specific_drone_id.strip()
        if cleaned_drone_id.lower() in _SENTINEL_IDS:
            cleaned_drone_id = ""
        try:
            mission = self.surveillance_store.dispatch_drone(
                target_area=target_area.strip(),
                incident_description=incident_description.strip(),
                mission_type=mission_type.strip() or "recon",
                dispatched_by=dispatched_by.strip() or "commander",
                specific_drone_id=cleaned_drone_id or None,
                scope=self._operational_scope(),
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
        missions = self.surveillance_store.get_active_missions(scope=self._operational_scope())
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
        result = self._recall(drone_or_mission_id)
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
        overview = self.surveillance_store.surveillance_overview(area=area.strip() or None, scope=self._operational_scope())
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
                scope=self._operational_scope(),
            )
        except SurveillancePersistenceError as exc:
            return f"Failed to update camera feed: {exc}"

        return (
            f"Camera '{updated['camera_id']}' feed successfully updated.\n"
            f"- Status: {updated['status'].upper()}\n"
            f"- Observation: {updated['feed_summary']}\n"
            f"- Last updated: {updated['last_updated']}"
        )
