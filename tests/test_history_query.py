from datetime import datetime, timezone

from agents.results import AgentResult
from history.query import HistoryQueryService
from persistence.interface import open_persistence


class FakeHistoryAgent:
    def __init__(self):
        self.last_prompt = None

    def process(self, text, allowed_tools):
        self.last_prompt = text
        return AgentResult("success", "answer from stored context")


def test_query_uses_summary_without_double_counting_and_applies_filters(tmp_path):
    store = open_persistence(str(tmp_path / "query.db"))
    try:
        store.write_summary("daily", {
            "summary_text": "fire and medical reports",
            "period_start": "2026-08-01T00:00:00",
            "period_end": "2026-08-02T00:00:00",
            "generated_at": "2026-08-02T00:05:00",
            "event_index": [
                {"event_id": "fire-1", "classification": "fire", "area": "north", "occurred_at": "2026-08-01T10:00:00", "outcome": "succeeded", "resolved": True},
                {"event_id": "medical-1", "classification": "medical", "area": "south", "occurred_at": "2026-08-01T11:00:00", "outcome": "succeeded", "resolved": True},
            ],
        })
        agent = FakeHistoryAgent()
        service = HistoryQueryService(store, agent)

        answer = service.query(
            "What fires occurred?",
            "2026-08-01T00:00:00",
            "2026-08-02T00:00:00",
            classification="fire",
            area="north",
        )

        assert answer.answer == "answer from stored context"
        assert answer.total_events_matched == 1
        assert [source.level for source in answer.sources_used] == ["daily"]
        assert "fire-1" in agent.last_prompt
    finally:
        store.close()


def test_partial_day_falls_back_to_raw_events(tmp_path):
    store = open_persistence(str(tmp_path / "query-raw.db"))
    try:
        store.append_event({
            "event_id": "e1", "received_at": "2026-08-01T10:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2026-08-01T10:00:00", "raw_text": "fire",
            "classification": "fire", "area": "north",
        })
        service = HistoryQueryService(store, FakeHistoryAgent())
        answer = service.query("What happened?", "2026-08-01T09:00:00", "2026-08-01T11:00:00")

        assert answer.total_events_matched == 1
        assert answer.sources_used[0].level == "raw_event"
    finally:
        store.close()
