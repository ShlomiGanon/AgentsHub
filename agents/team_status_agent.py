"""Readiness-team attendance specialist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agents.runtime import Agent, tool
from persistence import TeamStatusPersistenceError, open_team_status_persistence


def _aware_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
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

    @tool(
        "start_daily_attendance_check",
        "Opens today's one-hour readiness-team attendance window and returns the exact group message for the system Telegram transport to send.",
        side_effecting=True,
        idempotent=True,
    )
    def start_daily_attendance_check(self, now_iso: str = "") -> str:
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
        "Stores one approved-roster member's normalized free-text response; late responses remain pending until a commander reviews them.",
        side_effecting=True,
        idempotent=True,
    )
    def record_attendance_response(
        self,
        telegram_identity: str,
        source_message_id: str,
        availability: str,
        original_text: str,
        reason: str = "",
        unavailable_days: int = 0,
        received_at: str = "",
    ) -> str:
        now = _aware_datetime(received_at or None)
        normalized = availability.strip().lower()
        if normalized not in {"available", "unavailable"}:
            return "Clarification required: specify whether the member is available or unavailable."
        if normalized == "unavailable" and not reason.strip():
            return "Clarification required: an unavailable member must provide a reason."
        if normalized == "unavailable" and unavailable_days < 1:
            return "Clarification required: specify how many days the member will be unavailable."

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
