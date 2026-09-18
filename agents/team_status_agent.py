"""Readiness-team attendance specialist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re

from agents.contracts import ReportIngestionResult, project_report_facts
from agents.runtime import Agent, get_authenticated_request_identity, tool
from persistence import AttendanceCycle, TeamStatusPersistenceError, open_team_status_persistence


def _aware_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def verified_availability_period(start: str | None, end: str | None) -> tuple[str, str] | None:
    if not start or not end:
        return None
    try:
        start_at = _aware_datetime(start)
        end_at = _aware_datetime(end)
    except ValueError:
        return None
    if end_at <= start_at:
        return None
    return start_at.isoformat(), end_at.isoformat()


def normalize_attendance_availability(value: str | None) -> str | None:
    """Normalize the small, explicit set of attendance status aliases.

    The model-facing contract remains the canonical English enum values.  A
    Hebrew specialist can nevertheless return an exact Hebrew equivalent;
    accepting only these enumerated aliases avoids turning validation into
    fuzzy matching.
    """

    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().casefold().split())
    aliases = {
        "available": "available",
        "unavailable": "unavailable",
        "\u05d6\u05de\u05d9\u05df": "available",
        "\u05d6\u05de\u05d9\u05e0\u05d4": "available",
        "\u05d6\u05de\u05d9\u05e0\u05d9\u05dd": "available",
        "\u05d6\u05de\u05d9\u05e0\u05d5\u05ea": "available",
        "\u05d0\u05e0\u05d9 \u05d6\u05de\u05d9\u05df": "available",
        "\u05d0\u05e0\u05d9 \u05d6\u05de\u05d9\u05e0\u05d4": "available",
        "\u05dc\u05d0 \u05d6\u05de\u05d9\u05df": "unavailable",
        "\u05dc\u05d0 \u05d6\u05de\u05d9\u05e0\u05d4": "unavailable",
        "\u05dc\u05d0 \u05d6\u05de\u05d9\u05e0\u05d9\u05dd": "unavailable",
        "\u05dc\u05d0 \u05d6\u05de\u05d9\u05e0\u05d5\u05ea": "unavailable",
        "\u05d0\u05d9\u05e0\u05d5 \u05d6\u05de\u05d9\u05df": "unavailable",
        "\u05d0\u05d9\u05e0\u05d4 \u05d6\u05de\u05d9\u05e0\u05d4": "unavailable",
        "\u05d0\u05d9\u05e0\u05e0\u05d9 \u05d6\u05de\u05d9\u05df": "unavailable",
        "\u05d0\u05d9\u05e0\u05e0\u05d9 \u05d6\u05de\u05d9\u05e0\u05d4": "unavailable",
        "\u05d0\u05e0\u05d9 \u05dc\u05d0 \u05d6\u05de\u05d9\u05df": "unavailable",
        "\u05d0\u05e0\u05d9 \u05dc\u05d0 \u05d6\u05de\u05d9\u05e0\u05d4": "unavailable",
    }
    return aliases.get(normalized)


class TeamStatusAgent(Agent):
    """Specialist used only by readiness-team profiles."""

    name = "team_status_agent"
    owned_report_types = ("team_resource_report", "team_availability", "team_attendance_report")
    default_report_type = "team_attendance_report"
    role = (
        "Maintains the approved readiness-team roster and its current attendance picture. "
        "It opens the daily attendance cycle, records normalized available or unavailable "
        "responses, and returns a name-by-name availability report to the Main Agent."
    )
    system_prompt = (
        "You are the readiness-team status specialist. Work only with members of the approved "
        "roster. A member is either available or unavailable. Unavailable always requires a "
        "reason and may include a duration in days. If a message is unclear or says unavailable "
        "without a reason, ask a short clarification question and do not invent a status. "
        "Responses received after the one-hour window require commander approval. When the Main "
        "Agent asks for the team picture, call report_team_availability and return its complete "
        "name-by-name result without dropping unavailable or missing members."
    )

    status_db_path = ""
    timezone_name = "Asia/Jerusalem"
    attendance_check_hour = 8
    response_window_hours = 1

    def __init__(self, model: str, api_key: str | None = None):
        if not self.status_db_path:
            raise TypeError("TeamStatusAgent requires a class-level status_db_path")
        self.status_store = open_team_status_persistence(self.status_db_path)
        super().__init__(model, api_key)

    def extract_report(self, raw_text: str, *, received_at: str, scenario_time: str | None = None, **_) -> ExtractionResult | None:
        """Extract trusted, typed readiness reports received in the owned group."""
        from history import ExtractionResult
        text = str(raw_text or "")
        normalized = text.casefold()
        if re.search(r"\d+\s*\u05db\u05d1\u05d0\u05d9\u05dd|\u05e1\u05d3[\"\u05f3]?\u05db|manpower|firefighters", normalized):
            count_match = re.search(r"(\d+)\s*(?:\u05db\u05d1\u05d0\u05d9\u05dd|firefighters)", normalized)
            ashed = re.search(r"(?:\u05d0\u05e9\u05d3|ashed)\s*(\d+)", normalized)
            carmel = re.search(r"(?:\u05db\u05e8\u05de\u05dc|carmel)\s*(\d+)", normalized)
            count = int(count_match.group(1)) if count_match else None
            resources = []
            if ashed:
                resources.append({"name": "ASHED", "count": int(ashed.group(1)), "status": "operational"})
            if carmel:
                resources.append({"name": "CARMEL", "count": int(carmel.group(1)), "status": "operational"})
            fields = {"manpower_count": count or 0, "resources_count": len(resources)}
            return ExtractionResult("team_resource_report", "trusted", "readiness_team", tuple(r["name"] for r in resources), text, "low", scenario_time or received_at, False, (), business_fields=fields)
        if re.search(r"\b\d{1,2}:\d{2}\b", text) and re.search(r"(?:\u05d7\u05d5\u05d6\u05e8|\u05dc\u05e6\u05d0\u05ea|\u05de\u05e9\u05de\u05e8\u05ea|return|leave|medical)", normalized):
            return ExtractionResult("team_attendance_report", "trusted", "readiness_team", (), text, "low", scenario_time or received_at, False, ("availability_start", "availability_end"), business_fields={"availability": "unavailable", "reason": "routine medical checkup"})
        return None

    def ingest_report(self, event: dict) -> ReportIngestionResult:
        classification = event.get("classification")
        fields = event.get("business_fields") or {}
        if classification == "team_resource_report":
            count = fields.get("manpower_count")
            if type(count) is not int or count < 0:
                return ReportIngestionResult("rejected", "team resource report has invalid manpower count")
            resources = []
            description = str(event.get("description") or "")
            for name, label in (("ASHED", "\u05d0\u05e9\u05d3"), ("CARMEL", "\u05db\u05e8\u05de\u05dc")):
                match = re.search(rf"{label}\s*(\d+)|{name}\s*(\d+)", description, re.IGNORECASE)
                if match:
                    resources.append({"name": name, "count": int(match.group(1) or match.group(2)), "status": "operational"})
            self.status_store.record_operational_state(
                manpower_count=count, resources=resources, source_event_id=event.get("event_id"),
                received_at=event.get("received_at") or datetime.now(timezone.utc).isoformat(),
                scenario_id=event.get("scenario_id"), scenario_run_id=event.get("scenario_run_id"), scenario_time=event.get("scenario_time"),
            )
            return ReportIngestionResult("committed", "team operational state committed", projection=project_report_facts(event, domain="team", projection_kind="authoritative_state", facts={"manpower_count": count, "resources": resources}))
        if classification != "team_attendance_report":
            return ReportIngestionResult("not_applicable")
        availability = fields.get("availability")
        if availability != "unavailable" or not fields.get("reason"):
            return ReportIngestionResult("rejected", "attendance report requires unavailable status and reason")
        start, end = event.get("availability_start"), event.get("availability_end")
        if not start or not end:
            return ReportIngestionResult("rejected", "attendance report is missing its bounded interval")
        identity = str(event.get("sender_identity") or "")
        cycle = self.status_store.latest_cycle()
        if cycle is None:
            opened = event.get("scenario_time") or event.get("received_at")
            opened_at = _aware_datetime(opened).isoformat()
            self.status_store.open_cycle(opened_at[:10], opened_at, (_aware_datetime(opened) + timedelta(hours=1)).isoformat())
        try:
            response = self.status_store.record_response(
                telegram_identity=identity, source_message_id=str(event.get("source_message_id") or event.get("event_id")),
                availability="unavailable", original_text=str(event.get("raw_text") or event.get("description") or ""),
                received_at=event.get("received_at") or datetime.now(timezone.utc).isoformat(), reason=str(fields["reason"]),
                unavailable_until=end, availability_start=start, availability_end=end,
            )
        except Exception as exc:
            return ReportIngestionResult("rejected", f"attendance report was not accepted: {exc}")
        return ReportIngestionResult("committed", "attendance report committed", projection=project_report_facts(event, domain="team", facts={"availability": response["availability"], "availability_start": start, "availability_end": end, "reason": fields["reason"]}))

    def register_member(self, telegram_identity: str, full_name: str, registered_at: str | None = None) -> None:
        """Register one name/Telegram-ID pair before whole-roster approval."""

        self.status_store.register_member(telegram_identity, full_name, registered_at)

    def approve_roster(self, commander_identity: str, approved_at: str | None = None) -> int:
        """Approve every currently registered member in one commander action."""

        return self.status_store.approve_roster(commander_identity, approved_at)

    def review_late_response(
        self,
        response_id: str,
        *,
        approved: bool,
        commander_identity: str,
        reviewed_at: str | None = None,
    ) -> dict:
        """Accept or reject a late response; only the system's commander path may call this."""

        return self.status_store.review_late_response(
            response_id,
            approved=approved,
            reviewed_by=commander_identity,
            reviewed_at=reviewed_at,
        )

    def attendance_check_due(self, now_iso: str | None = None) -> bool:
        """True once after 08:00 Israel time for each local calendar day."""

        now = _aware_datetime(now_iso).astimezone(ZoneInfo(self.timezone_name))
        if now.hour < self.attendance_check_hour:
            return False
        latest = self.status_store.latest_cycle()
        return latest is None or latest["cycle_key"] != now.date().isoformat()

    def run_scheduled_attendance_check(self, now_iso: str | None = None) -> str | None:
        """System scheduler hook: open one due cycle and return its outbound text."""

        if not self.status_store.roster_is_approved() or not self.attendance_check_due(now_iso):
            return None
        return self.start_daily_attendance_check(now_iso or "")

    def open_scheduled_cycle(self, now_iso: str | None = None, *, force: bool = False) -> dict | None:
        """Open today's cycle if it is due (or `force`d) and return its structured facts.

        Returns None when the roster is not yet approved, when the check is not
        due yet, or when today's cycle is already open — so a caller polling this
        on a timer opens each day's cycle exactly once. The returned dict
        (`cycle_key`, `opened_at`, `deadline_at`, `members_required`) carries no
        user-facing text: the transport renders the prompt from its own catalog."""

        if not self.status_store.roster_is_approved():
            return None
        if not force and not self.attendance_check_due(now_iso):
            return None
        cycle, requested = self._open_cycle(now_iso or "")
        if not cycle.created:
            return None
        return {
            "cycle_key": cycle.cycle_key,
            "opened_at": cycle.opened_at,
            "deadline_at": cycle.deadline_at,
            "members_required": requested,
        }

    def _open_cycle(self, now_iso: str) -> tuple[AttendanceCycle, list[str]]:
        now = _aware_datetime(now_iso or None)
        local_now = now.astimezone(ZoneInfo(self.timezone_name))
        deadline = now + timedelta(hours=self.response_window_hours)
        cycle = self.status_store.open_cycle(
            local_now.date().isoformat(),
            now.isoformat(),
            deadline.isoformat(),
        )
        snapshot = self.status_store.availability_snapshot(now.isoformat())
        requested = [entry["full_name"] for entry in snapshot if entry["availability"] != "unavailable"]
        return cycle, requested

    @tool(
        "start_daily_attendance_check",
        "Opens today's one-hour readiness-team attendance window and returns the exact group message for the system Telegram transport to send.",
        side_effecting=True,
        idempotent=True,
    )
    def start_daily_attendance_check(self, now_iso: str = "") -> str:
        cycle, requested = self._open_cycle(now_iso)
        if not cycle.created:
            return f"The attendance cycle for {cycle.cycle_key} is already open."
        if not requested:
            return "The daily attendance check is open. No members need to report today."
        names = "\n".join(f"- {name}" for name in requested)
        return (
            "Daily readiness-team attendance check. Reply within one hour with your availability. "
            "If you are unavailable, include the reason and number of days.\n\n"
            f"Members required to report:\n{names}"
        )

    @tool(
        "record_attendance_response",
        "Stores one approved-roster member's attendance response. The availability argument must be exactly 'available' or 'unavailable'; unavailable requires a reason. Trusted source_message_id, original_text, received_at, availability_start, and availability_end are supplied by the event runtime; do not infer or provide them. Late responses remain pending until a commander reviews them.",
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
        availability_start: str = "",
        availability_end: str = "",
    ) -> str:
        telegram_identity = get_authenticated_request_identity()
        if not telegram_identity:
            return "The attendance response was not stored: authenticated requester identity is unavailable."
        now = _aware_datetime(received_at or None)
        if not source_message_id:
            source_message_id = f"msg-{int(now.timestamp())}"
        if not original_text:
            original_text = f"availability report: {availability}"

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(m["telegram_identity"] == telegram_identity for m in approved_members):
            return "The attendance response was not stored: requester is not an approved roster member."

        normalized = normalize_attendance_availability(availability)
        if normalized is None:
            return "Clarification required: specify whether the member is available or unavailable."
        clean_reason = reason.strip() if isinstance(reason, str) else ""
        if normalized == "unavailable" and not clean_reason:
            return "Clarification required: an unavailable member must provide a reason."
        period = verified_availability_period(availability_start, availability_end) if normalized == "unavailable" else None
        if period is None and normalized == "unavailable" and type(unavailable_days) is int and unavailable_days > 0:
            # Direct callers predating the trusted event-time contract supplied
            # only a day count. Preserve that public call shape, but normalize
            # it immediately into the absolute storage contract.
            period = (now.isoformat(), (now + timedelta(days=unavailable_days)).isoformat())
        if normalized == "unavailable" and period is None:
            return "Clarification required: specify how many days the member will be unavailable."

        availability_start_value = period[0] if period is not None else None
        availability_end_value = period[1] if period is not None else None
        unavailable_until = availability_end_value

        try:
            response = self.status_store.record_response(
                telegram_identity=telegram_identity,
                source_message_id=source_message_id,
                availability=normalized,
                original_text=original_text,
                received_at=now.isoformat(),
                reason=clean_reason or None,
                unavailable_until=unavailable_until,
                availability_start=availability_start_value,
                availability_end=availability_end_value,
            )
        except TeamStatusPersistenceError as exc:
            return f"The attendance response was not stored: {exc}"

        if response["approval_status"] == "pending":
            return f"The late response is pending commander approval. Response ID: {response['response_id']}"
        return "The attendance response was stored."

    @tool(
        "report_team_availability",
        "Returns the approved readiness-team roster with every member's current availability, reason, return time, original response, and report time.",
        side_effecting=False,
    )
    def report_team_availability(self, as_of_iso: str = "") -> str:
        now = _aware_datetime(as_of_iso or None)
        snapshot = self.status_store.availability_snapshot(now.isoformat())
        if not snapshot:
            return "The readiness-team roster is empty or has not been approved."

        labels = {
            "available": "available",
            "unavailable": "unavailable",
            "awaiting_response": "awaiting response",
        }
        lines = ["Readiness-team status:"]
        counts = {"available": 0, "unavailable": 0, "awaiting_response": 0}
        for entry in snapshot:
            status = entry["availability"]
            counts[status] += 1
            detail = ""
            if status == "unavailable":
                detail = f" — reason: {entry['reason']}; unavailable until: {entry['unavailable_until']}"
            if entry["original_text"]:
                detail += f"; original response: {entry['original_text']}; received at: {entry['received_at']}"
            lines.append(f"- {entry['full_name']}: {labels[status]}{detail}")

        lines.extend(
            (
                "",
                f"Total: {len(snapshot)}",
                f"Available: {counts['available']}",
                f"Unavailable: {counts['unavailable']}",
                f"Awaiting response: {counts['awaiting_response']}",
            )
        )
        return "\n".join(lines)
