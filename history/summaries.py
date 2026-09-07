"""Database-reconciled scheduling for summary generation and regeneration."""

import json
import logging
import threading
from datetime import datetime, timezone

from history.contracts import SummaryGenerationError
from history.event_pipeline import day_bounds, month_bounds, parse_timestamp, storage_timestamp, year_bounds


logger = logging.getLogger(__name__)


def _resolved(outcome: str | None) -> bool:
    return outcome in {"succeeded", "closed_on_precedent"}


def _event_index(events: list[dict]) -> list[dict]:
    return [
        {
            "event_id": event["event_id"],
            "classification": event.get("classification"),
            "area": event.get("area"),
            "occurred_at": event["occurred_at"],
            "outcome": event.get("outcome"),
            "resolved": _resolved(event.get("outcome")),
        }
        for event in events
        if event.get("occurred_at") is not None
    ]


def _merged_index(summaries: list[dict]) -> list[dict]:
    by_id = {}
    for summary in summaries:
        for item in summary.get("event_index") or []:
            by_id[item["event_id"]] = dict(item)
    return [by_id[event_id] for event_id in sorted(by_id)]


def _summary_prompt(level: str, period_start: str, period_end: str, records: list[dict]) -> str:
    return (
        f"Create a faithful {level} history summary for [{period_start}, {period_end}). "
        "Use only the supplied records. Retain what happened, selected protocols, exact agent "
        "tasks/actions, outcomes, and every contradiction without resolving it.\n"
        f"Records:\n{json.dumps(records, ensure_ascii=False, sort_keys=True)}"
    )


def _invoke(history_agent, prompt: str) -> str:
    agent_result = history_agent.process(prompt, allowed_tools=[])
    if agent_result.status != "success":
        raise SummaryGenerationError(f"history agent could not summarize: {agent_result.text}")
    return agent_result.text


def generate_summary(
    persistence,
    history_agent,
    level: str,
    period_start: datetime,
    period_end: datetime,
    generated_at: datetime | None = None,
) -> dict | None:
    generated = generated_at or datetime.now(timezone.utc)
    if period_end > generated:
        raise ValueError("cannot summarize an open period")

    start_text = storage_timestamp(period_start)
    end_text = storage_timestamp(period_end)

    if level == "daily":
        records = persistence.fetch_events_range(start_text, end_text)
        index = _event_index(records)
    elif level == "monthly":
        records = persistence.fetch_summaries_range("daily", start_text, end_text)
        records = [record for record in records if record["period_start"] >= start_text and record["period_end"] <= end_text]
        index = _merged_index(records)
    elif level == "yearly":
        records = persistence.fetch_summaries_range("monthly", start_text, end_text)
        records = [record for record in records if record["period_start"] >= start_text and record["period_end"] <= end_text]
        index = _merged_index(records)
    else:
        raise ValueError("summary level must be daily, monthly, or yearly")

    if not records:
        return None

    summary = {
        "summary_text": _invoke(history_agent, _summary_prompt(level, start_text, end_text, records)),
        "period_start": start_text,
        "period_end": end_text,
        "generated_at": storage_timestamp(generated),
        "event_index": index,
    }
    persistence.write_summary(level, summary)
    return summary


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
        """Whether the background thread's most recent reconciliation succeeded (§7.7's "whether the summary scheduler last ran successfully")."""

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
            existing_summary = _exact_summary(self._persistence, "daily", start, end)
            if existing_summary is None or self._daily_stale(existing_summary, period_events):
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
            existing_summary = _exact_summary(self._persistence, "monthly", start, end)
            if existing_summary is None or self._parent_stale(existing_summary, children):
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
            existing_summary = _exact_summary(self._persistence, "yearly", start, end)
            if existing_summary is None or self._parent_stale(existing_summary, children):
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
