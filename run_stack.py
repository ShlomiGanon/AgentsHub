"""Persistent API/bot process supervisor.

The admin panel can request a profile switch or a database reset through the small file-based
channel in :mod:`config.server_control`.  Running either child directly remains supported, but
destructive/restart controls are intentionally unavailable in that mode.
"""

from __future__ import annotations

import importlib
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

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
_APP_SIDECARS = (".settings.json", ".settings.json.tmp", ".notification_cursor", ".bot.lock", ".bot-simulator.lock")
_API_BIND_HOST = "127.0.0.1"
_API_READY_TIMEOUT_SECONDS = 45
_BOT_READY_GRACE_SECONDS = 2
_SIMULATOR_READY_TIMEOUT_SECONDS = 45


def _announce(message: str) -> None:
    """Operator-facing line: timestamped, flushed, and distinct from child logs."""

    logger.info(message)
    # logging may be configured to stderr; flush both so a Windows console shows it immediately.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except OSError:
            pass


def _relay_stream(stream, console, log_file, label: str) -> None:
    """Copy one child stream to the operator console and the component log file."""

    prefix = f"[{label}] "
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            tagged = prefix + line
            try:
                console.write(tagged)
                console.flush()
            except OSError:
                pass
            try:
                log_file.write(line)
                log_file.flush()
            except OSError:
                pass
    except OSError:
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _http_responds(url: str) -> bool:
    """True when something HTTP-speaks at url (including 4xx/5xx). Connection failure is False."""

    try:
        urlopen(url, timeout=2)
        return True
    except HTTPError:
        return True
    except (URLError, OSError, TimeoutError, ValueError):
        return False


def _wait_until_port_open(proc: subprocess.Popen, host: str, port: int, name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"{name} exited during startup with code {proc.returncode} before opening {host}:{port}"
            )
        if _port_is_open(host, port):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"{name} did not open {host}:{port} within {timeout:.0f}s "
        f"(process still running, pid {proc.pid})"
    )


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
        # None for a profile that hasn't declared SIMULATOR_PORT (docs/bot_simulation_mode_design.md) —
        # the third, simulation-mode bot subprocess is then never started at all.
        self.bot_sim_proc: subprocess.Popen | None = None
        self._logs: list[object] = []
        self._relays: list[threading.Thread] = []
        self.last_error = ""

    def _status(self, state: str) -> None:
        info = available_profile(self.profile_module)
        try:
            write_status(
                supervisor_pid=os.getpid(),
                state=state,
                profile_module=self.profile_module,
                profile_name=info.profile_name if info else self.profile_module,
                api_port=info.api_port if info else None,
                api_pid=self.api_proc.pid if self.api_proc and self.api_proc.poll() is None else None,
                bot_pid=self.bot_proc.pid if self.bot_proc and self.bot_proc.poll() is None else None,
                bot_sim_pid=self.bot_sim_proc.pid if self.bot_sim_proc and self.bot_sim_proc.poll() is None else None,
                last_error=self.last_error,
            )
        except OSError as exc:
            # A locked status.json must not take the supervisor down after the
            # children are already running (Windows replace/sharing errors).
            logger.warning("Could not write stack status (%s): %s", state, exc)

    def _open_log(self, path: str):
        handle = open(path, "a", encoding="utf-8")
        self._logs.append(handle)
        return handle

    def _spawn(self, label: str, args: list[str], env: dict[str, str], slug: str) -> subprocess.Popen:
        stdout_log = self._open_log(f"{label}-{slug}.stdout.log")
        stderr_log = self._open_log(f"{label}-{slug}.stderr.log")
        child_env = env.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None and process.stderr is not None
        for stream, console, log_file in (
            (process.stdout, sys.stdout, stdout_log),
            (process.stderr, sys.stderr, stderr_log),
        ):
            thread = threading.Thread(
                target=_relay_stream,
                args=(stream, console, log_file, label),
                name=f"relay-{label}-{'out' if stream is process.stdout else 'err'}",
                daemon=True,
            )
            thread.start()
            self._relays.append(thread)
        return process

    def start(self) -> None:
        info = available_profile(self.profile_module)
        if info is None:
            raise ValueError(f"unknown profile: {self.profile_module}")
        env = os.environ.copy()
        env["AGENTSHUB_SUPERVISOR"] = "1"
        slug = self.profile_module.rsplit(".", 1)[-1]
        api_url = f"http://{_API_BIND_HOST}:{info.api_port}"
        admin_url = f"{api_url}/admin/login"
        self._status("starting")

        _announce(
            f"Starting API for {info.profile_name} ({self.profile_module}); "
            f"waiting until {api_url} accepts connections"
        )
        self.api_proc = self._spawn(
            "api",
            [self.python_executable, "-m", "api.app", self.profile_module],
            env,
            slug,
        )
        _wait_until_port_open(
            self.api_proc, _API_BIND_HOST, info.api_port, "API", _API_READY_TIMEOUT_SECONDS
        )
        if not _http_responds(admin_url) and not _http_responds(api_url):
            raise RuntimeError(
                f"API bound {api_url} but HTTP requests to {admin_url} and {api_url} failed"
            )
        _announce(f"API is up at {api_url} (pid {self.api_proc.pid})")
        _announce(f"Admin panel is up at {admin_url}")

        _announce("Starting Telegram bot; waiting until the process stays alive")
        self.bot_proc = self._spawn(
            "bot",
            [self.python_executable, "-m", "bot.app", self.profile_module],
            env,
            slug,
        )
        bot_deadline = time.monotonic() + _BOT_READY_GRACE_SECONDS
        while time.monotonic() < bot_deadline:
            if self.bot_proc.poll() is not None:
                raise RuntimeError(
                    f"Telegram bot exited during startup with code {self.bot_proc.returncode} "
                    f"(pid {self.bot_proc.pid})"
                )
            time.sleep(0.2)
        _announce(
            f"Telegram bot is running (pid {self.bot_proc.pid}); "
            "it talks to Telegram, not HTTP"
        )

        if info.simulator_port:
            sim_url = f"http://{_API_BIND_HOST}:{info.simulator_port}"
            _announce(
                f"Starting simulation-mode bot; waiting until {sim_url} accepts connections"
            )
            self.bot_sim_proc = self._spawn(
                "bot-sim",
                [self.python_executable, "-m", "bot.simulator_app", self.profile_module],
                env,
                slug,
            )
            _wait_until_port_open(
                self.bot_sim_proc,
                _API_BIND_HOST,
                info.simulator_port,
                "simulation-mode bot",
                _SIMULATOR_READY_TIMEOUT_SECONDS,
            )
            _announce(
                f"Simulation-mode bot is up at {sim_url} (pid {self.bot_sim_proc.pid})"
            )

        self.last_error = ""
        self._status("running")
        sim_line = ""
        if self.bot_sim_proc and info.simulator_port:
            sim_line = (
                f"\n  Simulation bot: http://{_API_BIND_HOST}:{info.simulator_port} "
                f"(pid {self.bot_sim_proc.pid})"
            )
        _announce(
            "Stack is up — all required processes accepted connections:\n"
            f"  Profile:         {info.profile_name} ({self.profile_module})\n"
            f"  API:             {api_url} (pid {self.api_proc.pid})\n"
            f"  Admin:           {admin_url}\n"
            f"  Telegram bot:    pid {self.bot_proc.pid}{sim_line}"
        )

    def stop(self) -> None:
        self._status("stopping")
        for process in (self.bot_sim_proc, self.bot_proc, self.api_proc):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (self.bot_sim_proc, self.bot_proc, self.api_proc):
            if process is None:
                continue
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.api_proc = self.bot_proc = self.bot_sim_proc = None
        # On Windows ``terminate`` does not run the bot's Python ``finally`` block, so remove
        # only the now-stopped profile's declared lock files before the next start.
        for artifact in reset_artifacts(self.profile_module):
            if str(artifact).endswith((".bot.lock", ".bot-simulator.lock")) and artifact.is_file():
                artifact.unlink()
        for thread in self._relays:
            thread.join(timeout=2)
        self._relays.clear()
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
                elif (
                    (self.api_proc and self.api_proc.poll() is not None)
                    or (self.bot_proc and self.bot_proc.poll() is not None)
                    or (self.bot_sim_proc and self.bot_sim_proc.poll() is not None)
                ):
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
            try:
                write_status(
                    supervisor_pid=None,
                    state="stopped",
                    profile_module=self.profile_module,
                    last_error=self.last_error,
                )
            except OSError as exc:
                logger.warning("Could not write stopped stack status: %s", exc)


def main() -> None:
    load_dotenv(".env")
    selected = load_selected_profile()
    StackSupervisor(selected).run()


if __name__ == "__main__":
    main()
