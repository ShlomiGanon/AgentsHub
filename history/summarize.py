"""Hierarchical raw-to-daily-to-monthly-to-yearly summarization."""

import json
from datetime import datetime, timezone

from history.time_utils import parse_timestamp, storage_timestamp


class SummaryGenerationError(Exception):
    """A period summary could not be generated faithfully."""


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
    result = history_agent.process(prompt, allowed_tools=[])
    if result.status != "success":
        raise SummaryGenerationError(f"history agent could not summarize: {result.text}")
    return result.text


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
