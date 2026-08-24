"""Database-reconciled scheduling for summary generation and regeneration."""

import logging
import threading
from datetime import datetime, timezone

from history.summarize import generate_summary
from history.time_utils import day_bounds, month_bounds, parse_timestamp, storage_timestamp, year_bounds


logger = logging.getLogger(__name__)


def _exact_summary(persistence, level: str, start: datetime, end: datetime) -> dict | None:
    start_text = storage_timestamp(start)
    end_text = storage_timestamp(end)
    for summary in persistence.fetch_summaries_range(level, start_text, end_text):
        if summary["period_start"] == start_text and summary["period_end"] == end_text:
            return summary
    return None


def _is_newer(left: str, right: str) -> bool:
    return parse_timestamp(left) > parse_timestamp(right)


class SummaryScheduler:
    def __init__(self, persistence, history_agent, clock=None, poll_interval_seconds: float = 60.0):
        self._persistence = persistence
        self._history_agent = history_agent
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._poll_interval_seconds = poll_interval_seconds
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_run_at: str | None = None
        self._last_run_ok: bool | None = None
        self._last_run_error: str | None = None

    def last_run_status(self) -> dict:
        """Whether the background thread's most recent reconciliation
        succeeded (§7.7's "whether the summary scheduler last ran
        successfully"). `last_run_ok` is None until the thread has
        completed a first pass — never started is not the same as failed.
        A manual `reconcile()` call (as most tests make) does not update
        this; only the scheduled background run does.
        """

        return {
            "last_run_at": self._last_run_at,
            "last_run_ok": self._last_run_ok,
            "last_run_error": self._last_run_error,
        }

    def _all_events(self, now: datetime) -> list[dict]:
        return self._persistence.fetch_events_range("0001-01-01T00:00:00", storage_timestamp(now))

    def _daily_stale(self, summary: dict, events: list[dict]) -> bool:
        if summary.get("event_index") is None:
            return True
        return any(_is_newer(event["received_at"], summary["generated_at"]) for event in events)

    def _parent_stale(self, summary: dict, children: list[dict]) -> bool:
        if summary.get("event_index") is None:
            return True
        return any(_is_newer(child["generated_at"], summary["generated_at"]) for child in children)

    def reconcile(self) -> list[str]:
        now = self._clock().astimezone(timezone.utc)
        current_day_start = day_bounds(now)[0]
        events = self._all_events(now)
        actions = []

        daily_periods = {}
        for event in events:
            occurred_at = event.get("occurred_at")
            if occurred_at is None:
                continue
            start, end = day_bounds(parse_timestamp(occurred_at))
            if end <= current_day_start:
                daily_periods[(start, end)] = True

        for start, end in sorted(daily_periods):
            period_events = [
                event
                for event in events
                if start <= parse_timestamp(event["occurred_at"]) < end
            ]
            existing = _exact_summary(self._persistence, "daily", start, end)
            if existing is None or self._daily_stale(existing, period_events):
                generate_summary(self._persistence, self._history_agent, "daily", start, end, now)
                actions.append(f"daily:{storage_timestamp(start)}")

        daily_summaries = self._persistence.fetch_summaries_range(
            "daily", "0001-01-01T00:00:00", storage_timestamp(current_day_start)
        )
        monthly_periods = {
            month_bounds(parse_timestamp(summary["period_start"]))
            for summary in daily_summaries
            if month_bounds(parse_timestamp(summary["period_start"]))[1] <= now
        }

        for start, end in sorted(monthly_periods):
            children = [
                summary
                for summary in daily_summaries
                if start <= parse_timestamp(summary["period_start"])
                and parse_timestamp(summary["period_end"]) <= end
            ]
            existing = _exact_summary(self._persistence, "monthly", start, end)
            if existing is None or self._parent_stale(existing, children):
                generate_summary(self._persistence, self._history_agent, "monthly", start, end, now)
                actions.append(f"monthly:{storage_timestamp(start)}")

        monthly_summaries = self._persistence.fetch_summaries_range(
            "monthly", "0001-01-01T00:00:00", storage_timestamp(now)
        )
        yearly_periods = {
            year_bounds(parse_timestamp(summary["period_start"]))
            for summary in monthly_summaries
            if year_bounds(parse_timestamp(summary["period_start"]))[1] <= now
        }

        for start, end in sorted(yearly_periods):
            children = [
                summary
                for summary in monthly_summaries
                if start <= parse_timestamp(summary["period_start"])
                and parse_timestamp(summary["period_end"]) <= end
            ]
            existing = _exact_summary(self._persistence, "yearly", start, end)
            if existing is None or self._parent_stale(existing, children):
                generate_summary(self._persistence, self._history_agent, "yearly", start, end, now)
                actions.append(f"yearly:{storage_timestamp(start)}")

        return actions

    def notify_event_written(self, event_id: str, source: str, occurred_at: str | None, received_at: str) -> None:
        del event_id

        if source == "sensor" or occurred_at is None:
            return

        day_start, day_end = day_bounds(parse_timestamp(occurred_at))
        summary = _exact_summary(self._persistence, "daily", day_start, day_end)
        if summary is not None and _is_newer(received_at, summary["generated_at"]):
            self._wake_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(self._poll_interval_seconds)
            self._wake_event.clear()
            if self._stop_event.is_set():
                return
            try:
                self.reconcile()
                self._last_run_ok = True
                self._last_run_error = None
            except Exception as exc:
                self._last_run_ok = False
                self._last_run_error = str(exc)
                logger.exception("history summary reconciliation failed")
            finally:
                self._last_run_at = self._clock().astimezone(timezone.utc).isoformat()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="history-summary-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return

        self._stop_event.set()
        self._wake_event.set()
        self._thread.join()
        self._thread = None
