"""Readiness-team attendance specialist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re

from agents.contracts import ReportIngestionResult, project_report_facts
from agents.runtime import Agent, get_authenticated_request_identity, get_trusted_operational_scope, tool
from messages import get_catalog
from persistence import AttendanceCycle, OperationalScope, TeamStatusPersistenceError, current_operational_scope, open_team_status_persistence, operational_now, operational_time_of_event, runtime_now, scope_from_event


# Domain vocabulary for the trusted group-owned extraction path — how a
# readiness team states a headcount, a vehicle count or an absence, in either
# language. No persona, scenario or fixture sentence appears here.
_MANPOWER_COUNT = re.compile(
    get_catalog("en").text("extraction.team_status.manpower_count"),
    re.IGNORECASE,
)

_RESOURCE_VOCABULARY = (
    ("ASHED", re.compile(get_catalog("en").text("extraction.team_status.ashed"), re.IGNORECASE)),
    ("CARMEL", re.compile(get_catalog("en").text("extraction.team_status.carmel"), re.IGNORECASE)),
)

# An absence is committed only when the reporter states why. The reason is a
# closed operational category read from the message, never a default.
_ABSENCE_REASONS = (
    (re.compile(get_catalog("en").text("extraction.team_status.medical_check"), re.IGNORECASE), "medical checkup"),
    (re.compile(get_catalog("en").text("extraction.team_status.reserve_duty"), re.IGNORECASE), "reserve duty"),
    (re.compile(get_catalog("en").text("extraction.team_status.illness"), re.IGNORECASE), "illness"),
    (re.compile(get_catalog("en").text("extraction.team_status.leave"), re.IGNORECASE), "leave"),
)


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
    owned_report_types = (
        "team_resource_report",
        "team_operational_report",
        "team_availability",
        "team_attendance_report",
    )
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

    def ensure_operational_scope(self, scope: OperationalScope, baseline=None) -> None:
        self.status_store.ensure_scope(scope, baseline=baseline)

    def _operational_scope(self) -> OperationalScope:
        return get_trusted_operational_scope() or current_operational_scope()

    def extract_report(self, raw_text: str, *, received_at: str, scenario_time: str | None = None, **_) -> ExtractionResult | None:
        """Extract trusted, typed readiness reports received in the owned group.

        A headcount that the message does not state is never substituted with a
        number, and an absence reason that the message does not give is never
        supplied: both would become authoritative state nobody reported.
        """

        from history import ExtractionResult
        text = str(raw_text or "")
        occurrence = scenario_time or received_at

        resource_report = self._resource_fields(text)
        if resource_report is not None:
            fields, resources = resource_report
            return ExtractionResult(
                "team_resource_report", "trusted", "readiness_team",
                tuple(resource["name"] for resource in resources), text, "low", occurrence, False, (),
                business_fields=fields,
            )

        reason = self._absence_reason_in(text)
        if reason is not None:
            return ExtractionResult(
                "team_attendance_report", "trusted", "readiness_team", (), text, "low", occurrence, False,
                ("availability_start", "availability_end"),
                business_fields={"availability": "unavailable", "reason": reason},
            )
        return None

    @staticmethod
    def _resource_fields(text: str):
        """A headcount, a vehicle count, or nothing \u2014 never a substituted zero."""

        count_match = _MANPOWER_COUNT.search(text)
        resources = [
            {"name": name, "count": int(match.group(1)), "status": "operational"}
            for name, pattern in _RESOURCE_VOCABULARY
            for match in [pattern.search(text)]
            if match
        ]
        if count_match is None and not resources:
            return None

        fields = {"resources_count": len(resources)}
        if count_match is not None:
            fields["manpower_count"] = int(count_match.group(1))
        return fields, resources

    @staticmethod
    def _absence_reason_in(text: str) -> str | None:
        for pattern, reason in _ABSENCE_REASONS:
            if pattern.search(text):
                return reason
        return None

    def ingest_report(self, event: dict, *, scope: OperationalScope | None = None) -> ReportIngestionResult:
        scope = scope or scope_from_event(event)
        self.ensure_operational_scope(scope)
        classification = event.get("classification")
        fields = event.get("business_fields") or {}
        if classification == "team_resource_report":
            count = fields.get("manpower_count")
            if count is not None and (type(count) is not int or count < 0):
                return ReportIngestionResult("rejected", "team resource report has invalid manpower count")
            description = str(event.get("description") or "")
            resources = [
                {"name": name, "count": int(match.group(1)), "status": "operational"}
                for name, pattern in _RESOURCE_VOCABULARY
                for match in [pattern.search(description)]
                if match
            ]
            if count is None:
                # A vehicle report that states no headcount must not restate
                # the headcount; it carries the committed one forward.
                committed = self.status_store.operational_state(scope=scope)
                if committed is None:
                    return ReportIngestionResult("rejected", "team resource report states no manpower count and none is committed")
                count = int(committed["manpower_count"])
            self.status_store.record_operational_state(
                manpower_count=count, resources=resources, source_event_id=event.get("event_id"),
                received_at=event.get("received_at") or datetime.now(timezone.utc).isoformat(),
                scenario_id=event.get("scenario_id"), scenario_run_id=event.get("scenario_run_id"), scenario_time=event.get("scenario_time"),
                scope=scope,
            )
            return ReportIngestionResult("committed", "team operational state committed", projection=project_report_facts(event, domain="team", projection_kind="authoritative_state", facts={"manpower_count": count, "resources": resources}))
        if classification == "team_operational_report":
            allowed = {"operational_status", "location", "uncertainty", "resource_mention"}
            if set(fields) - allowed:
                return ReportIngestionResult("rejected", "team operational report contains unsupported domain fields")
            if any(
                value is not None and type(value) not in {str, int, float, bool}
                for value in fields.values()
            ):
                return ReportIngestionResult("rejected", "team operational report fields must be scalar")
            return ReportIngestionResult(
                "committed",
                "team operational fact committed",
                projection=project_report_facts(
                    event,
                    domain="team",
                    projection_kind="operational_fact",
                    facts=fields,
                ),
            )
        if classification != "team_attendance_report":
            return ReportIngestionResult("not_applicable")
        availability = fields.get("availability")
        if availability != "unavailable" or not fields.get("reason"):
            return ReportIngestionResult("rejected", "attendance report requires unavailable status and reason")
        start, end = event.get("availability_start"), event.get("availability_end")
        if not start or not end:
            return ReportIngestionResult("rejected", "attendance report is missing its bounded interval")
        identity = str(event.get("sender_identity") or "")
        # The attendance cycle is an operational window, so it is opened and
        # judged on the operational clock; `received_at` stays the runtime
        # receipt record and never decides business lateness.
        reported = operational_time_of_event(event, scope=scope)
        operational_day = reported.astimezone(ZoneInfo(self.timezone_name)).date().isoformat()
        cycle = self.status_store.latest_cycle(scope=scope)
        if cycle is None:
            self.status_store.open_cycle(operational_day, reported.isoformat(), (reported + timedelta(hours=self.response_window_hours)).isoformat(), scope=scope)
        try:
            response = self.status_store.record_response(
                telegram_identity=identity, source_message_id=str(event.get("source_message_id") or event.get("event_id")),
                availability="unavailable", original_text=str(event.get("raw_text") or event.get("description") or ""),
                received_at=event.get("received_at") or runtime_now().isoformat(), reason=str(fields["reason"]),
                unavailable_until=end, availability_start=start, availability_end=end,
                reported_at=reported.isoformat(), operational_day=operational_day,
                scope=scope,
            )
        except Exception as exc:
            return ReportIngestionResult("rejected", f"attendance report was not accepted: {exc}")
        return ReportIngestionResult("committed", "attendance report committed", projection=project_report_facts(event, domain="team", facts={"availability": response["availability"], "availability_start": start, "availability_end": end, "reason": fields["reason"]}))

    def register_member(self, telegram_identity: str, full_name: str, registered_at: str | None = None) -> None:
        """Register one name/Telegram-ID pair before whole-roster approval."""

        self.status_store.register_member(telegram_identity, full_name, registered_at, scope=self._operational_scope())

    def approve_roster(self, commander_identity: str, approved_at: str | None = None) -> int:
        """Approve every currently registered member in one commander action."""

        return self.status_store.approve_roster(commander_identity, approved_at, scope=self._operational_scope())

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
            scope=self._operational_scope(),
        )

    def attendance_check_due(self, now_iso: str | None = None) -> bool:
        """True once after 08:00 Israel time for each local calendar day."""

        now = (_aware_datetime(now_iso) if now_iso else operational_now(scope=self._operational_scope())).astimezone(ZoneInfo(self.timezone_name))
        if now.hour < self.attendance_check_hour:
            return False
        latest = self.status_store.latest_cycle(scope=self._operational_scope())
        return latest is None or latest["cycle_key"] != now.date().isoformat()

    def run_scheduled_attendance_check(self, now_iso: str | None = None) -> str | None:
        """System scheduler hook: open one due cycle and return its outbound text."""

        if not self.status_store.roster_is_approved(scope=self._operational_scope()) or not self.attendance_check_due(now_iso):
            return None
        return self.start_daily_attendance_check(now_iso or "")

    def open_scheduled_cycle(self, now_iso: str | None = None, *, force: bool = False) -> dict | None:
        """Open today's cycle if it is due (or `force`d) and return its structured facts.

        Returns None when the roster is not yet approved, when the check is not
        due yet, or when today's cycle is already open — so a caller polling this
        on a timer opens each day's cycle exactly once. The returned dict
        (`cycle_key`, `opened_at`, `deadline_at`, `members_required`) carries no
        user-facing text: the transport renders the prompt from its own catalog."""

        if not self.status_store.roster_is_approved(scope=self._operational_scope()):
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
        now = _aware_datetime(now_iso) if now_iso else operational_now(scope=self._operational_scope())
        local_now = now.astimezone(ZoneInfo(self.timezone_name))
        deadline = now + timedelta(hours=self.response_window_hours)
        cycle = self.status_store.open_cycle(
            local_now.date().isoformat(),
            now.isoformat(),
            deadline.isoformat(),
            scope=self._operational_scope(),
        )
        snapshot = self.status_store.availability_snapshot(now.isoformat(), scope=self._operational_scope())
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

        approved_members = self.status_store.list_members(approved_only=True, scope=self._operational_scope())
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
                scope=self._operational_scope(),
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
        now = _aware_datetime(as_of_iso) if as_of_iso else operational_now(scope=self._operational_scope())
        snapshot = self.status_store.availability_snapshot(now.isoformat(), scope=self._operational_scope())
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
