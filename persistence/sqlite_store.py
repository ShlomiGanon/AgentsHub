"""SQLite implementation of the persistence contract."""

import sqlite3
import threading
from concurrent.futures import Future
from queue import SimpleQueue

from persistence.contracts import PersistenceError, PersistenceInterface
from persistence.schema import run_migrations
from persistence.sqlite_events import SqliteEventsMixin
from persistence.sqlite_groups import SqliteGroupsMixin
from persistence.sqlite_jobs import SqliteJobsMixin
from persistence.sqlite_support import _STOP
from persistence.sqlite_users import SqliteUsersMixin
from tools import telemetry_span

class _ReadConnectionLease:
    """Thread-local read connection wrapper used by SQLitePersistence."""

    def __init__(self, connection: sqlite3.Connection):
        """Hold one sqlite3 connection for this thread's reads."""

        self._connection = connection

    def execute(self, *args, **kwargs):
        """Run a read statement on the leased connection."""

        with telemetry_span("sqlite_read", operation="execute"):
            return self._connection.execute(*args, **kwargs)

    def close(self) -> None:
        """No-op; the process owns the connection until SQLitePersistence.close."""

        return None


class SQLitePersistence(
    SqliteEventsMixin,
    SqliteUsersMixin,
    SqliteGroupsMixin,
    SqliteJobsMixin,
    PersistenceInterface,
):
        def __init__(self, db_path: str):
            """Open the DB, run migrations, and start the serialized writer thread."""

            super().__init__(db_path)

            run_migrations(db_path)

            self._write_queue: SimpleQueue = SimpleQueue()
            self._notification_condition = threading.Condition()
            self._notification_generation = 0
            self._log_condition = threading.Condition()
            self._log_generation = 0
            self._read_local = threading.local()
            self._read_connections: list[sqlite3.Connection] = []
            self._read_connections_lock = threading.Lock()
            # Bumped by close(): a thread whose cached read connection belongs to an
            # older generation was closed underneath it and must reconnect lazily.
            self._read_generation = 0
            self._writer_thread = threading.Thread(target=self._run_writer, daemon=True)
            self._writer_thread.start()

        def close(self) -> None:
            """Stop the writer and close cached read connections."""

            self._write_queue.put(_STOP)
            self._writer_thread.join()
            with self._read_connections_lock:
                connections, self._read_connections = self._read_connections, []
                self._read_generation += 1
            for connection in connections:
                connection.close()

        def _run_writer(self) -> None:
            """Apply queued write callables on a single dedicated SQLite connection."""

            connection = sqlite3.connect(self.db_path)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.row_factory = sqlite3.Row

            try:
                while True:
                    job = self._write_queue.get()
                    if job is _STOP:
                        return

                    write_operation, future = job
                    try:
                        future.set_result(write_operation(connection))
                    except BaseException as exc:  # propagated to the caller via the future
                        future.set_exception(exc)
            finally:
                connection.close()

        def _submit_write(self, write_operation):
            # Reentrant writes would wait behind themselves forever.
            """Run a write callable on the writer thread and wait for its result."""

            if threading.current_thread() is self._writer_thread:
                raise PersistenceError("cannot submit a write from within the persistence writer thread itself — this would deadlock")

            # A closed writer cannot service queued futures.
            if not self._writer_thread.is_alive():
                raise PersistenceError("cannot submit a write after this persistence connection has been closed")

            future: Future = Future()
            self._write_queue.put((write_operation, future))
            with telemetry_span("sqlite_write", operation="commit"):
                return future.result()

        def _wake_notification_waiters(self) -> None:
            """Wake threads blocked in wait_for_notifications_since."""

            with self._notification_condition:
                self._notification_generation += 1
                self._notification_condition.notify_all()

        def _wake_log_waiters(self) -> None:
            """Wake threads blocked in wait_for_log_entries_since."""

            with self._log_condition:
                self._log_generation += 1
                self._log_condition.notify_all()

        def _read_connection(self) -> _ReadConnectionLease:
            """This thread's read connection, reconnecting after close()."""

            connection = getattr(self._read_local, "connection", None)
            with self._read_connections_lock:
                current_generation = self._read_generation
            if connection is not None and getattr(self._read_local, "generation", None) != current_generation:
                connection = None  # closed by close() on another (or this) thread; reconnect below
            if connection is None:
                connection = sqlite3.connect(self.db_path, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                self._read_local.connection = connection
                self._read_local.generation = current_generation
                with self._read_connections_lock:
                    self._read_connections.append(connection)
            return _ReadConnectionLease(connection)

