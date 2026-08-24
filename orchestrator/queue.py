"""Serial event processing (work_plan.md §6.15).

A single background worker draining a queue one item at a time, in
arrival order, calling `process_fn(item)` for each — generic over what
"processing" means, so it doesn't need the (deferred) new-event flow
(§6.11) to exist. An exception from `process_fn` is logged and swallowed;
the worker moves on to the next item regardless — a failed run must never
stop the queue.

What this class does *not* do: recover in-flight or held events from the
database on restart. §6.15's "the queue survives nothing... a restart
replays from the record" is trivially true of an in-memory queue — actually
re-scanning persistence for unfinished work on startup is a startup-
sequence concern for whichever future piece (§7/§9) assembles the running
system, not something this generic mechanism does itself.

Coordinates with persistence's write queue (§2.9) by not touching it at
all: this queue is about *ordering* — one event at a time, in order —
persistence's is about SQLite's locking. Different concerns, not
duplicated here. `process_fn` is free to call persistence normally;
nothing here serializes around it beyond running one item at a time.
"""

import logging
import queue
import threading
from typing import Callable

logger = logging.getLogger(__name__)

_STOP = object()


class SerialEventQueue:
    def __init__(self, process_fn: Callable[[object], None]):
        self._process_fn = process_fn
        self._queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._currently_processing = None

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._worker.start()

    def submit(self, item) -> None:
        self._queue.put(item)

    def qsize(self) -> int:
        """How many submitted items are still waiting — not yet picked up
        by the worker (§7.7's "how many events are queued"). Deliberately
        excludes whatever is currently being processed; see
        `currently_processing`.
        """

        return self._queue.qsize()

    def currently_processing(self) -> object | None:
        """The raw item the worker is processing right now, or None.

        Returned exactly as submitted — this class stays fully generic
        over what an "item" is (work_plan.md §6.15's own design), so it
        never interprets one. A caller that submits `(event_id, work_fn)`
        pairs (as api/ does, for job-status derivation — §7.2) reads
        `.currently_processing()[0]` itself; nothing here assumes that
        shape. A single-attribute read/write is safe across threads
        without a lock — CPython's GIL makes both atomic, and only the
        worker thread ever writes it.
        """

        return self._currently_processing

    def wait_until_idle(self) -> None:
        """Block until every item submitted so far has been processed.

        For tests and any caller that needs a deterministic point to
        check results — the worker itself never waits on this.
        """

        self._queue.join()

    def stop(self) -> None:
        self._queue.put(_STOP)
        self._worker.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()

            if item is _STOP:
                self._queue.task_done()
                return

            self._currently_processing = item
            try:
                self._process_fn(item)
            except Exception:
                logger.exception("event processing failed; continuing with the next queued event", extra={"event": "queue_processing_failed"})
            finally:
                self._currently_processing = None
                self._queue.task_done()
