"""tools/simulator.py (work_plan.md §9.1)."""

import types

import pytest

from agents import adapter
from tests.api_fakes import SENSOR_IDENTITY, RunningApiServer, build_context, happy_path_agent
from tools import simulator


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput("status nominal, no anomalies")

    fake_module = types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))
    monkeypatch.setattr(adapter, "_get_crewai", lambda: fake_module)


# -- Text generation ---------------------------------------------------------


def test_fire_and_medical_text_differ_in_content():
    fire_texts = {simulator._generate_text("fire", "north_sector") for _ in range(30)}
    medical_texts = {simulator._generate_text("medical", "north_sector") for _ in range(30)}

    assert len(fire_texts) > 1  # real template variety, not one fixed string
    assert len(medical_texts) > 1
    assert fire_texts.isdisjoint(medical_texts)


def test_unclassifiable_text_is_drawn_from_its_own_pool():
    texts = {simulator._generate_text(None, "north_sector") for _ in range(30)}

    assert len(texts) > 1
    fire_texts = {simulator._generate_text("fire", "north_sector") for _ in range(30)}
    assert texts.isdisjoint(fire_texts)


def test_generated_text_names_the_area():
    text = simulator._generate_text("fire", "south_sector")
    assert "south_sector" in text


# -- Classification/area selection -------------------------------------------


def test_unclassifiable_rate_of_one_always_produces_no_classification():
    for _ in range(10):
        event_type, _area = simulator._next_classification_area(["fire"], ["north_sector"], repeat_rate=0.0, unclassifiable_rate=1.0, recent=[])
        assert event_type is None


def test_repeat_rate_of_one_with_a_nonempty_pool_always_reuses_it():
    recent = [("fire", "north_sector")]
    for _ in range(10):
        pair = simulator._next_classification_area(["fire", "medical"], ["north_sector", "south_sector"], repeat_rate=1.0, unclassifiable_rate=0.0, recent=recent)
        assert pair == ("fire", "north_sector")


def test_repeat_rate_of_one_with_an_empty_pool_falls_back_to_a_fresh_pick():
    # Nothing to repeat yet — must not crash or always return the same
    # thing by coincidence.
    event_type, area = simulator._next_classification_area(["fire"], ["north_sector"], repeat_rate=1.0, unclassifiable_rate=0.0, recent=[])
    assert event_type == "fire"
    assert area == "north_sector"


# -- CLI argument validation --------------------------------------------------


def test_giving_neither_count_nor_duration_is_an_error(capsys):
    exit_code = simulator.main(["--port", "1", "--identity", "x"])
    assert exit_code == 1
    assert "exactly one of --count or --duration" in capsys.readouterr().err


def test_giving_both_count_and_duration_is_an_error(capsys):
    exit_code = simulator.main(["--port", "1", "--identity", "x", "--count", "1", "--duration", "1"])
    assert exit_code == 1


# -- Real end-to-end run against a real running API --------------------------


def test_a_real_run_against_a_real_server_creates_the_events(tmp_path):
    ctx = build_context(tmp_path, main_agent=happy_path_agent())
    with RunningApiServer(ctx) as server:
        exit_code = simulator.main([
            "--host", "127.0.0.1",
            "--port", str(server.port),
            "--identity", SENSOR_IDENTITY,
            "--count", "5",
            "--rate", "0",  # no delay between sends — this is a test, not a demo
            "--seed", "1",
            "--quiet",
        ])

        ctx.queue.wait_until_idle()

        assert exit_code == 0
        all_events = ctx.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        assert len(all_events) == 5
        assert all(event["source"] == "sensor" for event in all_events)
        assert all(event["sender_identity"] == SENSOR_IDENTITY for event in all_events)


def test_burst_size_sends_events_with_no_inter_event_delay(tmp_path):
    ctx = build_context(tmp_path, main_agent=happy_path_agent())
    with RunningApiServer(ctx) as server:
        exit_code = simulator.main([
            "--host", "127.0.0.1",
            "--port", str(server.port),
            "--identity", SENSOR_IDENTITY,
            "--count", "8",
            "--burst-size", "8",
            "--rate", "0",
            "--seed", "2",
            "--quiet",
        ])

        ctx.queue.wait_until_idle()

        assert exit_code == 0
        all_events = ctx.deps.persistence.fetch_events_range("2000-01-01T00:00:00", "2100-01-01T00:00:00")
        assert len(all_events) == 8


def test_an_unregistered_identity_fails_every_submission(tmp_path):
    ctx = build_context(tmp_path, main_agent=happy_path_agent())
    with RunningApiServer(ctx) as server:
        exit_code = simulator.main([
            "--host", "127.0.0.1",
            "--port", str(server.port),
            "--identity", "not-a-registered-sensor",
            "--count", "3",
            "--rate", "0",
            "--quiet",
        ])

        assert exit_code == 1
