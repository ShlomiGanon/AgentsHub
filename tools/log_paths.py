"""Profile-scoped file destinations for process capture and JSON server logs."""

from __future__ import annotations

import os
from pathlib import Path

_ENV_LOG_DIR = "AGENTSHUB_LOG_DIR"


def profile_slug(profile_name: str) -> str:
    """Profile slug."""

    return profile_name.rsplit(".", 1)[-1]


def log_dir_for(profile_name: str, *, mkdir: bool = True) -> Path:
    """Log dir for."""

    override = os.environ.get(_ENV_LOG_DIR)
    if override:
        path = Path(override)
    else:
        path = Path("data") / "logs" / profile_slug(profile_name)
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def child_log_paths(profile_name: str, label: str) -> tuple[Path, Path]:
    """Child log paths."""

    directory = log_dir_for(profile_name)
    return directory / f"{label}.stdout.log", directory / f"{label}.stderr.log"


def supervisor_log_path(profile_name: str) -> Path:
    """Supervisor log path."""

    return log_dir_for(profile_name) / "stack.stderr.log"


def server_jsonl_path(profile_name: str) -> Path:
    """Server jsonl path."""

    return log_dir_for(profile_name) / "server.jsonl"


def should_write_server_jsonl() -> bool:
    """Should write server jsonl."""

    if os.environ.get(_ENV_LOG_DIR):
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ
