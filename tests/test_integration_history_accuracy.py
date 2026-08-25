"""9.16 — Test history accuracy over time (work_plan.md §9.16).

Multi-month fidelity and cross-level query assembly are already covered
by `tests/test_history_fidelity.py::test_seed_dataset_survives_three_summary_levels_with_contradictions`
and `tests/test_history_query.py`; downtime-gap backfill is already
covered by `tests/test_history_scheduler.py::test_reconciliation_builds_bottom_up_and_is_idempotent`.
What isn't covered anywhere: a late-arriving report actually causing all
three summary levels to regenerate, not just waking the scheduler
thread (`test_late_telegram_notification_wakes_only_for_existing_stale_day`
only asserts the wake event is set, never that a reconciliation pass
afterward really regenerates every level).
"""

from datetime import datetime, timezone

from agents.results import AgentResult
from history.scheduler import SummaryScheduler
from persistence.interface import open_persistence


class _FakeHistoryAgent:
    def process(self, text, allowed_tools):
        return AgentResult("success", text)


def test_a_late_arriving_report_regenerates_all_three_summary_levels(tmp_path):
    store = open_persistence(str(tmp_path / "late_arrival.db"))
    try:
        store.append_event({
            "event_id": "e1", "received_at": "2025-08-15T10:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2025-08-15T10:00:00", "raw_text": "fire",
            "classification": "fire", "area": "north", "outcome": "succeeded",
        })

        # A clock that advances between passes — reconcile stamps every
        # summary it (re)generates with the clock's "now", and staleness
        # propagates upward by comparing a parent's generated_at against
        # its children's; a fixed clock would make the daily regeneration
        # in the second pass look no newer than the still-current monthly/
        # yearly summaries, and the propagation this bullet exists to
        # prove would never fire.
        current_now = [datetime(2026, 2, 1, tzinfo=timezone.utc)]
        scheduler = SummaryScheduler(store, _FakeHistoryAgent(), clock=lambda: current_now[0])

        first_pass = scheduler.reconcile()
        assert [a.split(":")[0] for a in first_pass] == ["daily", "monthly", "yearly"]
        assert scheduler.reconcile() == []  # settled, nothing stale

        # A late Telegram report whose occurrence falls in the same,
        # already-summarized day, written (received) after that summary
        # was generated.
        store.append_event({
            "event_id": "e2", "received_at": "2026-02-02T09:00:00", "source": "telegram",
            "sender_identity": "s", "occurred_at": "2025-08-15T14:00:00", "raw_text": "late report of the same fire",
            "classification": "fire", "area": "north", "outcome": "succeeded",
        })

        current_now[0] = datetime(2026, 2, 2, tzinfo=timezone.utc)
        second_pass = scheduler.reconcile()

        # All three levels regenerated — not just the day the late report
        # landed in, but the month and year built on top of it too.
        assert [a.split(":")[0] for a in second_pass] == ["daily", "monthly", "yearly"]

        daily_after = store.fetch_summaries_range("daily", "2025-08-15T00:00:00", "2025-08-16T00:00:00")[0]
        # The regenerated daily summary's own event index now includes the
        # late arrival, not just the original event — real content change,
        # not merely a re-stamped timestamp.
        indexed_ids = {entry["event_id"] for entry in daily_after["event_index"]}
        assert indexed_ids == {"e1", "e2"}
    finally:
        store.close()
