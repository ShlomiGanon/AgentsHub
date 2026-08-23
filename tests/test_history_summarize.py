from datetime import datetime, timezone

from agents.results import AgentResult
from history.summarize import generate_summary
from persistence.interface import open_persistence


class FakeHistoryAgent:
    def __init__(self):
        self.prompts = []

    def process(self, text, allowed_tools):
        self.prompts.append(text)
        return AgentResult("success", f"summary-{len(self.prompts)}")


def test_rollups_use_lower_level_and_deduplicate_indexes(tmp_path):
    store = open_persistence(str(tmp_path / "summary.db"))
    try:
        store.append_event({
            "event_id": "e1", "received_at": "2026-01-01T10:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2026-01-01T10:00:00", "raw_text": "a",
            "classification": "fire", "area": "north", "outcome": "succeeded",
        })
        agent = FakeHistoryAgent()
        generated = datetime(2027, 1, 2, tzinfo=timezone.utc)

        daily = generate_summary(store, agent, "daily", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), generated)
        monthly = generate_summary(store, agent, "monthly", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc), generated)
        yearly = generate_summary(store, agent, "yearly", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc), generated)

        assert daily["event_index"][0]["event_id"] == "e1"
        assert monthly["event_index"] == daily["event_index"]
        assert yearly["event_index"] == daily["event_index"]
        assert "summary-1" in agent.prompts[1]
        assert "summary-2" in agent.prompts[2]
    finally:
        store.close()
