"""Bounded event queues with legacy serial and policy-aware modes."""

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Callable

from tools import get_trace_id, stage_context, trace_context

logger = logging.getLogger(__name__)
_STOP = object()


class EventQueueFullError(Exception):
    pass


@dataclass(frozen=True)
class QueueReservation:
    token: int
    continuation: bool = False


@dataclass(frozen=True)
class WorkItem:
    payload: object
    trace_id: str = ""
    priority: int = 10
    deadline_monotonic: float | None = None
    concurrency_keys: tuple[str, ...] = ()
    submitted_at: float = field(default_factory=time.monotonic)


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

    def reserve(self, continuation: bool = False) -> QueueReservation:
        return QueueReservation(0, continuation)

    def release_reservation(self, reservation: QueueReservation) -> None:
        return None

    def submit(self, queued_item, reservation: QueueReservation | None = None) -> None:
        self._queue.put(queued_item if isinstance(queued_item, WorkItem) else WorkItem(queued_item))

    def qsize(self) -> int:
        return self._queue.qsize()

    def currently_processing(self) -> object | None:
        return self._currently_processing

    def wait_until_idle(self) -> None:
        self._queue.join()

    def stop(self) -> None:
        self._queue.put(_STOP)
        self._worker.join()

    def _run(self) -> None:
        while True:
            queued = self._queue.get()
            if queued is _STOP:
                self._queue.task_done()
                return
            work_item: WorkItem = queued
            queued_item = work_item.payload
            self._currently_processing = queued_item
            try:
                with trace_context(work_item.trace_id or None):
                    logger.info(
                        "queue item started",
                        extra={
                            "event": "queue_started",
                            "trace_id": get_trace_id(),
                            "queue_wait_seconds": time.monotonic() - work_item.submitted_at,
                            "telemetry_only": True,
                        },
                    )
                    with stage_context("queue_execution"):
                        self._process_fn(queued_item)
            except Exception:
                logger.exception(
                    "event processing failed; continuing with the next queued event",
                    extra={"event": "queue_processing_failed", "item": repr(queued_item)},
                )
            finally:
                self._currently_processing = None
                self._queue.task_done()


class PolicyAwareEventQueue:
    def __init__(
        self,
        process_fn: Callable[[object], None],
        *,
        workers: int = 4,
        max_size: int = 100,
        reserved_continuation_percent: int = 20,
    ):
        self._process_fn = process_fn
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._workers = [threading.Thread(target=self._run, daemon=True) for _ in range(workers)]
        self._started = False
        self._sequence = count()
        self._reservation_sequence = count(1)
        self._max_size = max_size
        self._continuation_capacity = max(1, int(max_size * reserved_continuation_percent / 100))
        self._state_lock = threading.Lock()
        self._reserved_total = 0
        self._reserved_normal = 0
        self._currently_processing: dict[int, object] = {}
        self._resource_locks: dict[str, threading.Lock] = {}
        self._resource_lock_guard = threading.Lock()
        self._key_condition = threading.Condition()
        self._key_sequences: dict[str, list[int]] = {}

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for worker in self._workers:
            worker.start()

    def reserve(self, continuation: bool = False) -> QueueReservation | None:
        with self._state_lock:
            normal_capacity = self._max_size - self._continuation_capacity
            if self._reserved_total >= self._max_size:
                return None
            if not continuation and self._reserved_normal >= normal_capacity:
                return None
            self._reserved_total += 1
            if not continuation:
                self._reserved_normal += 1
            return QueueReservation(next(self._reservation_sequence), continuation)

    def release_reservation(self, reservation: QueueReservation) -> None:
        with self._state_lock:
            self._reserved_total = max(0, self._reserved_total - 1)
            if not reservation.continuation:
                self._reserved_normal = max(0, self._reserved_normal - 1)

    def submit(self, queued_item, reservation: QueueReservation | None = None) -> None:
        active_reservation = reservation or self.reserve(False)
        if active_reservation is None:
            raise EventQueueFullError("event queue is full")
        work_item = queued_item if isinstance(queued_item, WorkItem) else WorkItem(queued_item)
        item_sequence = next(self._sequence)
        with self._key_condition:
            for key in work_item.concurrency_keys:
                self._key_sequences.setdefault(key, []).append(item_sequence)
        self._queue.put((work_item.priority, item_sequence, active_reservation, work_item))

    def qsize(self) -> int:
        return self._queue.qsize()

    def currently_processing(self) -> object | None:
        with self._state_lock:
            return next(iter(self._currently_processing.values()), None)

    def wait_until_idle(self) -> None:
        self._queue.join()

    def stop(self) -> None:
        for _worker in self._workers:
            self._queue.put((10**9, next(self._sequence), QueueReservation(0, True), _STOP))
        for worker in self._workers:
            worker.join()

    def _locks_for(self, keys: tuple[str, ...]) -> list[threading.Lock]:
        with self._resource_lock_guard:
            return [self._resource_locks.setdefault(key, threading.Lock()) for key in sorted(set(keys))]

    def _run(self) -> None:
        worker_id = threading.get_ident()
        while True:
            _priority, item_sequence, reservation, queued = self._queue.get()
            if queued is _STOP:
                self._queue.task_done()
                return

            work_item: WorkItem = queued
            payload = work_item.payload
            locks = self._locks_for(work_item.concurrency_keys)
            with self._key_condition:
                while any(self._key_sequences[key][0] != item_sequence for key in work_item.concurrency_keys):
                    self._key_condition.wait()
            with self._state_lock:
                self._currently_processing[worker_id] = payload
            try:
                logger.info(
                    "queue item started",
                    extra={
                        "event": "queue_started",
                        "trace_id": work_item.trace_id,
                        "queue_wait_seconds": time.monotonic() - work_item.submitted_at,
                        "telemetry_only": True,
                    },
                )
                if work_item.deadline_monotonic is not None and time.monotonic() >= work_item.deadline_monotonic:
                    logger.warning("queue item deadline expired", extra={"event": "queue_deadline_expired", "item": repr(payload)})
                else:
                    for resource_lock in locks:
                        resource_lock.acquire()
                    with trace_context(work_item.trace_id or None), stage_context("queue_execution"):
                        self._process_fn(payload)
            except Exception:
                logger.exception("event processing failed; continuing", extra={"event": "queue_processing_failed", "item": repr(payload)})
            finally:
                for resource_lock in reversed(locks):
                    if resource_lock.locked():
                        resource_lock.release()
                with self._key_condition:
                    for key in work_item.concurrency_keys:
                        sequences = self._key_sequences[key]
                        sequences.remove(item_sequence)
                        if not sequences:
                            del self._key_sequences[key]
                    self._key_condition.notify_all()
                with self._state_lock:
                    self._currently_processing.pop(worker_id, None)
                self.release_reservation(reservation)
                self._queue.task_done()
