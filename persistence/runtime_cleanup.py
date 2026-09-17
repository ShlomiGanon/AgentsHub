"""Scoped cleanup of runtime state for the unified demo profile.

The normal profile reset removes whole databases, which would also remove the
profile's seed identities, roster, and operational inventory.  This module is
the narrower maintenance seam used when a fresh conversation/history context
is needed while retaining those seed records.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from persistence import open_persistence, open_surveillance_persistence, open_team_status_persistence


_PROFILE_MODULE = "profiles.unified_test"
_DATABASE_NAMES = (
    "unified_history.db",
    "unified_surveillance.db",
    "unified_team_status.db",
)
_RUNTIME_TABLES = (
    "events",
    "event_steps",
    "held_events",
    "conversation_messages",
    "notification_log",
    "log_entries",
    "daily_summaries",
    "monthly_summaries",
    "yearly_summaries",
    "attendance_responses",
    "attendance_cycles",
    "drone_missions",
)
_PRESERVED_TABLES = (
    "users",
    "telegram_groups",
    "team_members",
    "roster_approval",
    "cameras",
    "drones",
)


@dataclass(frozen=True)
class CleanupReport:
    profile_module: str
    before: Mapping[str, int]
    removed: Mapping[str, int]
    after: Mapping[str, int]
    removed_artifacts: tuple[str, ...]
    preserved_paths: tuple[str, ...]


def _assert_profile_scope(profile_module: str, data_dir: Path) -> Path:
    if profile_module != _PROFILE_MODULE:
        raise ValueError(f"runtime cleanup is restricted to {_PROFILE_MODULE}")

    resolved = data_dir.resolve()
    if resolved.name != "unified_test" or resolved.parent.name != "data":
        raise ValueError(f"refusing cleanup outside the unified_test data directory: {resolved}")

    return resolved


def _table_counts(path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        available = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
            if table in available
        }


def _validate_no_active_lock(data_dir: Path) -> None:
    for lock_path in data_dir.glob("*.lock"):
        try:
            pid = int(lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            raise RuntimeError(f"cannot prove lock is stale: {lock_path}")

        if pid <= 0:
            raise RuntimeError(f"cannot prove lock is stale: {lock_path}")

        try:
            os.kill(pid, 0)
        except OSError:
            continue

        raise RuntimeError(f"active process {pid} still owns {lock_path}")


def clean_unified_test_runtime(
    data_dir: str | Path,
    *,
    profile_module: str = _PROFILE_MODULE,
    dry_run: bool = False,
) -> CleanupReport:
    """Clear runtime context in ``profiles.unified_test`` without deleting seed data.

    The operation is idempotent.  It requires all three profile databases to
    exist, refuses active process locks, and only deletes the explicitly
    allowlisted runtime tables/artifacts.
    """

    root = _assert_profile_scope(profile_module, Path(data_dir))
    _validate_no_active_lock(root)

    paths = {name: root / name for name in _DATABASE_NAMES}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"refusing cleanup with missing profile databases: {missing}")

    before = _table_counts(paths["unified_history.db"], _RUNTIME_TABLES + _PRESERVED_TABLES)
    before.update({f"surveillance.{key}": value for key, value in _table_counts(paths["unified_surveillance.db"], _RUNTIME_TABLES + _PRESERVED_TABLES).items()})
    before.update({f"team_status.{key}": value for key, value in _table_counts(paths["unified_team_status.db"], _RUNTIME_TABLES + _PRESERVED_TABLES).items()})

    run_json_paths = tuple(sorted((root / "simulation_runs").glob("*/run.json")))
    cursor_paths = tuple(sorted(root.glob("*.notification_cursor")))
    sidecar_paths = tuple(
        sorted(
            path
            for database in paths.values()
            for suffix in ("-wal", "-shm", "-journal")
            for path in (Path(f"{database}{suffix}"),)
            if path.exists()
        )
    )

    if dry_run:
        return CleanupReport(
            profile_module=profile_module,
            before=before,
            removed={},
            after=before,
            removed_artifacts=(),
            preserved_paths=tuple(str(path) for path in run_json_paths) + (str(root / "unified_history.db.settings.json"),),
        )

    history = open_persistence(str(paths["unified_history.db"]))
    try:
        removed = dict(history.clear_runtime_history())
    finally:
        history.close()

    surveillance = open_surveillance_persistence(str(paths["unified_surveillance.db"]))
    try:
        removed.update({f"surveillance.{key}": value for key, value in surveillance.clear_runtime_state().items()})
    finally:
        close = getattr(surveillance, "close", None)
        if close:
            close()

    team_status = open_team_status_persistence(str(paths["unified_team_status.db"]))
    try:
        removed.update({f"team_status.{key}": value for key, value in team_status.clear_runtime_state().items()})
    finally:
        close = getattr(team_status, "close", None)
        if close:
            close()

    removed_artifacts: list[str] = []
    for artifact in (*run_json_paths, *cursor_paths, *sidecar_paths):
        if artifact.exists():
            try:
                artifact.unlink()
            except PermissionError:
                # SQLite may retain a transient WAL/SHM handle on Windows even
                # after the owning store has closed.  Never force-delete a
                # live SQLite sidecar; it is safe to leave it for SQLite's
                # normal checkpoint/cleanup on the next open.
                continue
            removed_artifacts.append(str(artifact))

    after = _table_counts(paths["unified_history.db"], _RUNTIME_TABLES + _PRESERVED_TABLES)
    after.update({f"surveillance.{key}": value for key, value in _table_counts(paths["unified_surveillance.db"], _RUNTIME_TABLES + _PRESERVED_TABLES).items()})
    after.update({f"team_status.{key}": value for key, value in _table_counts(paths["unified_team_status.db"], _RUNTIME_TABLES + _PRESERVED_TABLES).items()})

    preserved_paths = tuple(str(path) for path in (root / "unified_history.db.settings.json",))
    preserved_paths += tuple(str(path) for path in sorted((root / "simulation_runs").glob("*/baseline.json")))
    preserved_paths += tuple(str(path) for path in sorted((root / "simulation_runs").glob("*/*.db")))

    return CleanupReport(
        profile_module=profile_module,
        before=before,
        removed=removed,
        after=after,
        removed_artifacts=tuple(removed_artifacts),
        preserved_paths=preserved_paths,
    )


__all__ = ["CleanupReport", "clean_unified_test_runtime"]
