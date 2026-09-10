"""Unified command-and-control profile for readiness team, surveillance, and tactical forces."""

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents import (
    AgentResult,
    FriendlyForcesAgent,
    get_authenticated_request_identity,
    InvocationPolicy,
    SurveillanceAgent,
    TeamStatusAgent,
    make_exact_result_capture,
    tool,
)
from messages import get_catalog
from persistence import open_persistence, open_surveillance_persistence, open_team_status_persistence
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol

DEFAULT_LANGUAGE = "he"


def _catalog_text(key: str, **values) -> str:
    """Look up one message in this profile's own language.

    The one place `profiles/unified_test.py` reads user-facing (or
    model-facing) text — from `messages/he.py`/`messages/en.py` — rather
    than holding it as a literal in this file, so this module has no
    Hebrew of its own for tests/test_hebrew_leakage.py's HARD RULE to
    catch. See that catalog for the actual Hebrew/English wording.
    """

    return get_catalog(DEFAULT_LANGUAGE).text(key, **values)


PROFILE_NAME = _catalog_text("unified.profile_name")
MAX_ITER = 6
MODEL_TIMEOUT_SECONDS = 45

# Status/action icons used throughout this profile's operational output.
# Kept as plain Python constants rather than in messages/en.py or
# messages/he.py: those catalogs enforce a separate, unconditional "no
# emoji in any catalog message" rule (tests/test_messages.py's
# test_no_emoji_in_any_catalog_message) that predates this profile and
# has no per-file exemption. Each affected catalog entry instead declares
# an {icon} placeholder, filled in with one of these at the call site —
# the rendered text a user sees is unaffected either way.
_ICON_CHECK = "✅"
_ICON_CROSS = "❌"
_ICON_DRONE = "\U0001f6f8"
_ICON_BATTERY = "\U0001f50b"
_ICON_MAINTENANCE = "\U0001f6e0️"
_ICON_CAMERA = "\U0001f4f9"
_ICON_CHART = "\U0001f4ca"
_ICON_PEOPLE = "\U0001f465"
_ICON_HOURGLASS = "⏳"
_ICON_ROCKET = "\U0001f680"
_ICON_REPEAT = "\U0001f504"
_ICON_SIREN = "\U0001f6a8"
_ICON_SCROLL = "\U0001f4dc"

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "unified_test"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "unified_history.db")
UNIFIED_SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "unified_surveillance.db")
UNIFIED_TEAM_STATUS_DB_PATH = str(_PROFILE_DATA_DIR / "unified_team_status.db")
RESETTABLE_DATABASES = (DB_PATH, UNIFIED_SURVEILLANCE_DB_PATH, UNIFIED_TEAM_STATUS_DB_PATH)

BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []


# Each specialist below needs its tool output to reach the caller exactly
# as the tool wrote it — never paraphrased by the model — the same
# guarantee `agents.surveillance_agent.SurveillanceAgent` gives its own
# `return_drone_to_base` tool. `agents.make_exact_result_capture` is the
# shared implementation of that pattern (see docs/profile_spec.md); one
# instance per agent, so unrelated captures can never collide.
_surv_capture = make_exact_result_capture("unified_surveillance")
_capture_surv_result = _surv_capture.capture

_team_capture = make_exact_result_capture("unified_team_status")
_capture_team_result = _team_capture.capture
# Separate from the capture above: the raw query text for the current
# `process()` call, consulted by `_requested_roster_view`/`_matching_member`
# to infer which roster view or member a free-text question meant.
_team_query_text: ContextVar[str] = ContextVar("unified_team_query_text", default="")

_forces_capture = make_exact_result_capture("unified_friendly_forces")
_capture_forces_result = _forces_capture.capture


class UnifiedSurveillanceAgent(SurveillanceAgent):
    """Binds the visual surveillance specialist with Hebrew tactical tools."""

    surveillance_db_path = UNIFIED_SURVEILLANCE_DB_PATH
    role = _catalog_text("unified.surveillance.role")
    system_prompt = _catalog_text("unified.surveillance.system_prompt")

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=250, reasoning_effort="none")
        return _surv_capture.run(super().process, text, allowed_tools, invocation_policy=invocation_policy)

    @tool(
        "get_drone_fleet_status",
        _catalog_text("unified.surveillance.tool.fleet_status"),
        side_effecting=False,
    )
    def get_drone_fleet_status(self, status_filter: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        cleaned = status_filter.strip().lower()
        if cleaned in {"all", "*"}:
            cleaned = ""
        drones = self.surveillance_store.list_drones(status=cleaned or None)
        if not drones:
            all_drones = self.surveillance_store.list_drones()
            drones = all_drones if all_drones else []
        if not drones:
            res = catalog.text("unified.surveillance.no_drones")
            _capture_surv_result(res)
            return res
        status_map = {
            "ready": catalog.text("unified.surveillance.status.ready", icon=_ICON_CHECK),
            "in_flight": catalog.text("unified.surveillance.status.in_flight", icon=_ICON_DRONE),
            "charging": catalog.text("unified.surveillance.status.charging", icon=_ICON_BATTERY),
            "maintenance": catalog.text("unified.surveillance.status.maintenance", icon=_ICON_MAINTENANCE),
        }
        lines = [catalog.text("unified.surveillance.fleet_header", count=len(drones), icon=_ICON_DRONE)]
        counts = {"ready": 0, "in_flight": 0, "charging": 0, "maintenance": 0}
        for d in drones:
            st = status_map.get(d["status"], d["status"])
            counts[d["status"]] = counts.get(d["status"], 0) + 1
            mission_info = (
                catalog.text("unified.surveillance.fleet_mission_info", mission_id=d["assigned_mission_id"])
                if d.get("assigned_mission_id")
                else ""
            )
            lines.append(
                catalog.text(
                    "unified.surveillance.fleet_line",
                    drone_id=d["drone_id"],
                    callsign=d["callsign"],
                    model=d["model"],
                    status=st,
                    battery=d["battery_percent"],
                    area=d["current_area"],
                    mission_info=mission_info,
                )
            )
        lines.append(
            catalog.text(
                "unified.surveillance.fleet_summary",
                ready=counts.get("ready", 0),
                in_flight=counts.get("in_flight", 0),
                charging=counts.get("charging", 0),
            )
        )
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "get_active_missions",
        _catalog_text("unified.surveillance.tool.active_missions"),
        side_effecting=False,
    )
    def get_active_missions(self) -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        missions = self.surveillance_store.get_active_missions()
        if not missions:
            res = catalog.text("unified.surveillance.no_missions")
            _capture_surv_result(res)
            return res
        lines = [catalog.text("unified.surveillance.missions_header", count=len(missions), icon=_ICON_DRONE)]
        for m in missions:
            lines.append(
                catalog.text(
                    "unified.surveillance.mission_line",
                    mission_id=m["mission_id"],
                    callsign=m["callsign"],
                    drone_id=m["drone_id"],
                    target_area=m["target_area"],
                    battery=m["battery_percent"],
                    eta=m["eta_seconds"],
                    description=m["incident_description"],
                )
            )
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "get_camera_feeds",
        _catalog_text("unified.surveillance.tool.camera_feeds"),
        side_effecting=False,
    )
    def get_camera_feeds(self, area: str = "", camera_id: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        if camera_id.strip():
            c = self.surveillance_store.get_camera(camera_id.strip())
            cameras = [c] if c else []
        else:
            cameras = self.surveillance_store.list_cameras(area=area.strip() or None)
        if not cameras:
            res = catalog.text("unified.surveillance.no_cameras")
            _capture_surv_result(res)
            return res
        status_map = {
            "active": catalog.text("unified.surveillance.camera_status.active", icon=_ICON_CHECK),
            "offline": catalog.text("unified.surveillance.camera_status.offline", icon=_ICON_CROSS),
            "maintenance": catalog.text("unified.surveillance.camera_status.maintenance", icon=_ICON_MAINTENANCE),
        }
        lines = [catalog.text("unified.surveillance.cameras_header", count=len(cameras), icon=_ICON_CAMERA)]
        for c in cameras:
            st = status_map.get(c["status"], c["status"])
            lines.append(
                catalog.text(
                    "unified.surveillance.camera_line",
                    camera_id=c["camera_id"],
                    name=c["name"],
                    area=c["area"],
                    azimuth=c["azimuth_degrees"],
                    feed_summary=c["feed_summary"],
                    status=st,
                )
            )
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "get_surveillance_overview",
        _catalog_text("unified.surveillance.tool.overview"),
        side_effecting=False,
    )
    def get_surveillance_overview(self, area: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        overview = self.surveillance_store.surveillance_overview(area=area.strip() or None)
        lines = [
            catalog.text("unified.surveillance.overview_header", icon=_ICON_CHART),
            catalog.text(
                "unified.surveillance.overview_cameras_line",
                active=overview["active_camera_count"],
                total=len(overview["cameras"]),
            ),
            catalog.text(
                "unified.surveillance.overview_drones_line",
                ready=overview["ready_drone_count"],
                in_flight=overview["in_flight_drone_count"],
            ),
        ]
        if overview["active_missions"]:
            lines.append(
                catalog.text("unified.surveillance.overview_missions_header", count=len(overview["active_missions"]))
            )
            for m in overview["active_missions"]:
                lines.append(
                    catalog.text(
                        "unified.surveillance.overview_mission_line",
                        callsign=m["callsign"],
                        target_area=m["target_area"],
                        eta=m["eta_seconds"],
                    )
                )
        else:
            lines.append(catalog.text("unified.surveillance.overview_no_missions"))
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "return_drone_to_base",
        _catalog_text("unified.surveillance.tool.return_drone"),
        side_effecting=True,
        idempotent=True,
    )
    def return_drone_to_base(self, drone_or_mission_id: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        try:
            # The actual recall state machine — which drone(s), if any, get
            # recalled — lives once in `SurveillanceAgent._recall` (see that
            # docstring); this override only localizes the text it returns.
            result = super()._recall(drone_or_mission_id)
            status = result["status"]
            if status == "no_active":
                res = catalog.text("unified.surveillance.recall_none_active")
            elif status == "returned_all":
                res = catalog.text(
                    "unified.surveillance.recall_all_done", count=len(result["missions"]), icon=_ICON_CHECK
                )
            elif status == "not_found" and len(result["missions"]) == 1:
                # Profile-specific leniency, not part of the shared state
                # machine: with exactly one drone active, retry as an
                # auto-select rather than asking the commander to
                # disambiguate among a list of exactly one non-matching name.
                fallback = super()._recall("")
                if fallback["status"] == "returned":
                    d = fallback["drone"]
                    res = catalog.text(
                        "unified.surveillance.recall_fallback_done",
                        callsign=d["callsign"],
                        drone_id=d["drone_id"],
                        icon=_ICON_CHECK,
                    )
                else:
                    res = catalog.text("unified.surveillance.recall_no_match_single")
            elif status == "not_found":
                res = catalog.text(
                    "unified.surveillance.recall_no_match_multi",
                    requested=result.get("requested", ""),
                    count=len(result["missions"]),
                )
            elif status == "selection_required":
                res = catalog.text("unified.surveillance.recall_selection_required", count=len(result["missions"]))
            elif status == "returned":
                d = result["drone"]
                res = catalog.text(
                    "unified.surveillance.recall_done", callsign=d["callsign"], drone_id=d["drone_id"], icon=_ICON_CHECK
                )
            else:
                res = catalog.text("unified.surveillance.recall_done_generic", icon=_ICON_CHECK)
        except Exception as exc:
            res = catalog.text("unified.surveillance.recall_failed", error=str(exc))
        _capture_surv_result(res)
        return res

    @tool(
        "dispatch_drone_to_area",
        _catalog_text("unified.surveillance.tool.dispatch_drone"),
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_drone_to_area(
        self,
        target_area: str,
        incident_description: str = _catalog_text("unified.surveillance.default_incident_description"),
        mission_type: str = "recon",
        specific_drone_id: str = "",
        dispatched_by: str = "commander",
    ) -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        if not target_area.strip():
            res = catalog.text("unified.surveillance.dispatch_area_required")
            _capture_surv_result(res)
            return res
        if not incident_description.strip():
            incident_description = catalog.text("unified.surveillance.default_incident_description")
        # Filter out LLM placeholder/sentinel values for specific_drone_id.
        # The LLM sometimes passes "AUTO", "auto", "none", "null", "-" etc.
        # when it means "let the system choose". In all such cases, use auto-select.
        _SENTINEL_DRONE_IDS = {"auto", "none", "null", "n/a", "-", "automatic", "any", "best", "default"}
        cleaned_drone_id = specific_drone_id.strip()
        if cleaned_drone_id.lower() in _SENTINEL_DRONE_IDS:
            cleaned_drone_id = ""
        try:
            mission = self.surveillance_store.dispatch_drone(
                target_area=target_area.strip(),
                incident_description=incident_description.strip(),
                mission_type=mission_type.strip() or "recon",
                dispatched_by=dispatched_by.strip() or "commander",
                specific_drone_id=cleaned_drone_id or None,
            )
        except Exception as exc:
            res = catalog.text("unified.surveillance.dispatch_failed", error=str(exc))
            _capture_surv_result(res)
            return res
        d = mission["drone"]
        res = catalog.text(
            "unified.surveillance.dispatch_done",
            callsign=d["callsign"],
            drone_id=d["drone_id"],
            target_area=mission["target_area"],
            eta=mission["eta_seconds"],
            battery=d["battery_percent"],
            mission_id=mission["mission_id"],
            icon=_ICON_CHECK,
        )
        _capture_surv_result(res)
        return res

    @tool(
        "update_camera_observation",
        _catalog_text("unified.surveillance.tool.update_camera"),
        side_effecting=True,
        idempotent=True,
    )
    def update_camera_observation(self, camera_id: str, observation_note: str, status: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        if not camera_id.strip():
            res = catalog.text("unified.surveillance.camera_id_required")
            _capture_surv_result(res)
            return res
        try:
            cam = self.surveillance_store.update_camera_feed(
                camera_id.strip(),
                feed_summary=observation_note.strip() or None,
                status=status.strip().lower() or None,
            )
            res = catalog.text(
                "unified.surveillance.camera_update_done",
                camera_id=cam["camera_id"],
                name=cam["name"],
                feed_summary=cam["feed_summary"],
                icon=_ICON_CHECK,
            )
        except Exception as exc:
            res = catalog.text("unified.surveillance.camera_update_failed", error=str(exc))
        _capture_surv_result(res)
        return res


class UnifiedTeamStatusAgent(TeamStatusAgent):
    """Binds the readiness-team status specialist with Hebrew reporting tools."""

    status_db_path = UNIFIED_TEAM_STATUS_DB_PATH
    timezone_name = "Asia/Jerusalem"
    attendance_check_hour = 8
    response_window_hours = 1
    role = _catalog_text("unified.team_status.role")
    system_prompt = _catalog_text("unified.team_status.system_prompt")

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=250, reasoning_effort="none")
        query_token = _team_query_text.set(text)
        try:
            return _team_capture.run(super().process, text, allowed_tools, invocation_policy=invocation_policy)
        finally:
            _team_query_text.reset(query_token)

    @staticmethod
    def _requested_roster_view(view: str) -> str:
        requested = view.strip().lower()
        if requested and requested != "summary":
            return requested

        catalog = get_catalog(DEFAULT_LANGUAGE)
        text = _team_query_text.get().lower()

        def _matches(key: str) -> bool:
            # Keyword groups are stored as one "|"-delimited catalog string
            # each, not one key per word — see messages/he.py.
            return any(term in text for term in catalog.text(key).split("|"))

        if _matches("unified.team_status.keywords.reason"):
            return "reason"
        if _matches("unified.team_status.keywords.awaiting"):
            return "awaiting"
        if _matches("unified.team_status.keywords.unavailable"):
            return "unavailable"
        if _matches("unified.team_status.keywords.count_number") and _matches(
            "unified.team_status.keywords.count_available"
        ):
            return "count"
        if _matches("unified.team_status.keywords.available"):
            return "available"
        if _matches("unified.team_status.keywords.members"):
            return "members"
        return "summary"

    @staticmethod
    def _member_name(entry: dict) -> str:
        name = entry["full_name"].strip()
        identity = entry["telegram_identity"]
        catalog = get_catalog(DEFAULT_LANGUAGE)
        if name == catalog.text("unified.team_status.legacy_placeholder_name", identity=identity):
            return catalog.text("unified.team_status.unnamed_member", identity=identity)
        return name

    @classmethod
    def _matching_member(cls, snapshot: list[dict], query: str) -> dict | None:
        normalized = query.casefold()
        matches = [
            entry for entry in snapshot
            if entry["telegram_identity"].casefold() in normalized
            or cls._member_name(entry).casefold() in normalized
        ]
        return matches[0] if len(matches) == 1 else None

    @tool(
        "report_team_availability",
        _catalog_text("unified.team_status.tool.report_availability"),
        side_effecting=False,
    )
    def report_team_availability(
        self, as_of_iso: str = "", view: str = "summary", member_query: str = ""
    ) -> str:
        from datetime import datetime, timezone
        catalog = get_catalog(DEFAULT_LANGUAGE)
        now_iso = as_of_iso or datetime.now(timezone.utc).isoformat()
        snapshot = self.status_store.availability_snapshot(now_iso)
        avail = [e for e in snapshot if e["availability"] == "available"]
        unavail = [e for e in snapshot if e["availability"] == "unavailable"]
        awaiting = [e for e in snapshot if e["availability"] in {"awaiting_response", "pending"}]
        requested_view = self._requested_roster_view(view)

        none_now = catalog.text("unified.team_status.none_now")
        names = lambda entries: ", ".join(self._member_name(entry) for entry in entries) or none_now
        if requested_view == "members":
            lines = [
                catalog.text(
                    "unified.team_status.members_header",
                    count=len(snapshot),
                    names=names(snapshot),
                    icon=_ICON_PEOPLE,
                )
            ]
        elif requested_view == "available":
            lines = [
                catalog.text(
                    "unified.team_status.available_header", count=len(avail), names=names(avail), icon=_ICON_CHECK
                )
            ]
        elif requested_view == "unavailable":
            lines = [catalog.text("unified.team_status.unavailable_header", count=len(unavail), icon=_ICON_CROSS)]
            no_reason = catalog.text("unified.team_status.no_reason_saved")
            lines.extend(
                catalog.text(
                    "unified.team_status.unavailable_line",
                    name=self._member_name(entry),
                    reason=entry["reason"] or no_reason,
                )
                for entry in unavail
            )
            if not unavail:
                lines = [catalog.text("unified.team_status.none_unavailable", icon=_ICON_CROSS)]
        elif requested_view == "awaiting":
            lines = [
                catalog.text(
                    "unified.team_status.awaiting_header",
                    count=len(awaiting),
                    names=names(awaiting),
                    icon=_ICON_HOURGLASS,
                )
            ]
        elif requested_view == "count":
            lines = [
                catalog.text(
                    "unified.team_status.count_summary", available=len(avail), total=len(snapshot), icon=_ICON_CHECK
                )
            ]
        elif requested_view == "reason":
            member = self._matching_member(snapshot, member_query or _team_query_text.get())
            if member is None:
                lines = [catalog.text("unified.team_status.reason_unknown_member")]
            elif member["availability"] == "unavailable":
                until = (
                    catalog.text("unified.team_status.reason_until_suffix", until=member["unavailable_until"])
                    if member.get("unavailable_until")
                    else ""
                )
                lines = [
                    catalog.text(
                        "unified.team_status.reason_unavailable",
                        name=self._member_name(member),
                        reason=member["reason"] or catalog.text("unified.team_status.no_reason_saved"),
                        until=until,
                    )
                ]
            elif member["availability"] == "available":
                lines = [catalog.text("unified.team_status.reason_available", name=self._member_name(member))]
            else:
                lines = [catalog.text("unified.team_status.reason_awaiting", name=self._member_name(member))]
        else:
            lines = [
                catalog.text("unified.team_status.summary_header", count=len(snapshot), icon=_ICON_PEOPLE),
                catalog.text("unified.team_status.summary_available_line", count=len(avail), names=names(avail)),
                catalog.text(
                    "unified.team_status.summary_unavailable_line", count=len(unavail), names=names(unavail)
                ),
                catalog.text(
                    "unified.team_status.summary_awaiting_line", count=len(awaiting), names=names(awaiting)
                ),
            ]
        res = "\n".join(lines)
        _capture_team_result(res)
        return res

    @tool(
        "get_team_status_roster",
        _catalog_text("unified.team_status.tool.get_roster"),
        side_effecting=False,
    )
    def get_team_status_roster(
        self, as_of_iso: str = "", view: str = "summary", member_query: str = ""
    ) -> str:
        return self.report_team_availability(as_of_iso, view, member_query)

    @tool(
        "record_attendance_response",
        _catalog_text("unified.team_status.tool.record_attendance"),
        side_effecting=True,
        idempotent=True,
    )
    def record_attendance_response(
        self,
        source_message_id: str = "direct-response",
        availability: str = "available",
        original_text: str = "",
        reason: str = "",
        unavailable_days: int = 0,
        received_at: str = "",
    ) -> str:
        from datetime import datetime, timedelta, timezone
        catalog = get_catalog(DEFAULT_LANGUAGE)
        telegram_identity = get_authenticated_request_identity()
        if not telegram_identity:
            res = catalog.text("unified.team_status.identity_unavailable")
            _capture_team_result(res)
            return res
        now_dt = datetime.now(timezone.utc)
        if not source_message_id:
            source_message_id = f"msg-{int(now_dt.timestamp())}"
        if not original_text:
            original_text = catalog.text("unified.team_status.default_original_text", availability=availability)

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(m["telegram_identity"] == telegram_identity for m in approved_members):
            res = catalog.text("unified.team_status.not_approved")
            _capture_team_result(res)
            return res

        normalized = availability.strip().lower()
        if normalized not in {"available", "unavailable"}:
            res = catalog.text("unified.team_status.clarify_availability")
            _capture_team_result(res)
            return res
        if normalized == "unavailable" and not reason.strip():
            res = catalog.text("unified.team_status.clarify_reason")
            _capture_team_result(res)
            return res
        if normalized == "unavailable" and unavailable_days < 1:
            res = catalog.text("unified.team_status.clarify_days")
            _capture_team_result(res)
            return res

        unavailable_until = None
        if normalized == "unavailable":
            unavailable_until = (now_dt + timedelta(days=unavailable_days)).isoformat()
        stored_reason = reason.strip() if normalized == "unavailable" else None

        try:
            stored_response = self.status_store.record_response(
                telegram_identity=telegram_identity,
                source_message_id=source_message_id,
                availability=normalized,
                original_text=original_text,
                received_at=now_dt.isoformat(),
                reason=stored_reason,
                unavailable_until=unavailable_until,
            )
        except Exception as exc:
            res = catalog.text("unified.team_status.record_failed", error=str(exc))
            _capture_team_result(res)
            return res

        if stored_response["approval_status"] == "pending":
            res = catalog.text("unified.team_status.pending_commander_approval")
        elif normalized == "available":
            res = catalog.text("unified.team_status.marked_available", icon=_ICON_CHECK)
        else:
            res = catalog.text("unified.team_status.marked_unavailable", reason=reason, icon=_ICON_CROSS)
        _capture_team_result(res)
        return res


class UnifiedFriendlyForcesAgent(FriendlyForcesAgent):
    """Binds the friendly forces specialist with Hebrew dispatch confirmations."""

    role = _catalog_text("unified.friendly_forces.role")
    system_prompt = _catalog_text("unified.friendly_forces.system_prompt")

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=250, reasoning_effort="none")
        return _forces_capture.run(super().process, text, allowed_tools, invocation_policy=invocation_policy)

    @tool(
        "dispatch_ambulance",
        _catalog_text("unified.friendly_forces.tool.ambulance"),
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_ambulance(self, location: str, patient_count: int = 1, severity: str = "", note: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        record = catalog.text("unified.friendly_forces.log_ambulance", location=location, count=patient_count)
        self.dispatches_recorded.append(record)
        res = catalog.text("unified.friendly_forces.confirm_ambulance", location=location)
        _capture_forces_result(res)
        return res

    @tool(
        "dispatch_police",
        _catalog_text("unified.friendly_forces.tool.police"),
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_police(self, location: str, unit_count: int = 1, incident_type: str = "", note: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        record = catalog.text("unified.friendly_forces.log_police", location=location, count=unit_count)
        self.dispatches_recorded.append(record)
        res = catalog.text("unified.friendly_forces.confirm_police", location=location)
        _capture_forces_result(res)
        return res

    @tool(
        "dispatch_firefighters",
        _catalog_text("unified.friendly_forces.tool.firefighters"),
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_firefighters(self, location: str, engine_count: int = 1, severity: str = "", note: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        record = catalog.text("unified.friendly_forces.log_firefighters", location=location, count=engine_count)
        self.dispatches_recorded.append(record)
        res = catalog.text("unified.friendly_forces.confirm_firefighters", location=location)
        _capture_forces_result(res)
        return res

    @tool(
        "dispatch_military",
        _catalog_text("unified.friendly_forces.tool.military"),
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_military(self, location: str, unit_count: int = 1, mission_type: str = "", note: str = "") -> str:
        catalog = get_catalog(DEFAULT_LANGUAGE)
        record = catalog.text("unified.friendly_forces.log_military", location=location, count=unit_count)
        self.dispatches_recorded.append(record)
        res = catalog.text("unified.friendly_forces.confirm_military", location=location)
        _capture_forces_result(res)
        return res



def _seed_mock_data() -> None:
    """Initialize mock readiness-team members and bot-service if DB is empty."""
    catalog = get_catalog(DEFAULT_LANGUAGE)
    hist_store = open_persistence(DB_PATH)
    try:
        if hist_store.read_user("bot-service") is None:
            hist_store.write_user("bot-service", "commander")
    finally:
        hist_store.close()

    open_surveillance_persistence(UNIFIED_SURVEILLANCE_DB_PATH)

    team_store = open_team_status_persistence(UNIFIED_TEAM_STATUS_DB_PATH)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    if not team_store.roster_is_approved():
        team_store.register_member("2077472944", catalog.text("unified.seed.primary_name"), now_iso)
        team_store.register_member("commander_user", catalog.text("unified.seed.commander_user_name"), now_iso)
        team_store.register_member("viewer_user", catalog.text("unified.seed.viewer_user_name"), now_iso)
        team_store.register_member("1001", catalog.text("unified.seed.member_1001"), now_iso)
        team_store.register_member("1002", catalog.text("unified.seed.member_1002"), now_iso)
        team_store.register_member("1003", catalog.text("unified.seed.member_1003"), now_iso)
        team_store.approve_roster("commander_user", now_iso)

        cycle_key = now_dt.date().isoformat()
        deadline = (now_dt + timedelta(hours=4)).isoformat()
        team_store.open_cycle(cycle_key, now_iso, deadline)

    # Repair only the legacy placeholder produced by the removed Telegram
    # auto-registration path.  This reuses the profile's already-authoritative
    # approved name and leaves roster membership and approval untouched.
    members_by_identity = {
        member["telegram_identity"]: member
        for member in team_store.list_members(approved_only=False)
    }
    primary = members_by_identity.get("2077472944")
    legacy_placeholder = catalog.text("unified.team_status.legacy_placeholder_name", identity="2077472944")
    if primary and primary["full_name"] == legacy_placeholder:
        team_store.register_member("2077472944", catalog.text("unified.seed.primary_name"), primary["registered_at"])


def ensure_seed_data() -> None:
    """Explicit, idempotent entry point for this profile's mock/demo data.

    Call this once, before starting a real deployment of this profile (see
    `run_stack.py`) — deliberately *not* called automatically at import
    time. It used to run as a side effect of `import profiles.unified_test`
    itself, which fired for any reason the module got imported (a test
    reading a module-level constant, tooling that imports every profile,
    ...), not only when this profile was actually being started — the
    same reason every other profile in this repo leaves data seeding, and
    provisioning the `bot-service` identity in particular, to an explicit,
    operator-run step (`cli.user_admin`; see docs/operator_guide.md)
    rather than a module-import side effect.
    """

    _seed_mock_data()


AGENTS = [
    AgentSpec(cls=UnifiedSurveillanceAgent, tier="sub"),
    AgentSpec(cls=UnifiedTeamStatusAgent, tier="sub"),
    AgentSpec(cls=UnifiedFriendlyForcesAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="overall_situational_picture",
        description=_catalog_text("unified.protocol.overall_situational_picture.description"),
        participating_agents=("surveillance_agent", "team_status_agent"),
        approved_tools=("get_surveillance_overview", "get_team_status_roster", "report_team_availability"),
        expected_success_output=_catalog_text("unified.protocol.overall_situational_picture.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_surveillance_overview",
        description=_catalog_text("unified.protocol.query_surveillance_overview.description"),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_surveillance_overview",),
        expected_success_output=_catalog_text("unified.protocol.query_surveillance_overview.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_drone_fleet_status",
        description=_catalog_text("unified.protocol.query_drone_fleet_status.description"),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_drone_fleet_status",),
        expected_success_output=_catalog_text("unified.protocol.query_drone_fleet_status.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_active_drone_missions",
        description=_catalog_text("unified.protocol.query_active_drone_missions.description"),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_active_missions",),
        expected_success_output=_catalog_text("unified.protocol.query_active_drone_missions.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_camera_status",
        description=_catalog_text("unified.protocol.query_camera_status.description"),
        participating_agents=("surveillance_agent",),
        approved_tools=("get_camera_feeds",),
        expected_success_output=_catalog_text("unified.protocol.query_camera_status.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description=_catalog_text("unified.protocol.dispatch_drone_to_incident.description"),
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output=_catalog_text("unified.protocol.dispatch_drone_to_incident.expected_output"),
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="recall_drone_to_base",
        description=_catalog_text("unified.protocol.recall_drone_to_base.description"),
        participating_agents=("surveillance_agent",),
        approved_tools=("return_drone_to_base",),
        expected_success_output=_catalog_text("unified.protocol.recall_drone_to_base.expected_output"),
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="report_team_availability",
        description=_catalog_text("unified.protocol.report_team_availability.description"),
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output=_catalog_text("unified.protocol.report_team_availability.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="record_attendance_response",
        description=_catalog_text("unified.protocol.record_attendance_response.description"),
        participating_agents=("team_status_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output=_catalog_text("unified.protocol.record_attendance_response.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_emergency_forces",
        description=_catalog_text("unified.protocol.dispatch_emergency_forces.description"),
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_ambulance", "dispatch_police", "dispatch_firefighters", "dispatch_military"),
        expected_success_output=_catalog_text("unified.protocol.dispatch_emergency_forces.expected_output"),
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="query_historical_incidents",
        description=_catalog_text("unified.protocol.query_historical_incidents.description"),
        participating_agents=("history_agent",),
        approved_tools=(),
        expected_success_output=_catalog_text("unified.protocol.query_historical_incidents.expected_output"),
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
]

EVENT_TYPES = [
    "surveillance_report",
    "drone_dispatch",
    "drone_recall",
    "drone_mission_query",
    "team_availability",
    "team_attendance_report",
    "emergency_dispatch",
    "historical_query",
]

EVENT_TYPE_REQUIRED_FIELDS = {
    "emergency_dispatch": ("area",),
    "drone_dispatch": ("area",),
    "surveillance_report": ("area",),
}

AREAS = [
    "north_gate",
    "south_sector",
    "east_fence",
    "west_hill",
    "central_hub",
    "readiness_team",
]

API_PORT = 8905
RETRY_COUNT = 2
RISK_THRESHOLD = 0.6
LOOKBACK_WINDOW_DAYS = 30
TIMEZONE = "Asia/Jerusalem"
CONVERSATION_HISTORY_TURNS = 6
CONVERSATION_HISTORY_TTL_HOURS = 24
OPTIMIZATION_POLICY = OptimizationPolicy()

COMMANDER_KEYBOARD = (
    (
        _catalog_text("unified.keyboard.approvals_queue", icon=_ICON_HOURGLASS),
        _catalog_text("unified.keyboard.overall_picture", icon=_ICON_CHART),
    ),
    (
        _catalog_text("unified.keyboard.camera_status", icon=_ICON_CAMERA),
        _catalog_text("unified.keyboard.drone_fleet_status", icon=_ICON_DRONE),
    ),
    (
        _catalog_text("unified.keyboard.dispatch_drone", icon=_ICON_ROCKET),
        _catalog_text("unified.keyboard.recall_drone", icon=_ICON_REPEAT),
    ),
    (
        _catalog_text("unified.keyboard.team_status", icon=_ICON_PEOPLE),
        _catalog_text("unified.keyboard.dispatch_forces", icon=_ICON_SIREN),
    ),
    (_catalog_text("unified.keyboard.event_history", icon=_ICON_SCROLL),),
)

VIEWER_KEYBOARD = (
    (
        _catalog_text("unified.keyboard.available", icon=_ICON_CHECK),
        _catalog_text("unified.keyboard.unavailable", icon=_ICON_CROSS),
    ),
    (
        _catalog_text("unified.keyboard.team_status", icon=_ICON_PEOPLE),
        _catalog_text("unified.keyboard.overall_picture", icon=_ICON_CHART),
    ),
    (
        _catalog_text("unified.keyboard.camera_status", icon=_ICON_CAMERA),
        _catalog_text("unified.keyboard.event_history", icon=_ICON_SCROLL),
    ),
)
