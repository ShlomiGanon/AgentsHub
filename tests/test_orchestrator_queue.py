import threading
import time

from orchestrator.queue import SerialEventQueue


def test_items_are_processed_in_strict_arrival_order():
    processed = []
    q = SerialEventQueue(processed.append)
    q.start()

    for i in range(10):
        q.submit(i)

    q.wait_until_idle()
    q.stop()

    assert processed == list(range(10))


def test_one_failing_item_does_not_stop_subsequent_items():
    processed = []

    def _process(item):
        if item == "bad":
            raise ValueError("boom")
        processed.append(item)

    q = SerialEventQueue(_process)
    q.start()

    q.submit("first")
    q.submit("bad")
    q.submit("third")
    q.wait_until_idle()
    q.stop()

    assert processed == ["first", "third"]


def test_processing_is_serial_not_concurrent():
    # A slow item must finish before the next one starts — this would
    # fail if items were processed concurrently.
    order = []
    lock_held = threading.Event()

    def _process(item):
        assert not lock_held.is_set(), "two items were being processed at once"
        lock_held.set()
        time.sleep(0.02)
        order.append(item)
        lock_held.clear()

    q = SerialEventQueue(_process)
    q.start()

    for i in range(5):
        q.submit(i)

    q.wait_until_idle()
    q.stop()

    assert order == [0, 1, 2, 3, 4]


def test_wait_until_idle_blocks_until_processing_actually_finished():
    processed = []

    def _slow_process(item):
        time.sleep(0.05)
        processed.append(item)

    q = SerialEventQueue(_slow_process)
    q.start()
    q.submit("x")
    q.wait_until_idle()

    assert processed == ["x"]
    q.stop()


def test_start_is_idempotent():
    processed = []
    q = SerialEventQueue(processed.append)

    q.start()
    q.start()  # must not raise or start a second worker
    q.submit("only-once")
    q.wait_until_idle()
    q.stop()

    assert processed == ["only-once"]
