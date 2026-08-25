from config.settings_store import SettingsStore
from history.query import HistoryQueryService
from persistence.interface import open_persistence


class UnusedAgent:
    def process(self, text, allowed_tools):
        raise AssertionError("precedent lookup must not call the model")


def test_precedent_search_uses_summary_candidates_and_raw_gaps(tmp_path):
    db_path = str(tmp_path / "precedent.db")
    store = open_persistence(db_path)
    try:
        for event_id, occurred_at, outcome in (
            ("resolved", "2026-08-18T09:00:00", "succeeded"),
            ("unresolved", "2026-08-19T09:00:00", None),
        ):
            store.append_event({
                "event_id": event_id, "received_at": occurred_at, "source": "sensor",
                "sender_identity": "s", "occurred_at": occurred_at, "raw_text": event_id,
                "classification": "fire", "area": "north", "outcome": outcome,
                "selected_protocol": "basic", "steps": [],
            })
        store.write_summary("daily", {
            "summary_text": "resolved fire",
            "period_start": "2026-08-18T00:00:00",
            "period_end": "2026-08-19T00:00:00",
            "generated_at": "2026-08-19T00:05:00",
            "event_index": [{"event_id": "resolved", "classification": "fire", "area": "north", "occurred_at": "2026-08-18T09:00:00", "outcome": "succeeded", "resolved": True}],
        })
        settings = SettingsStore(db_path, 1, 0.5, 30)
        service = HistoryQueryService(store, UnusedAgent(), settings)

        matches = service.search_precedents("target", "fire", "north", "2026-08-20T12:00:00")

        assert {match.event_id for match in matches} == {"resolved", "unresolved"}
        assert next(match for match in matches if match.event_id == "resolved").resolved is True
        assert next(match for match in matches if match.event_id == "unresolved").resolved is False
    finally:
        store.close()


def test_precedent_search_logs_the_window_and_the_matches(tmp_path, caplog):
    db_path = str(tmp_path / "precedent_log.db")
    store = open_persistence(db_path)
    try:
        store.append_event({
            "event_id": "resolved", "received_at": "2026-08-18T09:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2026-08-18T09:00:00", "raw_text": "resolved",
            "classification": "fire", "area": "north", "outcome": "succeeded",
            "selected_protocol": "basic", "steps": [],
        })
        settings = SettingsStore(db_path, 1, 0.5, 30)
        service = HistoryQueryService(store, UnusedAgent(), settings)

        # DEBUG, not INFO — this is the search's own internal detail (the
        # window it computed, the raw candidate list); the outcome an
        # operator needs is logged separately, at INFO, once closure is
        # evaluated (orchestrator.flows's "precedent_closure" event).
        with caplog.at_level("DEBUG"):
            matches = service.search_precedents("target", "fire", "north", "2026-08-20T12:00:00")

        lookups = [r for r in caplog.records if getattr(r, "event", None) == "precedent_lookup"]
        assert len(lookups) == 1
        assert lookups[0].levelname == "DEBUG"
        assert lookups[0].target_event_id == "target"
        assert lookups[0].classification == "fire"
        assert lookups[0].window_start
        assert lookups[0].window_end
        assert lookups[0].matched_event_ids == [m.event_id for m in matches]
    finally:
        store.close()
