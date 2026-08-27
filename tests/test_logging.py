import json
import logging

import config.base as base_config
from tools.logging_config import configure_logging
from tools.tracing import get_trace_id, new_trace_id, trace_context


def test_log_record_is_a_single_json_object_with_named_fields(capsys):
    configure_logging("test_profile")
    logging.getLogger("test").info("something happened", extra={"risk_level": "high"})

    output = capsys.readouterr().out.strip()
    record = json.loads(output)

    assert record["message"] == "something happened"
    assert record["profile_name"] == "test_profile"
    assert record["risk_level"] == "high"
    assert "timestamp" in record
    assert "trace_id" in record


def test_trace_id_is_attached_to_every_record_within_the_context(capsys):
    configure_logging("test_profile")

    with trace_context() as trace_id:
        logging.getLogger("test").info("step one")
        logging.getLogger("test").info("step two")

    lines = capsys.readouterr().out.strip().splitlines()
    records = [json.loads(line) for line in lines]

    assert records[0]["trace_id"] == trace_id
    assert records[1]["trace_id"] == trace_id


def test_trace_context_restores_previous_value_on_exit():
    outer_id = new_trace_id()

    with trace_context(outer_id):
        with trace_context() as inner_id:
            assert get_trace_id() == inner_id
            assert inner_id != outer_id

        assert get_trace_id() == outer_id


# -- DB-backed log sink (§1.8 follow-up) -------------------------------------


class _FakePersistence:
    """A minimal duck-typed stand-in — only `write_log_entry` is exercised
    by the handler under test, so nothing else needs implementing.
    """

    def __init__(self, raise_on_write: Exception | None = None):
        self.calls: list[tuple[str | None, dict]] = []
        self._raise_on_write = raise_on_write

    def write_log_entry(self, trace_id, details):
        if self._raise_on_write is not None:
            raise self._raise_on_write
        self.calls.append((trace_id, dict(details)))


def test_configure_logging_with_no_persistence_never_touches_any_write_path(capsys):
    """Backward compatibility: every existing caller that doesn't pass
    `persistence=` (bot.app, and every test predating this feature) must
    keep working completely unchanged.
    """

    configure_logging("test_profile")
    logging.getLogger("test").info("no db sink here")

    output = capsys.readouterr().out.strip()
    assert json.loads(output)["message"] == "no db sink here"  # stdout still works exactly as before


def test_a_persistence_handle_receives_a_full_copy_of_every_log_record(capsys):
    fake = _FakePersistence()
    configure_logging("test_profile", persistence=fake)

    with trace_context() as trace_id:
        logging.getLogger("test").info("something happened", extra={"risk_level": "high"})

    assert len(fake.calls) == 1
    written_trace_id, details = fake.calls[0]
    assert written_trace_id == trace_id
    assert details["message"] == "something happened"
    assert details["risk_level"] == "high"
    assert details["level"] == "INFO"
    assert details["logger"] == "test"
    assert "trace_id" not in details  # travels as its own parameter, never duplicated in details

    # The DB row's content matches what stdout got, field for field (minus
    # trace_id/timestamp, which travel outside `details` on the DB side).
    stdout_record = json.loads(capsys.readouterr().out.strip())
    for key, value in details.items():
        assert stdout_record[key] == value


def test_a_write_failure_in_the_db_sink_never_raises_into_the_caller(capsys):
    fake = _FakePersistence(raise_on_write=RuntimeError("disk full"))
    configure_logging("test_profile", persistence=fake)

    # Must not raise — a broken log sink must never break the request
    # that triggered the log call.
    logging.getLogger("test").info("this must still work")

    # The stdout line still went out — only the DB sink degraded.
    output = capsys.readouterr().out.strip()
    assert json.loads(output)["message"] == "this must still work"


def test_a_write_failure_warns_to_stderr_exactly_once(capsys):
    fake = _FakePersistence(raise_on_write=RuntimeError("disk full"))
    configure_logging("test_profile", persistence=fake)

    logging.getLogger("test").info("first")
    logging.getLogger("test").info("second")
    logging.getLogger("test").info("third")

    stderr = capsys.readouterr().err
    assert stderr.count("disk full") == 1, "the fallback warning must fire once, not once per failed write"


def test_a_record_with_no_active_trace_context_is_passed_through_as_no_trace(capsys):
    fake = _FakePersistence()
    configure_logging("test_profile", persistence=fake)

    logging.getLogger("test").info("outside any trace")

    [(written_trace_id, _details)] = fake.calls
    assert written_trace_id is None  # never an empty string masquerading as "no trace"


# -- Human-readable console formatter (§1.8 follow-up, terminal pass) -------


def test_the_json_stream_is_unaffected_by_the_console_formatter(capsys):
    """The console formatter is a second, additional handler — stdout
    must still be exactly what it always was (existing tests all over
    this suite parse every stdout line as JSON).
    """

    configure_logging("test_profile")
    logging.getLogger("test").info("something happened", extra={"risk_level": "high"})

    stdout = capsys.readouterr().out.strip()
    record = json.loads(stdout)  # would raise if anything non-JSON leaked onto stdout
    assert record["message"] == "something happened"


def test_console_line_has_the_documented_shape(capsys):
    configure_logging("test_profile")

    with trace_context("abcdef1234567890"):
        logging.getLogger("test").info("something happened")

    line = capsys.readouterr().err.strip()
    assert line.startswith("[")
    # [HH:MM:SS] LEVEL  <8-char trace>  <message>
    assert line[9] == "]"
    assert "abcdef12" in line  # first 8 characters of the trace ID
    assert "abcdef1234567890" not in line  # not the full ID — only 8 characters


def test_console_line_shows_eight_dashes_when_there_is_no_trace_id(capsys):
    configure_logging("test_profile")
    logging.getLogger("test").info("outside any trace")

    line = capsys.readouterr().err.strip()
    assert "--------" in line


def test_console_line_for_a_known_event_is_a_curated_summary_not_a_dumped_dict(capsys):
    configure_logging("test_profile")
    logging.getLogger("api.ingestion").info(
        "intent classified",
        extra={"event": "intent_classified", "intent": "question", "reason": "greeting, no actionable content"},
    )

    line = capsys.readouterr().err.strip()
    assert "intent classified" in line
    assert "question" in line
    assert "greeting, no actionable content" in line
    assert "{" not in line  # never a raw dict/extra dump
    assert "event" not in line.split("→")[0]  # the field name itself never leaks into the summary


def test_console_line_for_an_unrecognized_event_falls_back_to_the_raw_message(capsys):
    configure_logging("test_profile")
    logging.getLogger("httpx").info("HTTP Request: POST https://example.com/ \"HTTP/1.1 200 OK\"")

    line = capsys.readouterr().err.strip()
    assert "HTTP Request" in line


def test_console_line_truncates_a_long_value_instead_of_wrapping(capsys):
    configure_logging("test_profile")
    long_reason = "x" * 300
    logging.getLogger("orchestrator.flows").info(
        "risk assessed",
        extra={"event": "risk_assessed", "risk_level": "high", "risk_score": 0.9, "risk_reason": long_reason},
    )

    line = capsys.readouterr().err.strip()
    assert len(line) < 200  # nowhere near the full 300-character reason
    assert "…" in line


def test_console_line_distinguishes_no_match_from_ambiguous_protocol_selection(capsys):
    # Found live: before this fix, any non-"selected" status rendered as
    # "ambiguous among [...]" here — so a real NO_MATCH selection (a
    # distinct orchestrator.main_agent status from "ambiguous") printed as
    # "protocol selection → ambiguous among []", misleading during manual
    # testing even though the actually-stored outcome was correctly
    # "no_match_protocol" the whole time.
    configure_logging("test_profile")
    logging.getLogger("orchestrator.flows").info(
        "protocol selection",
        extra={"event": "protocol_selection", "status": "no_match", "candidate_names": [], "reason": "nothing loaded fits"},
    )

    line = capsys.readouterr().err.strip()
    assert "no match" in line
    assert "ambiguous" not in line
    assert "nothing loaded fits" in line


def test_console_line_still_renders_a_genuine_ambiguous_selection_correctly(capsys):
    configure_logging("test_profile")
    logging.getLogger("orchestrator.flows").info(
        "protocol selection",
        extra={"event": "protocol_selection", "status": "ambiguous", "candidate_names": ["status_check", "routine_check"], "reason": "tie"},
    )

    line = capsys.readouterr().err.strip()
    assert "ambiguous among [status_check, routine_check]" in line


def test_console_formatter_does_not_prevent_the_db_sink_from_receiving_full_detail(capsys):
    """All three handlers read from the same call — the console line being
    short must never mean the DB sink (or stdout JSON) got the same short
    version.
    """

    fake = _FakePersistence()
    configure_logging("test_profile", persistence=fake)

    long_reason = "y" * 300
    logging.getLogger("orchestrator.flows").info(
        "risk assessed",
        extra={"event": "risk_assessed", "risk_level": "high", "risk_score": 0.9, "risk_reason": long_reason},
    )

    console_line = capsys.readouterr().err
    assert long_reason not in console_line  # short on the console

    [(_trace_id, details)] = fake.calls
    assert details["risk_reason"] == long_reason  # full, unabbreviated in the DB sink


# -- LOG_CONSOLE_JSON opt-out (§1.8 follow-up) -------------------------------


def test_default_behavior_is_unchanged_json_on_stdout_human_readable_on_stderr(capsys):
    """The exact scenario every existing test in this suite relies on,
    stated explicitly: nothing sets LOG_CONSOLE_JSON, so
    `config.base.LOG_CONSOLE_JSON_ENABLED` is on, and `configure_logging`
    behaves exactly as it did before this flag existed.
    """

    assert base_config.LOG_CONSOLE_JSON_ENABLED is True  # the actual default, not a test-only assumption

    configure_logging("test_profile")
    logging.getLogger("test").info("something happened", extra={"event": "intent_classified", "intent": "question", "reason": "why"})

    captured = capsys.readouterr()
    stdout_record = json.loads(captured.out.strip())
    assert stdout_record["message"] == "something happened"

    assert "intent classified" in captured.err  # the human-readable line is still there too


def test_disabling_console_json_removes_only_the_stdout_handler(capsys, monkeypatch):
    monkeypatch.setattr(base_config, "LOG_CONSOLE_JSON_ENABLED", False)

    configure_logging("test_profile")
    logging.getLogger("test").info("something happened", extra={"event": "intent_classified", "intent": "question", "reason": "why"})

    captured = capsys.readouterr()
    assert captured.out == ""  # nothing at all on stdout — not even a blank line
    assert "intent classified" in captured.err  # the human-readable console line is unaffected


def test_disabling_console_json_never_affects_the_db_sink(capsys, monkeypatch):
    monkeypatch.setattr(base_config, "LOG_CONSOLE_JSON_ENABLED", False)

    fake = _FakePersistence()
    configure_logging("test_profile", persistence=fake)
    logging.getLogger("test").info("something happened", extra={"event": "intent_classified", "intent": "question", "reason": "why"})

    assert capsys.readouterr().out == ""  # confirms the flag actually took effect

    [(_trace_id, details)] = fake.calls
    assert details["message"] == "something happened"
    assert details["reason"] == "why"  # full detail, unaffected by the console flag either way

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
