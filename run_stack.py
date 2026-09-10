"""Persistent API/bot process supervisor.

The admin panel can request a profile switch or a database reset through the small file-based
channel in :mod:`config.server_control`.  Running either child directly remains supported, but
destructive/restart controls are intentionally unavailable in that mode.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from config.server_control import (
    available_profile,
    consume_command,
    load_selected_profile,
    save_selected_profile,
    write_status,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("stack_runner")

_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_APP_SIDECARS = (".settings.json", ".settings.json.tmp", ".notification_cursor", ".bot.lock")


def reset_artifacts(module_path: str) -> tuple[Path, ...]:
    """Return only explicitly declared DB files and their known, exact sidecars."""

    module = importlib.import_module(module_path)
    declared = getattr(module, "RESETTABLE_DATABASES", None)
    if not isinstance(declared, tuple) or not declared:
        raise ValueError(f"{module_path} does not declare RESETTABLE_DATABASES")
    main_db = getattr(module, "DB_PATH", None)
    if main_db not in declared:
        raise ValueError("RESETTABLE_DATABASES must include DB_PATH")
    paths: list[Path] = []
    for value in declared:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("RESETTABLE_DATABASES contains an invalid path")
        database = Path(value).resolve()
        if database.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError(f"refusing to reset non-database path: {database}")
        paths.append(database)
        paths.extend(Path(f"{database}{suffix}") for suffix in (*_SQLITE_SIDECARS, *_APP_SIDECARS))
    return tuple(dict.fromkeys(paths))


def reset_profile_databases(module_path: str) -> tuple[Path, ...]:
    removed: list[Path] = []
    for path in reset_artifacts(module_path):
        if path.exists():
            if not path.is_file():
                raise ValueError(f"refusing to remove non-file reset target: {path}")
            path.unlink()
            removed.append(path)
    return tuple(removed)


class StackSupervisor:
    def __init__(self, profile_module: str, *, python_executable: str | None = None):
        self.profile_module = profile_module
        self.python_executable = python_executable or sys.executable
        self.api_proc: subprocess.Popen | None = None
        self.bot_proc: subprocess.Popen | None = None
        self._logs: list[object] = []
        self.last_error = ""

    def _status(self, state: str) -> None:
        info = available_profile(self.profile_module)
        write_status(
            supervisor_pid=os.getpid(),
            state=state,
            profile_module=self.profile_module,
            profile_name=info.profile_name if info else self.profile_module,
            api_port=info.api_port if info else None,
            api_pid=self.api_proc.pid if self.api_proc and self.api_proc.poll() is None else None,
            bot_pid=self.bot_proc.pid if self.bot_proc and self.bot_proc.poll() is None else None,
            last_error=self.last_error,
        )

    def start(self) -> None:
        if available_profile(self.profile_module) is None:
            raise ValueError(f"unknown profile: {self.profile_module}")
        env = os.environ.copy()
        env["AGENTSHUB_SUPERVISOR"] = "1"
        slug = self.profile_module.rsplit(".", 1)[-1]
        for component in ("api", "bot"):
            self._logs.append(open(f"{component}-{slug}.stdout.log", "a", encoding="utf-8"))
            self._logs.append(open(f"{component}-{slug}.stderr.log", "a", encoding="utf-8"))
        self._status("starting")
        self.api_proc = subprocess.Popen(
            [self.python_executable, "-m", "api.app", self.profile_module],
            stdout=self._logs[0], stderr=self._logs[1], env=env,
        )
        time.sleep(3)
        if self.api_proc.poll() is not None:
            raise RuntimeError(f"API exited during startup with code {self.api_proc.returncode}")
        self.bot_proc = subprocess.Popen(
            [self.python_executable, "-m", "bot.app", self.profile_module],
            stdout=self._logs[2], stderr=self._logs[3], env=env,
        )
        time.sleep(1)
        if self.bot_proc.poll() is not None:
            raise RuntimeError(f"bot exited during startup with code {self.bot_proc.returncode}")
        self.last_error = ""
        self._status("running")
        logger.info("Stack started for %s (API %d, bot %d)", self.profile_module, self.api_proc.pid, self.bot_proc.pid)

    def stop(self) -> None:
        self._status("stopping")
        for process in (self.bot_proc, self.api_proc):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (self.bot_proc, self.api_proc):
            if process is None:
                continue
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.api_proc = self.bot_proc = None
        # On Windows ``terminate`` does not run the bot's Python ``finally`` block, so remove
        # only the now-stopped profile's declared lock files before the next start.
        for artifact in reset_artifacts(self.profile_module):
            if str(artifact).endswith(".bot.lock") and artifact.is_file():
                artifact.unlink()
        for handle in self._logs:
            handle.close()
        self._logs.clear()

    def switch(self, requested_profile: str) -> None:
        if available_profile(requested_profile) is None:
            self.last_error = f"Unknown profile requested: {requested_profile}"
            self._status("running")
            return
        previous = self.profile_module
        self.stop()
        self.profile_module = requested_profile
        try:
            self.start()
            save_selected_profile(requested_profile)
        except Exception as exc:
            logger.exception("Profile %s failed; rolling back to %s", requested_profile, previous)
            self.stop()
            self.profile_module = previous
            self.last_error = f"Could not load {requested_profile}; restored {previous}: {exc}"
            self.start()
            self.last_error = f"Could not load {requested_profile}; restored {previous}: {exc}"
            self._status("running")

    def reset(self) -> None:
        active = self.profile_module
        self.stop()
        removed = reset_profile_databases(active)
        logger.warning("Reset %s; removed %d database artifacts", active, len(removed))
        self.start()

    def run(self) -> None:
        self.start()
        try:
            while True:
                command = consume_command()
                if command:
                    if command.get("action") == "reset":
                        try:
                            self.reset()
                        except Exception as exc:
                            self.last_error = f"Database reset/restart failed: {exc}"
                            logger.exception("Database reset/restart failed")
                            if self.api_proc is None or self.api_proc.poll() is not None:
                                self.start()
                            self._status("running")
                    elif command.get("action") == "switch_profile":
                        self.switch(str(command.get("profile_module", "")))
                elif (self.api_proc and self.api_proc.poll() is not None) or (self.bot_proc and self.bot_proc.poll() is not None):
                    self.last_error = "A child process exited unexpectedly; the active profile was restarted."
                    logger.error(self.last_error)
                    self.stop()
                    time.sleep(1)
                    self.start()
                self._status("running")
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping stack")
        finally:
            self.stop()
            write_status(supervisor_pid=None, state="stopped", profile_module=self.profile_module, last_error=self.last_error)


def main() -> None:
    load_dotenv(".env")
    selected = load_selected_profile()
    StackSupervisor(selected).run()


if __name__ == "__main__":
    main()
