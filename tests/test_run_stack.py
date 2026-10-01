import sys
from types import ModuleType

import pytest

import run_stack
from run_stack import reset_profile_databases


def test_main_starts_the_last_selected_profile(monkeypatch):
    started = []

    class FakeSupervisor:
        def __init__(self, profile_module):
            self.profile_module = profile_module

        def run(self):
            started.append(self.profile_module)

    monkeypatch.setattr(run_stack, "load_dotenv", lambda path: True)
    monkeypatch.setattr(run_stack, "load_selected_profile", lambda: "profiles.firefighting")
    monkeypatch.setattr(run_stack, "StackSupervisor", FakeSupervisor)

    run_stack.main()

    assert started == ["profiles.firefighting"]


def test_reset_removes_only_declared_databases_and_known_sidecars(tmp_path):
    database = tmp_path / "main.db"
    settings = tmp_path / "main.db.settings.json"
    unrelated = tmp_path / "keep.txt"
    for path in (database, settings, unrelated):
        path.write_text("x", encoding="utf-8")
    module = ModuleType("profiles.test_reset_profile")
    module.DB_PATH = str(database)
    module.RESETTABLE_DATABASES = (str(database),)
    sys.modules[module.__name__] = module
    try:
        removed = reset_profile_databases(module.__name__)
    finally:
        sys.modules.pop(module.__name__, None)
    assert set(removed) == {database.resolve(), settings.resolve()}
    assert unrelated.exists()


def test_run_stops_children_when_start_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(run_stack, "write_status", lambda **kwargs: None)

    class Boom(run_stack.StackSupervisor):
        def start(self):
            calls.append("start")
            raise RuntimeError("simulation-mode bot exited during startup")

        def stop(self):
            calls.append("stop")

        def _status(self, state):
            pass

    supervisor = Boom("profiles.response_team")
    with pytest.raises(RuntimeError, match="simulation-mode bot"):
        supervisor.run()

    assert calls == ["start", "stop"]


def test_spawn_opens_child_logs_under_the_profile_log_dir(tmp_path, monkeypatch):
    from types import SimpleNamespace

    opened = []
    monkeypatch.setenv("AGENTSHUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(run_stack, "available_profile", lambda module: SimpleNamespace(
        profile_name="Demo", api_port=18907, simulator_port=None,
    ))
    monkeypatch.setattr(run_stack, "_port_is_open", lambda host, port: False)
    monkeypatch.setattr(run_stack, "_wait_until_port_open", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_stack, "_http_responds", lambda url: True)
    monkeypatch.setattr(run_stack, "_announce", lambda message: None)
    monkeypatch.setattr(run_stack, "write_status", lambda **kwargs: None)

    supervisor = run_stack.StackSupervisor("profiles.response_team")

    def _open_log(self, path):
        opened.append(path)
        handle = SimpleNamespace(close=lambda: None)
        self._logs.append(handle)
        return handle

    monkeypatch.setattr(run_stack.StackSupervisor, "_open_log", _open_log)
    monkeypatch.setattr(
        run_stack.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=SimpleNamespace(),
            stderr=SimpleNamespace(),
            pid=1,
            poll=lambda: None,
        ),
    )
    monkeypatch.setattr(run_stack.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))

    supervisor.start()
    try:
        assert any(path.endswith("api.stdout.log") for path in opened)
        assert all("logs" in path.replace("\\", "/") for path in opened)
        assert (tmp_path / "logs" / "stack.stderr.log").is_file()
    finally:
        supervisor._detach_supervisor_log()


def test_start_refuses_an_already_occupied_api_port(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        run_stack,
        "available_profile",
        lambda module: SimpleNamespace(profile_name="Demo", api_port=8907, simulator_port=None),
    )
    monkeypatch.setattr(run_stack, "_port_is_open", lambda host, port: True)

    supervisor = run_stack.StackSupervisor("profiles.response_team")
    with pytest.raises(RuntimeError, match="already in use"):
        supervisor.start()


def test_unexpected_child_exit_names_the_process_and_code():
    from types import SimpleNamespace

    supervisor = run_stack.StackSupervisor("profiles.response_team")
    supervisor.bot_proc = SimpleNamespace(poll=lambda: 1, returncode=1, pid=99)

    assert supervisor._unexpected_child_exit() == "bot exited unexpectedly with code 1 (pid 99)"


def test_reset_refuses_a_declared_non_database_path(tmp_path):
    module = ModuleType("profiles.test_unsafe_reset_profile")
    module.DB_PATH = str(tmp_path)
    module.RESETTABLE_DATABASES = (str(tmp_path),)
    sys.modules[module.__name__] = module
    try:
        with pytest.raises(ValueError, match="non-database"):
            reset_profile_databases(module.__name__)
    finally:
        sys.modules.pop(module.__name__, None)
