"""9.4 — Test profile isolation (work_plan.md §9.4).

Two full deployments — two real running API servers, two real SQLite
files, two real settings stores — run at once and must never leak state
into each other.
"""

import types

import pytest

from agents import adapter
from config.live_settings import SettingsStore
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, RunningApiServer, build_context, happy_path_agent
from tools.simulator import _post_event


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


def test_two_profiles_at_once_on_separate_ports_and_databases(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ctx_a = build_context(tmp_path / "a")
    ctx_b = build_context(tmp_path / "b")

    with RunningApiServer(ctx_a) as server_a, RunningApiServer(ctx_b) as server_b:
        assert server_a.port != server_b.port
        assert ctx_a.deps.persistence.db_path != ctx_b.deps.persistence.db_path


def test_events_written_under_one_profile_never_appear_in_the_others_history_or_precedent_search(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ctx_a = build_context(tmp_path / "a")
    ctx_b = build_context(tmp_path / "b")

    try:
        event_a = ctx_a.deps.persistence.append_event({
            "received_at": "2026-08-24T10:00:00", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T10:00:00", "raw_text": "fire near gate 3",
            "classification": "fire", "area": "north_sector",
        })
        event_b = ctx_b.deps.persistence.append_event({
            "received_at": "2026-08-24T10:05:00", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T10:05:00", "raw_text": "fire near gate 3, again",
            "classification": "fire", "area": "north_sector",
        })

        events_seen_by_a = ctx_a.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        events_seen_by_b = ctx_b.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")

        assert {e["event_id"] for e in events_seen_by_a} == {event_a}
        assert {e["event_id"] for e in events_seen_by_b} == {event_b}

        # Precedent search: A's own event, matching classification/area
        # against A's own history, must never surface B's event (which
        # would be a genuine match if the databases were shared).
        precedents_from_a = ctx_a.deps.history_query_service.search_precedents(
            target_event_id=event_a, classification="fire", area="north_sector", target_event_occurred_at="2026-08-24T10:00:00",
        )
        matched_ids = {p.event_id for p in precedents_from_a}
        assert event_b not in matched_ids
    finally:
        ctx_a.queue.stop()
        ctx_a.deps.persistence.close()
        ctx_b.queue.stop()
        ctx_b.deps.persistence.close()


def test_a_user_added_to_one_profile_is_refused_by_the_other(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ctx_a = build_context(tmp_path / "a")
    ctx_b = build_context(tmp_path / "b")

    try:
        ctx_a.deps.persistence.write_user("new-commander", "commander")

        assert ctx_a.deps.persistence.read_user("new-commander") is not None
        assert ctx_b.deps.persistence.read_user("new-commander") is None
    finally:
        ctx_a.queue.stop()
        ctx_a.deps.persistence.close()
        ctx_b.queue.stop()
        ctx_b.deps.persistence.close()


def test_the_two_settings_stores_are_independent(tmp_path):
    store_a = SettingsStore(str(tmp_path / "a.db"), starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)
    store_b = SettingsStore(str(tmp_path / "b.db"), starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)

    store_a.set_risk_threshold(0.9)

    assert store_a.get_risk_threshold() == 0.9
    assert store_b.get_risk_threshold() == 0.5


def test_two_real_servers_process_writes_through_their_own_api_independently(tmp_path):
    agent_a = happy_path_agent(risk_score="0.1", selected="status_check")
    agent_b = happy_path_agent(risk_score="0.1", selected="status_check")

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ctx_a = build_context(tmp_path / "a", main_agent=agent_a)
    ctx_b = build_context(tmp_path / "b", main_agent=agent_b)

    with RunningApiServer(ctx_a) as server_a, RunningApiServer(ctx_b) as server_b:
        result_a = _post_event(server_a.base_url, VIEWER_IDENTITY, "fire at gate 3")
        result_b = _post_event(server_b.base_url, COMMANDER_IDENTITY, "fire at gate 4")

        ctx_a.queue.wait_until_idle()
        ctx_b.queue.wait_until_idle()

        events_a = ctx_a.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        events_b = ctx_b.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")

        assert {e["event_id"] for e in events_a} == {result_a["event_id"]}
        assert {e["event_id"] for e in events_b} == {result_b["event_id"]}
