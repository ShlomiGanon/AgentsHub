"""The DB-backed structured-log sink, end to end (work_plan.md §1.8 follow-up).

Drives one event through a real running API (mirroring
`tests/test_integration_end_to_end_flow.py`'s own harness exactly) with the
DB-backed log sink attached, then queries `persistence.fetch_log_entries`
directly — not stdout — and confirms a complete, correctly-ordered set of
rows for that one trace ID, across every module involved. Complements
`tests/test_persistence_conformance.py` (which proves the write/read path
works in isolation) and `tests/test_logging.py` (which proves the handler
itself behaves correctly against a fake) — this is the one place a real
request, a real SQLite file, and the real handler all run together.
"""

import io
import json
import types

from agents import adapter
from tests.api_fakes import SENSOR_IDENTITY, RunningApiServer, build_context, happy_path_agent
from tools.logging_config import configure_logging
from tools.simulator import _post_event


def _fake_crewai(response_text="status nominal, no anomalies"):
    class _FakeOutput:
        def __init__(self, raw):
            self.raw = raw

    class _FakeCrewAgent:
        def __init__(self, **kwargs):
            pass

        def kickoff(self, text):
            return _FakeOutput(response_text)

    return types.SimpleNamespace(Agent=_FakeCrewAgent, tools=types.SimpleNamespace(BaseTool=object))


def test_querying_by_trace_id_returns_every_log_row_for_one_request_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_get_crewai", lambda: _fake_crewai())

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    agent = happy_path_agent(risk_score="0.2", selected="status_check", verdict="success")
    ctx = build_context(tmp_path, main_agent=agent)
    configure_logging("test-log-sink-profile", persistence=ctx.deps.persistence)

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "smoke observed near gate 3, north_sector")
        assert result["status_code"] == 202
        event_id = result["event_id"]

        ctx.queue.wait_until_idle()

    # -- Find this run's own trace ID from the DB alone, not from stdout —
    # a real operator debugging after the fact has only the DB to work
    # from. The event's own extraction-result row is the natural anchor:
    # it's the first row that carries this event_id and always fires.
    stdout_lines = [line for line in captured.getvalue().splitlines() if line.strip()]
    stdout_records = [json.loads(line) for line in stdout_lines]
    [extraction_stdout_record] = [r for r in stdout_records if r.get("event") == "extraction_result" and r.get("event_id") == event_id]
    the_trace_id = extraction_stdout_record["trace_id"]
    assert the_trace_id

    entries = ctx.deps.persistence.fetch_log_entries(the_trace_id)
    assert entries, "no log entries were written to the DB for this trace at all"

    # -- Correct order: id is strictly increasing (the AUTOINCREMENT
    # ordering guarantee `write_log_entry`/`fetch_log_entries` document),
    # and the named events appear in the sequence the flow actually
    # produces them.
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)  # no duplicates

    events_in_order = [e["event"] for e in entries if "event" in e]
    expected_order = [
        "report_received", "extraction_result", "risk_assessed", "protocol_selection",
        "precedent_closure", "step_start", "step_result", "insight_generated", "final_verdict", "event_outcome",
    ]
    assert events_in_order == expected_order

    # -- Complete: every row belongs to this trace, and none of the
    # required detail was dropped between the JSON line and the DB row.
    assert all(e["trace_id"] == the_trace_id for e in entries)

    by_event = {e["event"]: e for e in entries if "event" in e}
    assert by_event["report_received"]["event_id"] == event_id
    assert by_event["report_received"]["raw_text"] == "smoke observed near gate 3, north_sector"
    assert by_event["report_received"]["sender_identity"] == SENSOR_IDENTITY

    assert by_event["extraction_result"]["classification"] == "fire"
    assert by_event["extraction_result"]["area"] == "north_sector"

    assert by_event["risk_assessed"]["risk_level"]
    assert by_event["risk_assessed"]["risk_reason"]

    assert by_event["protocol_selection"]["protocol_name"] == "status_check"

    assert by_event["step_start"]["task_text"]
    assert by_event["step_result"]["result_text"]
    assert by_event["step_result"]["succeeded"] is True

    assert by_event["insight_generated"]["insight_text"]
    assert by_event["final_verdict"]["verdict"] == "success"
    assert by_event["event_outcome"]["outcome"] == "succeeded"

    # -- The DB row and the stdout JSON line for the same record agree —
    # the DB is a full copy, not an abbreviated one (the "details" side of
    # each row is built from the exact same helper the JSON formatter is).
    stdout_by_event = {r["event"]: r for r in stdout_records if "event" in r}
    for name in expected_order:
        for key, value in by_event[name].items():
            if key in ("id", "timestamp"):
                continue  # DB-only / not present in the stdout shape
            assert stdout_by_event[name].get(key) == value, f"{name}.{key} differs between the DB row and the stdout line"


def test_werkzeug_access_log_line_carries_the_same_trace_id_as_the_rest_of_the_request(tmp_path, monkeypatch):
    """The gap found during manual verification: querying by trace_id
    reconstructed everything about a request except the one line
    recording the HTTP request itself — werkzeug's own access-log entry
    (e.g. "POST /Event HTTP/1.1 202 -") always landed with trace_id=NULL,
    because it's written by werkzeug's own request handler strictly
    *after* the view function (and any `with trace_context(...)` inside
    it) has already returned and reset the contextvar. Fixed via
    `tools.tracing.set_trace_id` (no auto-reset) plus `api.app.build_app`'s
    `before_request` hook, which clears it fresh at the start of every
    request so an unrelated request never inherits a stale value.
    """

    monkeypatch.setattr(adapter, "_get_crewai", lambda: _fake_crewai())

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    agent = happy_path_agent(risk_score="0.2", selected="status_check", verdict="success")
    ctx = build_context(tmp_path, main_agent=agent)
    configure_logging("test-werkzeug-trace-profile", persistence=ctx.deps.persistence)

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "smoke observed near gate 3, north_sector")
        assert result["status_code"] == 202
        ctx.queue.wait_until_idle()

    stdout_records = [json.loads(line) for line in captured.getvalue().splitlines() if line.strip()]
    [outcome_record] = [r for r in stdout_records if r.get("event") == "event_outcome"]
    the_trace_id = outcome_record["trace_id"]
    assert the_trace_id

    entries = ctx.deps.persistence.fetch_log_entries(the_trace_id)

    werkzeug_rows = [e for e in entries if e.get("logger") == "werkzeug" and "POST /Event" in e.get("message", "")]
    assert werkzeug_rows, "the werkzeug access-log line for this request never landed under this trace ID at all"
    assert werkzeug_rows[0]["trace_id"] == the_trace_id
    assert "202" in werkzeug_rows[0]["message"]

    # Every other row for this request agrees — this isn't a coincidental match.
    assert all(e["trace_id"] == the_trace_id for e in entries)


def test_a_failed_request_still_shares_its_trace_id_with_the_werkzeug_500_line(tmp_path, monkeypatch):
    """Confirms the fix holds on the failure path too: the old `with
    trace_context(...)` wrapping reset the trace ID on the way out even
    when an exception was in flight (a `with` block's `__exit__` always
    runs), so a request that later 500s used to lose its trace ID before
    both `api.errors`'s own log record *and* werkzeug's 500 access-log
    line were written. `set_trace_id` has no such reset, on success or on
    failure.
    """
    import api.events as events_module

    monkeypatch.setattr(adapter, "_get_crewai", lambda: _fake_crewai())
    monkeypatch.setattr(events_module, "begin_report", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    ctx = build_context(tmp_path, main_agent=happy_path_agent())
    configure_logging("test-werkzeug-500-profile", persistence=ctx.deps.persistence)

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "smoke observed near gate 3, north_sector")
        assert result["status_code"] == 500

    stdout_records = [json.loads(line) for line in captured.getvalue().splitlines() if line.strip()]
    [error_record] = [r for r in stdout_records if r.get("event") == "api_unexpected_error"]
    the_trace_id = error_record["trace_id"]
    assert the_trace_id

    entries = ctx.deps.persistence.fetch_log_entries(the_trace_id)

    werkzeug_rows = [e for e in entries if e.get("logger") == "werkzeug" and "POST /Event" in e.get("message", "")]
    assert werkzeug_rows, "the werkzeug access-log line for the failed request never landed under this trace ID"
    assert "500" in werkzeug_rows[0]["message"]
    assert werkzeug_rows[0]["trace_id"] == the_trace_id


def test_a_hold_resolution_is_logged_with_who_answered_and_what_they_chose(tmp_path, monkeypatch):
    """The gap this pass closed beyond §1.8's own literal list: knowing a
    hold was *created* doesn't say who resolved it or what they decided —
    that used to live only in the events/held_events tables, never the log
    stream a trace ID otherwise reconstructs everything else from.
    """

    monkeypatch.setattr(adapter, "_get_crewai", lambda: _fake_crewai())

    agent = happy_path_agent(risk_score="0.9", selected="dispatch_response", verdict="success")
    ctx = build_context(tmp_path, main_agent=agent)
    configure_logging("test-hold-resolution-profile", persistence=ctx.deps.persistence)

    with RunningApiServer(ctx) as server:
        result = _post_event(server.base_url, SENSOR_IDENTITY, "fire confirmed near gate 3, north_sector")
        event_id = result["event_id"]
        ctx.queue.wait_until_idle()

        job = ctx.deps.persistence.fetch_event(event_id)
        assert job["approval_held"] is True  # dispatch_response is flagged — sanity check on the fixture itself

        import urllib.request

        body = json.dumps({"decision": "approved"}).encode("utf-8")
        request = urllib.request.Request(
            f"{server.base_url}/Approve/{event_id}", data=body, method="POST",
            headers={"X-Identity": "commander-1", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:
            assert response.status == 202

        ctx.queue.wait_until_idle()

    # The resolution's own trace ID is a *different* trace than the
    # original ingestion (api/holds.py mints a fresh one per resumption, by
    # design — see that module's docstring) — `fetch_log_entries` needs a
    # trace ID up front, and this interface deliberately has no "list every
    # trace" operation, so the most direct check is a raw scan of the one
    # table this whole feature writes, exactly as an operator with a
    # DB browser and no known trace ID yet would do.
    import sqlite3

    conn = sqlite3.connect(ctx.deps.persistence.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT trace_id, details FROM log_entries ORDER BY id").fetchall()
    conn.close()

    hold_resolved_rows = [json.loads(r["details"]) for r in rows if json.loads(r["details"]).get("event") == "hold_resolved"]
    assert hold_resolved_rows, "no hold_resolved event was logged at all"

    [approval_resolution] = [r for r in hold_resolved_rows if r["hold_kind"] == "approval" and r["event_id"] == event_id]
    assert approval_resolution["resolved_by"] == "commander-1"
    assert approval_resolution["decision"] == "approved"
    assert approval_resolution["status"] == "approved"
