"""persistence/sqlite_backend.py's own concurrency guarantee (work_plan.md
§2.9): one serialized writer thread draining a queue, backed by SQLite's
WAL mode, so concurrent readers proceed without blocking on an in-flight
write. Every other persistence test drives the backend single-threaded —
this file is the one place that guarantee is actually exercised under
real multi-threaded contention, with a real temporary file (concurrent
writers against `:memory:` wouldn't even share a database), not mocked or
asyncio-simulated.
"""

import threading

from persistence.sqlite_backend import SQLitePersistence

THREAD_JOIN_TIMEOUT_SECONDS = 30  # generous — a hang here means a real deadlock, not slowness


def _minimal_event(**overrides):
    event = {
        "received_at": "2026-08-01T10:00:00",
        "source": "sensor",
        "sender_identity": "sensor-1",
        "occurred_at": "2026-08-01T10:00:00",
        "raw_text": "text",
    }
    event.update(overrides)
    return event


def _run_threads(targets):
    """Start every callable in `targets` as its own thread, join them all
    with a timeout, and return which ones (by index) never finished —
    an empty list means no deadlock/hang.
    """

    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(THREAD_JOIN_TIMEOUT_SECONDS)

    return [i for i, thread in enumerate(threads) if thread.is_alive()]


def test_concurrent_appends_from_many_threads_lose_nothing(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "concurrent_append.db"))
    thread_count = 25
    results: list = [None] * thread_count
    errors: list = [None] * thread_count

    def _append(i):
        def _target():
            try:
                results[i] = persistence.append_event(_minimal_event(raw_text=f"event-from-thread-{i}"))
            except BaseException as exc:  # capture in the test thread, not silently swallow in the worker
                errors[i] = exc

        return _target

    try:
        hung = _run_threads([_append(i) for i in range(thread_count)])

        assert hung == [], f"thread(s) {hung} never finished — a real deadlock, not just slowness"
        assert errors == [None] * thread_count, f"write(s) raised: {[e for e in errors if e is not None]}"

        event_ids = results
        assert len(event_ids) == len(set(event_ids)) == thread_count  # every write got its own real ID, none lost or collided

        # Every event is individually present and intact — not just "N rows exist."
        all_events = persistence.fetch_events_range("2026-01-01T00:00:00", "2026-12-31T00:00:00")
        assert len(all_events) == thread_count
        raw_texts_by_id = {e["event_id"]: e["raw_text"] for e in all_events}
        for i, event_id in enumerate(event_ids):
            assert raw_texts_by_id[event_id] == f"event-from-thread-{i}"
    finally:
        persistence.close()


def test_concurrent_updates_to_different_events_are_all_applied(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "concurrent_update.db"))
    thread_count = 20

    try:
        event_ids = [persistence.append_event(_minimal_event(raw_text=f"e{i}")) for i in range(thread_count)]
        errors: list = [None] * thread_count

        def _update(i):
            def _target():
                try:
                    persistence.update_event(event_ids[i], {"risk_level": f"level-{i}", "risk_reason": f"reason-{i}"})
                except BaseException as exc:
                    errors[i] = exc

            return _target

        hung = _run_threads([_update(i) for i in range(thread_count)])

        assert hung == []
        assert errors == [None] * thread_count, f"update(s) raised: {[e for e in errors if e is not None]}"

        for i, event_id in enumerate(event_ids):
            event = persistence.fetch_event(event_id)
            assert event["risk_level"] == f"level-{i}"
            assert event["risk_reason"] == f"reason-{i}"
    finally:
        persistence.close()


def test_concurrent_readers_during_a_write_burst_see_only_whole_events_never_partial(tmp_path):
    persistence = SQLitePersistence(str(tmp_path / "concurrent_read_write.db"))
    writer_count = 15
    reader_count = 10
    reader_errors: list = [None] * reader_count
    reader_saw_a_row = [False] * reader_count
    stop_reading = threading.Event()

    def _write(i):
        def _target():
            persistence.append_event(_minimal_event(raw_text=f"event-{i}"))

        return _target

    def _read(i):
        def _target():
            try:
                while not stop_reading.is_set():
                    for event in persistence.fetch_events_range("2026-01-01T00:00:00", "2026-12-31T00:00:00"):
                        # WAL mode's own guarantee: a reader never sees a row
                        # mid-INSERT — every field required at append time
                        # must already be present and well-formed, not a
                        # half-written row.
                        assert event["event_id"]
                        assert event["raw_text"].startswith("event-")
                        assert event["source"] == "sensor"
                        reader_saw_a_row[i] = True
            except BaseException as exc:
                reader_errors[i] = exc

        return _target

    try:
        readers = [threading.Thread(target=_read(i)) for i in range(reader_count)]
        for reader in readers:
            reader.start()

        hung = _run_threads([_write(i) for i in range(writer_count)])
        assert hung == [], f"writer thread(s) {hung} never finished — a real deadlock"

        stop_reading.set()
        for reader in readers:
            reader.join(THREAD_JOIN_TIMEOUT_SECONDS)
        assert not any(reader.is_alive() for reader in readers), "a reader never noticed the stop signal — blocked on the writer"

        assert reader_errors == [None] * reader_count, f"reader(s) saw corrupted/partial data: {[e for e in reader_errors if e is not None]}"
        assert len(persistence.fetch_events_range("2026-01-01T00:00:00", "2026-12-31T00:00:00")) == writer_count
        # Not asserting every reader caught a row mid-flight (scheduling is
        # not guaranteed) — only that whichever ones did never saw anything
        # broken, and none of them ever blocked long enough to hang.
    finally:
        persistence.close()
