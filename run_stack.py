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
    control_dir,
    discover_profiles,
    load_selected_profile,
    save_selected_profile,
    write_status,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("stack_runner")

_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_APP_SIDECARS = (".settings.json", ".settings.json.tmp", ".notification_cursor", ".bot.lock", ".bot-simulator.lock")


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
        self.api_procs: dict[str, subprocess.Popen] = {}
        self.bot_proc: subprocess.Popen | None = None
        self.bot_sim_proc: subprocess.Popen | None = None
        self._logs: list[object] = []
        self.last_error = ""

    def _open_logs(self, component: str, slug: str) -> tuple[object, object]:
        handles = (
            open(f"{component}-{slug}.stdout.log", "a", encoding="utf-8"),
            open(f"{component}-{slug}.stderr.log", "a", encoding="utf-8"),
        )
        self._logs.extend(handles)
        return handles

    def _status(self, state: str) -> None:
        profiles = discover_profiles()
        info = next((profile for profile in profiles if profile.module_path == self.profile_module), None)
        write_status(
            supervisor_pid=os.getpid(),
            state=state,
            profile_module=self.profile_module,
            profile_name=info.profile_name if info else self.profile_module,
            api_port=info.api_port if info else None,
            api_pid=(self.api_procs.get(self.profile_module).pid
                     if self.api_procs.get(self.profile_module) and self.api_procs[self.profile_module].poll() is None
                     else None),
            api_pids={module: proc.pid for module, proc in self.api_procs.items() if proc.poll() is None},
            bot_pid=self.bot_proc.pid if self.bot_proc and self.bot_proc.poll() is None else None,
            bot_sim_pid=self.bot_sim_proc.pid if self.bot_sim_proc and self.bot_sim_proc.poll() is None else None,
            bot_sim_profile_module=self.profile_module if self.bot_sim_proc else None,
            last_error=self.last_error,
        )

    def _start_simulator(self) -> None:
        info = available_profile(self.profile_module)
        if info is None or not info.simulator_port:
            self.bot_sim_proc = None
            return
        stdout, stderr = self._open_logs("bot-sim", self.profile_module.rsplit(".", 1)[-1])
        self.bot_sim_proc = subprocess.Popen(
            [self.python_executable, "-m", "bot.simulator_app", self.profile_module],
            stdout=stdout, stderr=stderr, env={**os.environ, "AGENTSHUB_SUPERVISOR": "1"},
        )
        time.sleep(1)
        if self.bot_sim_proc.poll() is not None:
            raise RuntimeError(f"simulation-mode bot exited during startup with code {self.bot_sim_proc.returncode}")

    def _stop_simulator(self) -> None:
        process, module_path = self.bot_sim_proc, self.profile_module
        self.bot_sim_proc = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        module = __import__(module_path, fromlist=["DB_PATH"])
        lock_path = Path(f"{module.DB_PATH}.bot-simulator.lock")
        if process is not None and lock_path.is_file():
            lock_path.unlink()

    def start(self) -> None:
        profiles = discover_profiles()
        if not profiles or not any(profile.module_path == self.profile_module for profile in profiles):
            raise ValueError(f"unknown profile: {self.profile_module}")
        env = os.environ.copy()
        env["AGENTSHUB_SUPERVISOR"] = "1"
        self._status("starting")
        for profile in profiles:
            slug = profile.module_path.rsplit(".", 1)[-1]
            stdout, stderr = self._open_logs("api", slug)
            self.api_procs[profile.module_path] = subprocess.Popen(
                [self.python_executable, "-m", "api.app", profile.module_path],
                stdout=stdout, stderr=stderr, env=env,
            )
        time.sleep(3)
        for module_path, process in self.api_procs.items():
            if process.poll() is not None:
                raise RuntimeError(f"API for {module_path} exited during startup with code {process.returncode}")
        stdout, stderr = self._open_logs("bot", "shared")
        self.bot_proc = subprocess.Popen(
            [self.python_executable, "-m", "bot.app", "--shared"],
            stdout=stdout, stderr=stderr, env=env,
        )
        time.sleep(1)
        if self.bot_proc.poll() is not None:
            raise RuntimeError(f"bot exited during startup with code {self.bot_proc.returncode}")
        self._start_simulator()
        self.last_error = ""
        self._status("running")
        logger.info("Shared stack started for %d profiles (one Telegram bot, simulator: %s)",
                    len(self.api_procs), self.profile_module)

    def stop(self) -> None:
        self._status("stopping")
        self._stop_simulator()
        processes = [self.bot_proc, *self.api_procs.values()]
        for process in processes:
            if process is not None and process.poll() is None:
                process.terminate()
        for process in processes:
            if process is None:
                continue
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        shared_lock = control_dir() / "shared-bot.lock"
        if self.bot_proc is not None and shared_lock.is_file():
            shared_lock.unlink()
        self.bot_proc = None
        self.api_procs.clear()
        for handle in self._logs:
            handle.close()
        self._logs.clear()

    def switch(self, requested_profile: str) -> None:
        if available_profile(requested_profile) is None:
            self.last_error = f"Unknown profile requested: {requested_profile}"
            self._status("running")
            return
        previous = self.profile_module
        self._stop_simulator()
        self.profile_module = requested_profile
        try:
            self._start_simulator()
            save_selected_profile(requested_profile)
            self._status("running")
            logger.info("Simulator profile selected: %s; shared APIs and Telegram bot unchanged", requested_profile)
        except Exception as exc:
            logger.exception("Simulator %s failed; restoring %s", requested_profile, previous)
            self._stop_simulator()
            self.profile_module = previous
            self.last_error = f"Could not load {requested_profile}; restored {previous}: {exc}"
            self._start_simulator()
            self._status("running")

    def reset(self) -> None:
        active = self.profile_module
        self.stop()
        removed = reset_profile_databases(active)
        logger.warning("Reset %s; removed %d database artifacts", active, len(removed))
        self.start()

    def run(self) -> None:
        try:
            self.start()
        except Exception:
            self.stop()
            raise
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
                            if not self.api_procs or any(
                                process.poll() is not None for process in self.api_procs.values()
                            ):
                                self.stop()
                                self.start()
                            self._status("running")
                    elif command.get("action") == "switch_profile":
                        self.switch(str(command.get("profile_module", "")))
                elif self.bot_sim_proc and self.bot_sim_proc.poll() is not None:
                    self.last_error = "The selected simulator exited unexpectedly; it was restarted."
                    logger.error(self.last_error)
                    self._stop_simulator()
                    time.sleep(1)
                    self._start_simulator()
                elif (
                    any(process.poll() is not None for process in self.api_procs.values())
                    or (self.bot_proc and self.bot_proc.poll() is not None)
                ):
                    self.last_error = "A shared API or Telegram bot exited unexpectedly; the managed stack was restarted."
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
