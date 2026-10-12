"""Readiness-team attendance specialist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agents.runtime import Agent, get_authenticated_request_identity, tool
from agents.contracts import failed_tool_result
from messages import MessageCatalogError, get_current_catalog
from persistence import AttendanceCycle, TeamStatusPersistenceError, open_team_status_persistence

# Optional, more specific variants of a notice key, tried in this order. Each names the
# placeholders it needs, so `<key>.area` is used only when an area is known, and so on.
_NOTICE_VARIANTS = (
    ("area_description", ("area", "description")),
    ("area", ("area",)),
    ("description", ("description",)),
)


def _aware_datetime(value: str | None) -> datetime:
    """Aware datetime."""

    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class TeamStatusAgent(Agent):
    """Specialist used only by readiness-team profiles."""

    name = "team_status_agent"
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
        "For a protocol run, the authenticated requester identity and the authoritative event "
        "envelope supply the member identity, source message ID, original text, and received-at "
        "time. Never refuse a write because those provenance fields are absent from the prose task "
        "and never ask the caller to repeat them; use the envelope/runtime context and call the "
        "approved write tool with the normalized availability, reason, and duration. "
        "Every attendance report is stored immediately. When the Main "
        "Agent asks for the team picture, call report_team_availability and return its complete "
        "name-by-name result without dropping unavailable or missing members."
    )

    status_db_path = ""
    timezone_name = "Asia/Jerusalem"
    response_window_hours = 1

    def __init__(self, model: str, api_key: str | None = None):
        """Init."""

        if not self.status_db_path:
            raise TypeError("TeamStatusAgent requires a class-level status_db_path")
        self.status_store = open_team_status_persistence(self.status_db_path)
        super().__init__(model, api_key)

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

    def attendance_check_due(self, now_iso: str | None, check_hour: int) -> bool:
        """True once the enabled group's local hour has arrived for this calendar day."""

        now = _aware_datetime(now_iso).astimezone(ZoneInfo(self.timezone_name))
        if now.hour < check_hour:
            return False
        latest = self.status_store.latest_cycle()
        return latest is None or latest["cycle_key"] != now.date().isoformat()

    def open_scheduled_cycle(
        self,
        now_iso: str | None = None,
        *,
        force: bool = False,
        check_hour: int | None = None,
    ) -> dict | None:
        """Open or reuse today's cycle and return its structured facts.

        `check_hour` comes from the enabled attendance group row. None means no
        eligible group: the automatic path does not open; a pending force
        broadcast can still be claimed. Force reuses today's cycle if it
        already exists and requests a one-shot broadcast for the bot."""

        if not self.status_store.roster_is_approved():
            return None
        if force:
            cycle, requested = self._open_cycle(now_iso or "")
            self.status_store.request_broadcast(cycle.cycle_key)
            return {
                "cycle_key": cycle.cycle_key,
                "opened_at": cycle.opened_at,
                "deadline_at": cycle.deadline_at,
                "members_required": requested,
            }
        if check_hour is not None and self.attendance_check_due(now_iso, check_hour):
            cycle, requested = self._open_cycle(now_iso or "")
            if not cycle.created:
                return None
            return {
                "cycle_key": cycle.cycle_key,
                "opened_at": cycle.opened_at,
                "deadline_at": cycle.deadline_at,
                "members_required": requested,
            }
        return self.status_store.claim_broadcast()

    def _open_cycle(self, now_iso: str) -> tuple[AttendanceCycle, list[str]]:
        """Open cycle."""

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
        """Start daily attendance check."""

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
        "Stores one approved-roster member's normalized free-text response immediately.",
        side_effecting=True,
        idempotent=True,
    )
    def record_attendance_response(
        self,
        source_message_id: str = "direct-response",
        availability: str = "",
        original_text: str = "",
        reason: str = "",
        unavailable_days: int = 0,
        received_at: str = "",
    ) -> str:
        """Record attendance response."""

        telegram_identity = get_authenticated_request_identity()
        if not telegram_identity:
            return failed_tool_result("The attendance response was not stored: authenticated requester identity is unavailable.")
        now = _aware_datetime(received_at or None)
        if not source_message_id:
            source_message_id = f"msg-{int(now.timestamp())}"
        if not original_text:
            original_text = f"availability report: {availability}"

        approved_members = self.status_store.list_members(approved_only=True)
        if not any(m["telegram_identity"] == telegram_identity for m in approved_members):
            return failed_tool_result("The attendance response was not stored: requester is not an approved roster member.")

        normalized = availability.strip().lower()
        if normalized not in {"available", "unavailable"}:
            return failed_tool_result("Clarification required: specify whether the member is available or unavailable.")
        if normalized == "unavailable" and not reason.strip():
            return failed_tool_result("Clarification required: an unavailable member must provide a reason.")
        if normalized == "unavailable" and unavailable_days < 1:
            return failed_tool_result("Clarification required: specify how many days the member will be unavailable.")

        unavailable_until = None
        if normalized == "unavailable":
            unavailable_until = (now + timedelta(days=unavailable_days)).isoformat()

        try:
            response = self.status_store.record_response(
                telegram_identity=telegram_identity,
                source_message_id=source_message_id,
                availability=normalized,
                original_text=original_text,
                received_at=now.isoformat(),
                reason=reason or None,
                unavailable_until=unavailable_until,
            )
        except TeamStatusPersistenceError as exc:
            return failed_tool_result(f"The attendance response was not stored: {exc}")

        return "The attendance response was stored."

    def _notice_context(self, about_event_id: str, area: str, description: str) -> tuple[str, str]:
        """(area text, description text) for a notice; a profile may resolve about_event_id itself."""

        return area, description

    @tool(
        "post_operational_notice",
        "Returns the catalog sentence for one operational update. area, description, and about_event_id "
        "are optional details of the event the update is about; a more specific catalog sentence is used "
        "when they are known. Does not dispatch a resource and does not ask which camera to check.",
        side_effecting=False,
    )
    def post_operational_notice(
        self, notice_key: str = "", area: str = "", description: str = "", about_event_id: str = ""
    ) -> str:
        """Return the catalog sentence for a profile-declared operational update."""

        catalog = get_current_catalog()
        if notice_key not in catalog.messages:
            return failed_tool_result("Clarification required: unknown operational notice.")
        area_text, description_text = self._notice_context(
            about_event_id.strip(), area.strip(), description.strip()
        )
        known = {"area": area_text, "description": description_text}
        for suffix, placeholders in _NOTICE_VARIANTS:
            variant_key = f"{notice_key}.{suffix}"
            if variant_key in catalog.messages and all(known[name] for name in placeholders):
                try:
                    return catalog.text(variant_key, **{name: known[name] for name in placeholders})
                except MessageCatalogError:
                    continue
        try:
            return catalog.text(notice_key)
        except MessageCatalogError:
            return failed_tool_result("Clarification required: the operational notice needs event details.")

    def _field_presence(self, entry: dict) -> str | None:
        """Where a member not yet reporting attendance is known to be active, if a profile tracks it."""

        return None

    def _until_text(self, unavailable_until: str) -> str:
        """Local date and time for an unavailability end, formatted without non-ASCII strftime."""

        local = _aware_datetime(unavailable_until).astimezone(ZoneInfo(self.timezone_name))
        # Windows strftime rejects non-ASCII format strings, so only digits go through it and
        # the connecting words come from the catalog.
        return get_current_catalog().text(
            "attendance.report.until_datetime", date=local.strftime("%d/%m/%Y"), time=local.strftime("%H:%M")
        )

    @tool(
        "report_team_availability",
        "Returns the approved readiness-team roster with every member's current availability, reason, return time, original response, and report time.",
        side_effecting=False,
    )
    def report_team_availability(self, as_of_iso: str = "") -> str:
        """Report team availability."""

        catalog = get_current_catalog()
        now = _aware_datetime(as_of_iso or None)
        snapshot = self.status_store.availability_snapshot(now.isoformat())
        if not snapshot:
            return catalog.text("attendance.report.empty")

        # The Hebrew report stays short for commanders; other languages keep the raw response.
        include_original = catalog.language != "he"
        lines = [catalog.text("attendance.report.header")]
        counts = {"available": 0, "unavailable": 0, "awaiting_response": 0, "in_field": 0}
        for entry in snapshot:
            status = entry["availability"]
            label_values: dict = {}
            if status == "awaiting_response":
                presence = self._field_presence(entry)
                if presence:
                    status, label_values = "in_field", {"area": presence}
            counts[status] += 1
            detail = ""
            if status == "unavailable":
                detail = catalog.text("attendance.report.reason_detail", reason=entry["reason"])
                if entry["unavailable_until"]:
                    detail += catalog.text(
                        "attendance.report.until_detail", until=self._until_text(entry["unavailable_until"])
                    )
            if include_original and entry["original_text"]:
                detail += catalog.text(
                    "attendance.report.original_detail",
                    original=entry["original_text"], received_at=entry["received_at"],
                )
            lines.append(catalog.text(
                "attendance.report.member_line",
                name=entry["full_name"],
                status=catalog.text(f"attendance.report.status.{status}", **label_values),
                detail=detail,
            ))

        count_parts = [
            catalog.text("attendance.report.count_available", count=counts["available"]),
            catalog.text("attendance.report.count_unavailable", count=counts["unavailable"]),
            catalog.text("attendance.report.count_awaiting", count=counts["awaiting_response"]),
        ]
        if counts["in_field"]:
            count_parts.append(catalog.text("attendance.report.count_in_field", count=counts["in_field"]))
        lines.extend((
            "",
            catalog.text("attendance.report.total", count=len(snapshot)),
            catalog.text("attendance.report.count_separator").join(count_parts),
        ))
        return "\n".join(lines)
