"""Unified command-and-control profile for readiness team, surveillance, and tactical forces."""

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import uuid

from agents import (
    AgentResult,
    FriendlyForcesAgent,
    InvocationPolicy,
    SurveillanceAgent,
    TeamStatusAgent,
    tool,
)
from persistence import open_persistence, open_surveillance_persistence, open_team_status_persistence
from profiles.contracts import AgentSpec, OptimizationPolicy
from protocols import CriticalityLevel, Protocol
from tools import get_trace_id

PROFILE_NAME = "חמ''ל מבצעי אחוד (Unified Command Hub)"
DEFAULT_LANGUAGE = "he"
MAX_ITER = 6
MODEL_TIMEOUT_SECONDS = 45

_PROFILE_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "unified_test"
_PROFILE_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_PROFILE_DATA_DIR / "unified_history.db")
UNIFIED_SURVEILLANCE_DB_PATH = str(_PROFILE_DATA_DIR / "unified_surveillance.db")
UNIFIED_TEAM_STATUS_DB_PATH = str(_PROFILE_DATA_DIR / "unified_team_status.db")

BOT_TOKEN_ENV = "BOT_TOKEN"
MODEL_CREDENTIAL_ENVS = []


_surv_key: ContextVar[str | None] = ContextVar("unified_surv_key", default=None)
_surv_results: dict[str, str] = {}
_surv_lock = threading.Lock()


def _capture_surv_result(output: str) -> None:
    key = _surv_key.get() or get_trace_id()
    if key:
        with _surv_lock:
            _surv_results[key] = output


_team_key: ContextVar[str | None] = ContextVar("unified_team_key", default=None)
_team_results: dict[str, str] = {}
_team_lock = threading.Lock()


def _capture_team_result(output: str) -> None:
    key = _team_key.get() or get_trace_id()
    if key:
        with _team_lock:
            _team_results[key] = output


_forces_key: ContextVar[str | None] = ContextVar("unified_forces_key", default=None)
_forces_results: dict[str, str] = {}
_forces_lock = threading.Lock()


def _capture_forces_result(output: str) -> None:
    key = _forces_key.get() or get_trace_id()
    if key:
        with _forces_lock:
            _forces_results[key] = output


class UnifiedSurveillanceAgent(SurveillanceAgent):
    """Binds the visual surveillance specialist with Hebrew tactical tools."""

    surveillance_db_path = UNIFIED_SURVEILLANCE_DB_PATH
    role = (
        "אחראי על תצפית חזותית, מערך מצלמות אבטחה, וצי רחפנים טקטיים. "
        "מספק סטטוס רחפנים וסוללות, תמונת מצב מצלמות, ושיגור או החזרת רחפנים."
    )
    system_prompt = (
        "אתה סוכן מומחה לתצפית חזותית ורחפנים. "
        "חובה לענות אך ורק בעברית קצרה, מדויקת ומבצעית (עד 4-5 שורות לכל היותר). "
        "אל תשתמש באנגלית כלל, למעט מזהים מדויקים (כגון CAM-01, DRONE-01). "
        "לשאלות סטטוס של צי רחפנים קרא ל-get_drone_fleet_status. "
        "לשאלות על משימות רחפנים פעילות באוויר קרא ל-get_active_missions. "
        "לשאלות על מצלמות קרא ל-get_camera_feeds. "
        "לתמונת מצב כוללת קרא ל-get_surveillance_overview. "
        "להחזרת רחפן קרא ל-return_drone_to_base. "
        "לשיגור רחפן קרא ל-dispatch_drone_to_area. "
        "היה תמציתי, ישיר ומבצעי."
    )

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=250, reasoning_effort="none")
        key = get_trace_id() or uuid.uuid4().hex
        token = _surv_key.set(key)
        with _surv_lock:
            _surv_results.pop(key, None)
        try:
            model_result = super().process(text, allowed_tools, invocation_policy=invocation_policy)
            with _surv_lock:
                exact = _surv_results.pop(key, None)
            if exact is not None:
                return AgentResult(status="success", text=exact)
            return model_result
        finally:
            with _surv_lock:
                _surv_results.pop(key, None)
            _surv_key.reset(token)

    @tool(
        "get_drone_fleet_status",
        "מחזיר סטטוס תפעולי, רמות סוללה ומיקומים של צי הרחפנים בעברית.",
        side_effecting=False,
    )
    def get_drone_fleet_status(self, status_filter: str = "") -> str:
        cleaned = status_filter.strip().lower()
        if cleaned in {"all", "*"}:
            cleaned = ""
        drones = self.surveillance_store.list_drones(status=cleaned or None)
        if not drones:
            all_drones = self.surveillance_store.list_drones()
            drones = all_drones if all_drones else []
        if not drones:
            res = "לא נמצאו רחפנים במערך."
            _capture_surv_result(res)
            return res
        status_map = {
            "ready": "מוכן לפעולה ✅",
            "in_flight": "באוויר במשימה 🛸",
            "charging": "בטעינה 🔋",
            "maintenance": "בתחזוקה 🛠️",
        }
        lines = [f"🛸 מצב צי רחפנים ({len(drones)} רחפנים):"]
        ready_count = sum(1 for d in drones if d["status"] == "ready")
        flight_count = sum(1 for d in drones if d["status"] == "in_flight")
        charging_count = sum(1 for d in drones if d["status"] == "charging")
        for d in drones:
            st = status_map.get(d["status"], d["status"])
            mission = f" (במשימה: {d['assigned_mission_id']})" if d.get("assigned_mission_id") else ""
            lines.append(
                f"• [{d['drone_id']}] {d['callsign']} ({d['model']}): {st} | סוללה: {d['battery_percent']}% | גזרה: {d['current_area']}{mission}"
            )
        lines.append(f"סיכום: {ready_count} מוכנים לשיגור | {flight_count} באוויר | {charging_count} בטעינה")
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "get_active_missions",
        "מחזיר את כל משימות הרחפנים הפעילות כרגע באוויר בעברית.",
        side_effecting=False,
    )
    def get_active_missions(self) -> str:
        missions = self.surveillance_store.get_active_missions()
        if not missions:
            res = "אין כרגע משימות רחפנים פעילות באוויר."
            _capture_surv_result(res)
            return res
        lines = [f"🛸 משימות רחפנים פעילות באוויר ({len(missions)}):"]
        for m in missions:
            lines.append(
                f"• [{m['mission_id']}] רחפן {m['callsign']} ({m['drone_id']}) -> גזרה: {m['target_area']} "
                f"| סוללה: {m['battery_percent']}% | ETA: {m['eta_seconds']} שנ' | משימה: {m['incident_description']}"
            )
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "get_camera_feeds",
        "מחזיר תמונת מצב וסטטוס של מצלמות האבטחה לפי גזרה או זיהוי מצלמה בעברית.",
        side_effecting=False,
    )
    def get_camera_feeds(self, area: str = "", camera_id: str = "") -> str:
        if camera_id.strip():
            c = self.surveillance_store.get_camera(camera_id.strip())
            cameras = [c] if c else []
        else:
            cameras = self.surveillance_store.list_cameras(area=area.strip() or None)
        if not cameras:
            res = "לא נמצאו מצלמות פעילות בגזרה המבוקשת."
            _capture_surv_result(res)
            return res
        status_map = {"active": "תקין ופעיל ✅", "offline": "לא מקוון ❌", "maintenance": "בתחזוקה 🛠️"}
        lines = [f"📹 מצב מצלמות אבטחה ({len(cameras)} מצלמות):"]
        for c in cameras:
            st = status_map.get(c["status"], c["status"])
            lines.append(
                f"• [{c['camera_id']}] {c['name']} ({c['area']}, {c['azimuth_degrees']}°): {c['feed_summary']} [{st}]"
            )
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "get_surveillance_overview",
        "תמונת מצב תצפיתית ואווירית משולבת: מצלמות, רחפנים ומשימות פעילות בעברית.",
        side_effecting=False,
    )
    def get_surveillance_overview(self, area: str = "") -> str:
        overview = self.surveillance_store.surveillance_overview(area=area.strip() or None)
        lines = [
            "📊 תמונת מצב תצפיתית כוללת:",
            f"• מצלמות אבטחה: {overview['active_camera_count']}/{len(overview['cameras'])} פעילות ותקינות בגזרה.",
            f"• מערך רחפנים: {overview['ready_drone_count']} מוכנים לשיגור, {overview['in_flight_drone_count']} באוויר במשימה.",
        ]
        if overview["active_missions"]:
            lines.append(f"• משימות באוויר ({len(overview['active_missions'])}):")
            for m in overview["active_missions"]:
                lines.append(f"  - רחפן {m['callsign']} לעבר {m['target_area']} (זמן משוער: {m['eta_seconds']} שנ')")
        else:
            lines.append("• משימות באוויר: אין משימות אוויריות פעילות כרגע.")
        res = "\n".join(lines)
        _capture_surv_result(res)
        return res

    @tool(
        "return_drone_to_base",
        "החזרת רחפן פעיל לבסיס בצורה מבוקרת ובטוחה בעברית.",
        side_effecting=True,
        idempotent=True,
    )
    def return_drone_to_base(self, drone_or_mission_id: str = "") -> str:
        active = self.surveillance_store.get_active_missions()
        if not active:
            res = "אין כרגע רחפנים פעילים באוויר להחזרה."
            _capture_surv_result(res)
            return res
        requested = drone_or_mission_id.strip()
        normalized = requested.casefold()
        if not requested or normalized in {"all", "all drones", "כולם", "כולן", "כל הרחפנים"} or "כולם" in normalized or "כל הרחפ" in normalized:
            self.surveillance_store.recall_all_drones()
            res = f"פקודת החזרה התקבלה: כל הרחפנים הפעילים ({len(active)}) חוזרים כעת לבסיס לנחיתה ✅."
        else:
            try:
                mission = self.surveillance_store.recall_drone(requested)
                res = f"פקודת החזרה התקבלה: רחפן {mission['callsign']} ({mission['drone_id']}) חוזר כעת לבסיס לנחיתה ✅."
            except Exception as exc:
                res = f"החזרת הרחפן נכשלה: {exc}"
        _capture_surv_result(res)
        return res

    @tool(
        "dispatch_drone_to_area",
        "שיגור רחפן טקטי לאירוע או גזרה לצורך תצפית או סיור.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_drone_to_area(
        self,
        target_area: str,
        incident_description: str = "סיור ותצפית מבצעית",
        mission_type: str = "recon",
        specific_drone_id: str = "",
        dispatched_by: str = "commander",
    ) -> str:
        if not target_area.strip():
            res = "נדרש לציין גזרת יעד לשיגור הרחפן."
            _capture_surv_result(res)
            return res
        if not incident_description.strip():
            incident_description = "סיור ותצפית מבצעית"
        try:
            mission = self.surveillance_store.dispatch_drone(
                target_area=target_area.strip(),
                incident_description=incident_description.strip(),
                mission_type=mission_type.strip() or "recon",
                dispatched_by=dispatched_by.strip() or "commander",
                specific_drone_id=specific_drone_id.strip() or None,
            )
        except Exception as exc:
            res = f"שיגור הרחפן נכשל: {exc}"
            _capture_surv_result(res)
            return res
        d = mission["drone"]
        res = (
            f"הזנקת רחפן הושלמה בהצלחה ✅\n"
            f"• רחפן: {d['callsign']} ({d['drone_id']})\n"
            f"• גזרת יעד: {mission['target_area']}\n"
            f"• זמן הגעה משוער (ETA): כ-{mission['eta_seconds']} שניות\n"
            f"• סוללה: {d['battery_percent']}% | מזהה משימה: {mission['mission_id']}"
        )
        _capture_surv_result(res)
        return res

    @tool(
        "update_camera_observation",
        "עדכון תצפית ידנית או סטטוס של מצלמת אבטחה בעברית.",
        side_effecting=True,
        idempotent=True,
    )
    def update_camera_observation(self, camera_id: str, observation_note: str, status: str = "") -> str:
        if not camera_id.strip():
            res = "נדרש מזהה מצלמה לעדכון תצפית."
            _capture_surv_result(res)
            return res
        try:
            cam = self.surveillance_store.update_camera_feed(
                camera_id.strip(),
                feed_summary=observation_note.strip() or None,
                status=status.strip().lower() or None,
            )
            res = f"תצפית מצלמה {cam['camera_id']} ({cam['name']}) עודכנה בהצלחה ✅: {cam['feed_summary']}"
        except Exception as exc:
            res = f"עדכון תצפית המצלמה נכשל: {exc}"
        _capture_surv_result(res)
        return res


class UnifiedTeamStatusAgent(TeamStatusAgent):
    """Binds the readiness-team status specialist with Hebrew reporting tools."""

    status_db_path = UNIFIED_TEAM_STATUS_DB_PATH
    timezone_name = "Asia/Jerusalem"
    attendance_check_hour = 8
    response_window_hours = 1
    role = (
        "אחראי על ניהול מצבת ונוכחות כיתת כוננות. "
        "מספק דוחות זמינות (מי זמין/לא זמין), וקולט דיווחי נוכחות של חברי הכיתה."
    )
    system_prompt = (
        "אתה סוכן מומחה לניהול וסטטוס כיתת כוננות. "
        "חובה לענות אך ורק בעברית קצרה, מדויקת ומבצעית (עד 4-5 שורות לכל היותר). "
        "אל תשתמש באנגלית כלל. "
        "לשאלות על סטטוס הנוכחות של כיתת הכוננות קרא ל-report_team_availability. "
        "לרישום דיווח נוכחות קרא ל-record_attendance_response. "
        "היה תמציתי וברור."
    )

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=250, reasoning_effort="none")
        key = get_trace_id() or uuid.uuid4().hex
        token = _team_key.set(key)
        with _team_lock:
            _team_results.pop(key, None)
        try:
            model_result = super().process(text, allowed_tools, invocation_policy=invocation_policy)
            with _team_lock:
                exact = _team_results.pop(key, None)
            if exact is not None:
                return AgentResult(status="success", text=exact)
            return model_result
        finally:
            with _team_lock:
                _team_results.pop(key, None)
            _team_key.reset(token)

    @tool(
        "report_team_availability",
        "מחזיר דו\"ח נוכחות מפורט ותמציתי של כיתת הכוננות למחזור הנוכחי בעברית.",
        side_effecting=False,
    )
    def report_team_availability(self, as_of_iso: str = "") -> str:
        from datetime import datetime, timezone
        now_iso = as_of_iso or datetime.now(timezone.utc).isoformat()
        snapshot = self.status_store.availability_snapshot(now_iso)
        avail = [e for e in snapshot if e["availability"] == "available"]
        unavail = [e for e in snapshot if e["availability"] == "unavailable"]
        pending = [e for e in snapshot if e["availability"] == "pending"]
        lines = [
            f"👥 סטטוס כיתת כוננות (סה\"כ {len(snapshot)} לוחמים):",
            f"• זמינים לפעילות ({len(avail)}): {', '.join(e['full_name'] for e in avail) if avail else 'אין כרגע'}",
        ]
        if unavail:
            lines.append(f"• אינם זמינים ({len(unavail)}):")
            for e in unavail:
                reason = f" ({e['reason']})" if e.get("reason") else ""
                lines.append(f"  - {e['full_name']}{reason}")
        if pending:
            lines.append(f"• טרם דיווחו ({len(pending)}): {', '.join(e['full_name'] for e in pending)}")
        res = "\n".join(lines)
        _capture_team_result(res)
        return res

    @tool(
        "record_attendance_response",
        "רישום תגובת נוכחות של לוחם כיתת כוננות בעברית.",
        side_effecting=True,
        idempotent=True,
    )
    def record_attendance_response(
        self,
        telegram_identity: str,
        source_message_id: str = "direct-response",
        availability: str = "available",
        original_text: str = "",
        reason: str = "",
        unavailable_days: int = 0,
        received_at: str = "",
    ) -> str:
        from datetime import datetime, timedelta, timezone
        now_dt = datetime.now(timezone.utc)
        if not source_message_id:
            source_message_id = f"msg-{int(now_dt.timestamp())}"
        if not original_text:
            original_text = f"דיווח זמינות: {availability}"

        # Ensure member is registered and approved in roster
        try:
            members = self.status_store.list_members(approved_only=False)
            if not any(m["telegram_identity"] == telegram_identity for m in members):
                self.status_store.register_member(telegram_identity, f"חבר כיתת כוננות ({telegram_identity})", now_dt.isoformat())
                self.status_store.approve_roster("system", now_dt.isoformat())
        except Exception:
            pass

        normalized = availability.strip().lower()
        if normalized not in {"available", "unavailable"}:
            res = "הבהרה נדרשת: ציין האם אתה זמין או לא זמין."
            _capture_team_result(res)
            return res
        if normalized == "unavailable" and not reason.strip():
            res = "הבהרה נדרשת: לוחם שאינו זמין נדרש לספק סיבה."
            _capture_team_result(res)
            return res
        if normalized == "unavailable" and unavailable_days < 1:
            res = "הבהרה נדרשת: ציין לכמה ימים אינך זמין."
            _capture_team_result(res)
            return res

        unavailable_until = None
        if normalized == "unavailable":
            unavailable_until = (now_dt + timedelta(days=unavailable_days)).isoformat()

        try:
            self.status_store.record_response(
                telegram_identity=telegram_identity,
                source_message_id=source_message_id,
                availability=normalized,
                original_text=original_text,
                received_at=now_dt.isoformat(),
                reason=reason or None,
                unavailable_until=unavailable_until,
            )
        except Exception as exc:
            res = f"רישום התגובה נכשל: {exc}"
            _capture_team_result(res)
            return res

        heb_status = "זמין לכוננות ✅" if normalized == "available" else f"אינו זמין ({reason}) ❌"
        res = f"דיווח הנוכחות נקלט בהצלחה: {heb_status}."
        _capture_team_result(res)
        return res


class UnifiedFriendlyForcesAgent(FriendlyForcesAgent):
    """Binds the friendly forces specialist with Hebrew dispatch confirmations."""

    role = (
        "אחראי על תיאום והזנקת כוחות ביטחון וחירום (משטרה, מד\"א, כיבוי אש, צבא)."
    )
    system_prompt = (
        "אתה סוכן מומחה לתיאום והזנקת כוחות ביטחון וחירום (משטרה, מד\"א, כיבוי אש, צבא). "
        "חובה לענות אך ורק בעברית קצרה ומדויקת (עד 3 שורות). "
        "אל תשתמש באנגלית כלל. דווח תמיד איזה כוח הוזנק ולאיזה יעד בדיוק."
    )

    def process(
        self, text: str, allowed_tools: list[str], *, invocation_policy: InvocationPolicy | None = None
    ) -> AgentResult:
        if invocation_policy is None:
            invocation_policy = InvocationPolicy(max_output_tokens=250, reasoning_effort="none")
        key = get_trace_id() or uuid.uuid4().hex
        token = _forces_key.set(key)
        with _forces_lock:
            _forces_results.pop(key, None)
        try:
            model_result = super().process(text, allowed_tools, invocation_policy=invocation_policy)
            with _forces_lock:
                exact = _forces_results.pop(key, None)
            if exact is not None:
                return AgentResult(status="success", text=exact)
            return model_result
        finally:
            with _forces_lock:
                _forces_results.pop(key, None)
            _forces_key.reset(token)

    @tool(
        "dispatch_ambulance",
        "רישום הזנקת כוחות רפואה / מד\"א ליעד מבוקש.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_ambulance(self, location: str, patient_count: int = 1, severity: str = "", note: str = "") -> str:
        record = f"הוזנק מד\"א ל-'{location}': נפגעים={patient_count}"
        self.dispatches_recorded.append(record)
        res = f"נרשמה בהצלחה הזנקת צוות רפואה/מד\"א ליעד '{location}'."
        _capture_forces_result(res)
        return res

    @tool(
        "dispatch_police",
        "רישום הזנקת כוחות משטרה ליעד מבוקש.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_police(self, location: str, unit_count: int = 1, incident_type: str = "", note: str = "") -> str:
        record = f"הוזנקה משטרה ל-'{location}': כוחות={unit_count}"
        self.dispatches_recorded.append(record)
        res = f"נרשמה בהצלחה הזנקת כוחות משטרה ליעד '{location}'."
        _capture_forces_result(res)
        return res

    @tool(
        "dispatch_firefighters",
        "רישום הזנקת כוחות כיבוי והצלה ליעד מבוקש.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_firefighters(self, location: str, engine_count: int = 1, severity: str = "", note: str = "") -> str:
        record = f"הוזנק כיבוי אש ל-'{location}': רכבים={engine_count}"
        self.dispatches_recorded.append(record)
        res = f"נרשמה בהצלחה הזנקת כוחות כיבוי והצלה ליעד '{location}'."
        _capture_forces_result(res)
        return res

    @tool(
        "dispatch_military",
        "רישום הזנקת כוחות צבא וביטחון ליעד מבוקש.",
        side_effecting=True,
        idempotent=False,
    )
    def dispatch_military(self, location: str, unit_count: int = 1, mission_type: str = "", note: str = "") -> str:
        record = f"הוזנק כוח צבאי ל-'{location}': כוחות={unit_count}"
        self.dispatches_recorded.append(record)
        res = f"נרשמה בהצלחה הזנקת כוחות צבא וביטחון ליעד '{location}'."
        _capture_forces_result(res)
        return res



def _seed_mock_data() -> None:
    """Initialize mock readiness-team members and bot-service if DB is empty."""
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
        team_store.register_member("2077472944", "מפקד / משתמש ראשי", now_iso)
        team_store.register_member("commander_user", "מפקד כיתת כוננות", now_iso)
        team_store.register_member("viewer_user", "לוחם כיתת כוננות", now_iso)
        team_store.register_member("1001", "דן לוי", now_iso)
        team_store.register_member("1002", "יוסי כהן", now_iso)
        team_store.register_member("1003", "מיכל אברהם", now_iso)
        team_store.approve_roster("commander_user", now_iso)

        cycle_key = now_dt.date().isoformat()
        deadline = (now_dt + timedelta(hours=4)).isoformat()
        team_store.open_cycle(cycle_key, now_iso, deadline)



# Run initial seed
_seed_mock_data()

AGENTS = [
    AgentSpec(cls=UnifiedSurveillanceAgent, tier="sub"),
    AgentSpec(cls=UnifiedTeamStatusAgent, tier="sub"),
    AgentSpec(cls=UnifiedFriendlyForcesAgent, tier="sub"),
]

PROTOCOLS = [
    Protocol(
        name="query_surveillance_overview",
        description="תמונת מצב תצפיתית כוללת: סטטוס מצלמות, רחפנים ומשימות אוויריות פעילות בכל הגזרות.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_surveillance_overview",),
        expected_success_output="תמונת מצב טקטית מרוכזת של מערך התצפית והרחפנים.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_drone_fleet_status",
        description="בירור מצב צי הרחפנים: זמינות, רמות סוללה, מיקומים וסטטוס מבצעי של כל הרחפנים.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_drone_fleet_status",),
        expected_success_output="דוח מפורט של מצב הרחפנים, סוללות וזמינות לשיגור.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_active_drone_missions",
        description="בירור משימות רחפנים פעילות באוויר: יעדים, זמני הגעה משוערים, רמות סוללה ומשימות.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_active_missions",),
        expected_success_output="דוח משימות רחפנים פעילות באוויר בעברית.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="query_camera_status",
        description="בדיקת סטטוס ותמונת מצב של מצלמות אבטחה לפי גזרה או מצלמה ספציפית.",
        participating_agents=("surveillance_agent",),
        approved_tools=("get_camera_feeds",),
        expected_success_output="דוח תצפית של מצלמות האבטחה בגזרה המבוקשת.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_drone_to_incident",
        description="שיגור רחפן טקטי לאירוע או גזרה לצורך תצפית או סיור. פעולת מפקד בלבד הדורשת אישור.",
        participating_agents=("surveillance_agent",),
        approved_tools=("dispatch_drone_to_area",),
        expected_success_output="אישור שיגור רחפן לגזרה כולל אות קריאה וזמן הגעה משוער.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="recall_drone_to_base",
        description="החזרת רחפן פעיל לבסיס וסגירת משימה אווירית. פעולת מפקד בלבד הדורשת אישור.",
        participating_agents=("surveillance_agent",),
        approved_tools=("return_drone_to_base",),
        expected_success_output="אישור החזרת הרחפן לבסיס ועדכון סטטוס המשימה והצי.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="report_team_availability",
        description="דוח מצבת נוכחות וזמינות כיתת כוננות: מי זמין, מי לא זמין, סיבות, ומי שטרם דיווח.",
        participating_agents=("team_status_agent",),
        approved_tools=("report_team_availability",),
        expected_success_output="תמונת מצב שמית מפורטת של כיתת הכוננות.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="record_attendance_response",
        description="הזנת דיווח נוכחות של חבר כיתת כוננות: סטטוס זמין או לא זמין עם סיבה.",
        participating_agents=("team_status_agent",),
        approved_tools=("record_attendance_response",),
        expected_success_output="אישור קליטת דיווח הנוכחות של חבר הכיתה.",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
        requires_confirmation=False,
        commander_only=False,
    ),
    Protocol(
        name="dispatch_emergency_forces",
        description="הזנקת ותיאום כוחות חירום וביטחון: אמבולנס, משטרה, כיבוי אש, צבא. פעולת מפקד בלבד הדורשת אישור.",
        participating_agents=("friendly_forces_agent",),
        approved_tools=("dispatch_ambulance", "dispatch_police", "dispatch_firefighters", "dispatch_military"),
        expected_success_output="אישור רישום ותיאום הזנקת כוחות החירום ליעד.",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
        requires_confirmation=True,
        commander_only=True,
    ),
    Protocol(
        name="query_historical_incidents",
        description="תחקור אירועים ומשימות קודמות מתוך יומן המבצעים וההיסטוריה.",
        participating_agents=("history_agent",),
        approved_tools=(),
        expected_success_output="סיכום תמציתי ומדויק של אירועי עבר ביומן המבצעי.",
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
    ("📊 תמונת מצב כללית", "📹 מצב מצלמות"),
    ("🛸 מצב צי רחפנים", "🚀 הזנקת רחפן"),
    ("🔄 החזרת רחפן לבסיס", "👥 סטטוס כיתת כוננות"),
    ("🚨 הזנקת כוחות", "📜 היסטוריית אירועים"),
)

VIEWER_KEYBOARD = (
    ("✅ אני זמין לכוננות", "❌ איני זמין"),
    ("👥 סטטוס כיתת כוננות", "📊 תמונת מצב כללית"),
    ("📹 מצב מצלמות", "📜 היסטוריית אירועים"),
)

