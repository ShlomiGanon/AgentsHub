from datetime import datetime, timezone

from agents.results import AgentResult
from fixtures.seed_events import load_seed_dataset
from history.interface import SummaryScheduler
from persistence.interface import open_persistence


class FidelityAgent:
    def process(self, text, allowed_tools):
        return AgentResult("success", text)


def test_seed_dataset_survives_three_summary_levels_with_contradictions(tmp_path):
    store = open_persistence(str(tmp_path / "fidelity.db"))
    try:
        load_seed_dataset(store)
        scheduler = SummaryScheduler(
            store,
            FidelityAgent(),
            clock=lambda: datetime(2027, 1, 2, tzinfo=timezone.utc),
        )
        scheduler.reconcile()

        [yearly] = store.fetch_summaries_range("yearly", "2026-01-01T00:00:00", "2027-01-01T00:00:00")
        assert "One person with a minor injury" in yearly["summary_text"]
        assert "Two people hurt" in yearly["summary_text"]
        assert "selected_protocol" in yearly["summary_text"]
        assert "outcome" in yearly["summary_text"]
        assert len(yearly["event_index"]) == len({item["event_id"] for item in yearly["event_index"]})
    finally:
        store.close()
