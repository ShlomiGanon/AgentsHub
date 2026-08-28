"""Serial event processing (work_plan.md §6.15)."""

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
        """How many submitted items are still waiting — not yet picked up by the worker (§7.7's "how many events are queued")."""

        return self._queue.qsize()

    def currently_processing(self) -> object | None:
        """The raw item the worker is processing right now, or None."""

        return self._currently_processing

    def wait_until_idle(self) -> None:
        """Block until every item submitted so far has been processed."""

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
                logger.exception(
                    "event processing failed; continuing with the next queued event",
                    extra={"event": "queue_processing_failed", "item": repr(item)},
                )
            finally:
                self._currently_processing = None
                self._queue.task_done()
