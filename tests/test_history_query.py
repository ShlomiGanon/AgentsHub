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

# -- answer_most_recent_event (orchestrator.reasoning's direct-lookup
# path, question-flow-repros follow-up) ------------------------------------


def test_answer_most_recent_event_picks_the_latest_by_occurred_at_not_insertion_order(tmp_path):
    store = open_persistence(str(tmp_path / "query-recent.db"))
    try:
        # Inserted out of chronological order on purpose — the pick must
        # be by occurred_at, never by row/insertion order.
        store.append_event({
            "event_id": "e-later", "received_at": "2026-08-02T09:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2026-08-02T09:00:00", "raw_text": "medical incident",
            "classification": "medical", "area": "south",
        })
        store.append_event({
            "event_id": "e-earlier", "received_at": "2026-08-01T10:00:00", "source": "sensor",
            "sender_identity": "s", "occurred_at": "2026-08-01T10:00:00", "raw_text": "fire",
            "classification": "fire", "area": "north",
        })

        agent = FakeHistoryAgent()
        service = HistoryQueryService(store, agent)

        answer = service.answer_most_recent_event("what is the last event?")

        assert answer.answer == "answer from stored context"
        assert answer.total_events_matched == 1
        assert answer.sources_used[0].source_id == "e-later"
        assert answer.time_start == "2026-08-02T09:00:00"
        # The History Agent is still the one interpreting — it's handed
        # the retrieved event's real content, not a bare question (§5.7).
        assert "e-later" in agent.last_prompt
        assert "medical incident" in agent.last_prompt  # the real event content, not a stub or a bare question
    finally:
        store.close()


def test_answer_most_recent_event_raises_a_clean_error_when_nothing_has_been_recorded(tmp_path):
    from history.query import HistoryQueryError

    store = open_persistence(str(tmp_path / "query-empty.db"))
    try:
        service = HistoryQueryService(store, FakeHistoryAgent())

        try:
            service.answer_most_recent_event("what is the last event?")
            assert False, "expected HistoryQueryError"
        except HistoryQueryError as exc:
            assert "no events" in str(exc)
    finally:
        store.close()

"""history/query.py and history/query.py — two related, real bugs
found and fixed during Mission 9's integration testing (work_plan.md
§9.19/§9.20).

Bug 1 — format mismatch: `retrieve_range`'s raw-event fallback builds its
query bounds via `history.time_utils.storage_timestamp` (whole-second
precision, no timezone suffix). Events written with a raw
`datetime.now(timezone.utc).isoformat()` timestamp (as
`api/ingestion.py`/`api/ingestion.py`'s own `_now()` used to) carry
microsecond precision and a `+00:00` suffix — `occurred_at` is a plain
TEXT column compared lexicographically, so an event occurring earlier in
the same second as a query's upper bound could still sort *after* the
truncated bound string and be silently excluded. Fixed by making `_now()`
go through `storage_timestamp` too, so every `occurred_at`/`received_at`
value in the table shares one comparable format.

Bug 2 — same-second boundary: fixing Bug 1 alone was not enough. Once
every timestamp shares one whole-second-precision format, two events
genuinely milliseconds apart (exactly what a real burst produces) can
share an *identical* truncated `occurred_at` string. `find_precedents`'s
window used a strict, exclusive upper bound at the target event's own
timestamp, so a same-second precedent was still never found — equal is
not less than. Fixed by widening precedent search's own window by one
second, scoped to `history/query.py::find_precedents` only (not
`retrieve_range`'s general contract, which `history/query.py` also
depends on and which was not shown to have the same problem).

Both fixes verified empirically before being written up here: 15 repeated
real-time runs failed ~80% of the time before Bug 2's fix and passed
15/15 after it — see `docs/progress.md`'s §9.19/§9.20 entries for the
full diagnosis. The tests below are the deterministic version of that
same check, not dependent on real-time luck to reproduce.
"""

from datetime import datetime, timezone

from api.ingestion import _now as events_now
from api.ingestion import _now as messages_now
from history.query import find_precedents, retrieve_range
from history.time_utils import parse_timestamp, storage_timestamp
from persistence.interface import open_persistence


class _FakeSettingsStore:
    def get_lookback_window_days(self) -> int:
        return 30


def test_now_helpers_produce_storage_timestamp_compatible_format():
    for now_fn in (events_now, messages_now):
        value = now_fn()
        assert "." not in value, f"{now_fn.__module__}._now() kept sub-second precision: {value!r}"
        assert "+" not in value, f"{now_fn.__module__}._now() kept a timezone suffix: {value!r}"
        assert storage_timestamp(parse_timestamp(value)) == value


def test_a_same_second_event_is_found_as_a_precedent(tmp_path):
    store = open_persistence(str(tmp_path / "retrieval.db"))
    try:
        # Two events sharing the exact same whole-second occurred_at —
        # the deterministic version of what a real burst produces.
        earlier = store.append_event({
            "received_at": "2026-08-24T19:42:07", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T19:42:07", "raw_text": "fire at gate 3",
            "classification": "fire", "area": "north_sector", "outcome": "succeeded",
        })

        precedents = find_precedents(store, _FakeSettingsStore(), "target-event-id", "fire", "north_sector", "2026-08-24T19:42:07")

        assert earlier in {p.event_id for p in precedents}, "a same-second precedent was silently missed"
    finally:
        store.close()


def test_the_widened_window_does_not_pull_in_a_genuinely_later_event(tmp_path):
    store = open_persistence(str(tmp_path / "retrieval2.db"))
    try:
        # One full second after the target — must never be treated as
        # its own precedent (that would be looking into the future).
        later = store.append_event({
            "received_at": "2026-08-24T19:42:08", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T19:42:08", "raw_text": "fire at gate 3",
            "classification": "fire", "area": "north_sector", "outcome": "succeeded",
        })

        precedents = find_precedents(store, _FakeSettingsStore(), "target-event-id", "fire", "north_sector", "2026-08-24T19:42:07")

        assert later not in {p.event_id for p in precedents}
    finally:
        store.close()


def test_retrieve_range_itself_is_unaffected_by_the_precedent_specific_widening(tmp_path):
    # The fix is scoped to find_precedents; retrieve_range's own general
    # contract (used directly by history/query.py) keeps its ordinary
    # half-open [start, end) semantics.
    store = open_persistence(str(tmp_path / "retrieval3.db"))
    try:
        event_id = store.append_event({
            "received_at": "2026-08-24T19:42:07", "source": "sensor", "sender_identity": "sensor-1",
            "occurred_at": "2026-08-24T19:42:07", "raw_text": "fire at gate 3",
            "classification": "fire", "area": "north_sector", "outcome": "succeeded",
        })

        # A window ending exactly at the event's own second, half-open,
        # correctly excludes it — unchanged, ordinary behavior.
        sources = retrieve_range(
            store,
            datetime(2026, 8, 24, 19, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 24, 19, 42, 7, tzinfo=timezone.utc),
            "fire", "north_sector",
        )
        assert event_id not in {s.source_id for s in sources if s.level == "raw_event"}
    finally:
        store.close()


def _history_event(event_id, occurred_at, classification="fire", area="north", outcome="succeeded"):
    return {
        "event_id": event_id,
        "received_at": occurred_at,
        "source": "sensor",
        "sender_identity": "sensor-1",
        "occurred_at": occurred_at,
        "raw_text": f"{classification} in {area}",
        "classification": classification,
        "area": area,
        "outcome": outcome,
    }


def test_structured_count_is_computed_by_the_database_without_calling_the_history_agent(tmp_path):
    from history.contracts import HistoryQuerySpec

    store = open_persistence(str(tmp_path / "structured-count.db"))
    try:
        store.append_event(_history_event("f1", "2026-08-01T10:00:00"))
        store.append_event(_history_event("f2", "2026-08-02T10:00:00"))
        store.append_event(_history_event("m1", "2026-08-02T11:00:00", classification="medical"))
        agent = FakeHistoryAgent()
        service = HistoryQueryService(store, agent, classifications=("fire", "medical"), areas=("north",))

        answer = service.query_spec(
            "How many fires were there?",
            HistoryQuerySpec(
                operation="count",
                time_start="2026-08-01T00:00:00",
                time_end="2026-08-03T00:00:00",
                classifications=("fire",),
            ),
        )

        assert answer.total_events_matched == 2
        assert answer.answer == "2 matching events."
        assert agent.last_prompt is None
    finally:
        store.close()


def test_structured_latest_uses_the_filtered_database_result(tmp_path):
    from history.contracts import HistoryQuerySpec

    store = open_persistence(str(tmp_path / "structured-latest.db"))
    try:
        store.append_event(_history_event("north-old", "2026-08-01T10:00:00"))
        store.append_event(_history_event("north-new", "2026-08-03T10:00:00"))
        store.append_event(_history_event("south-newer", "2026-08-04T10:00:00", area="south"))
        service = HistoryQueryService(store, FakeHistoryAgent(), classifications=("fire",), areas=("north", "south"))

        answer = service.query_spec(
            "What is the latest northern fire?",
            HistoryQuerySpec(operation="latest", classifications=("fire",), areas=("north",)),
        )

        assert answer.sources_used[0].source_id == "north-new"
        assert "north-new" in answer.answer
    finally:
        store.close()


def test_structured_aggregate_groups_counts_in_sql(tmp_path):
    from history.contracts import HistoryQuerySpec

    store = open_persistence(str(tmp_path / "structured-aggregate.db"))
    try:
        store.append_event(_history_event("n1", "2026-08-01T10:00:00"))
        store.append_event(_history_event("n2", "2026-08-02T10:00:00"))
        store.append_event(_history_event("s1", "2026-08-02T11:00:00", area="south"))
        service = HistoryQueryService(store, FakeHistoryAgent(), areas=("north", "south"))

        answer = service.query_spec(
            "Compare north and south",
            HistoryQuerySpec(operation="compare", group_by="area"),
        )

        assert "north: 2" in answer.answer
        assert "south: 1" in answer.answer
    finally:
        store.close()


def test_structured_query_rejects_unknown_registry_values_before_search(tmp_path):
    import pytest

    from history.contracts import HistoryQueryError, HistoryQuerySpec

    store = open_persistence(str(tmp_path / "structured-validation.db"))
    try:
        service = HistoryQueryService(store, FakeHistoryAgent(), classifications=("fire",))

        with pytest.raises(HistoryQueryError, match="unknown classification"):
            service.query_spec("Any floods?", HistoryQuerySpec(operation="count", classifications=("flood",)))
    finally:
        store.close()
