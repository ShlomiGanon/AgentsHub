"""history/retrieval.py and history/precedent.py — two related, real bugs
found and fixed during Mission 9's integration testing (work_plan.md
§9.19/§9.20).

Bug 1 — format mismatch: `retrieve_range`'s raw-event fallback builds its
query bounds via `history.time_utils.storage_timestamp` (whole-second
precision, no timezone suffix). Events written with a raw
`datetime.now(timezone.utc).isoformat()` timestamp (as
`api/events.py`/`api/messages.py`'s own `_now()` used to) carry
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
second, scoped to `history/precedent.py::find_precedents` only (not
`retrieve_range`'s general contract, which `history/query.py` also
depends on and which was not shown to have the same problem).

Both fixes verified empirically before being written up here: 15 repeated
real-time runs failed ~80% of the time before Bug 2's fix and passed
15/15 after it — see `docs/progress.md`'s §9.19/§9.20 entries for the
full diagnosis. The tests below are the deterministic version of that
same check, not dependent on real-time luck to reproduce.
"""

from datetime import datetime, timezone

from api.events import _now as events_now
from api.messages import _now as messages_now
from history.precedent import find_precedents
from history.retrieval import retrieve_range
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
