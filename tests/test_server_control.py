import json

import pytest

import run_stack
from config import server_control


def test_control_channel_accepts_only_discovered_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSHUB_CONTROL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTSHUB_SUPERVISOR", "1")
    server_control.write_status(supervisor_pid=123, state="running")

    command_id = server_control.submit_command("switch_profile", profile_module="profiles.demo")
    command = server_control.consume_command()
    assert command["id"] == command_id
    assert command["profile_module"] == "profiles.demo"
    with pytest.raises(ValueError):
        server_control.submit_command("switch_profile", profile_module="os")


def test_selected_profile_survives_outside_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSHUB_CONTROL_DIR", str(tmp_path))
    server_control.save_selected_profile("profiles.demo")
    assert server_control.load_selected_profile() == "profiles.demo"


def test_failed_profile_switch_rolls_back_and_keeps_error_for_admin(monkeypatch):
    supervisor = run_stack.StackSupervisor("profiles.demo", python_executable="python")
    starts = []
    monkeypatch.setattr(run_stack, "available_profile", lambda module: object())
    monkeypatch.setattr(supervisor, "stop", lambda: None)

    def fake_start():
        starts.append(supervisor.profile_module)
        if supervisor.profile_module == "profiles.friendly_forces":
            raise RuntimeError("startup failed")

    monkeypatch.setattr(supervisor, "start", fake_start)
    monkeypatch.setattr(supervisor, "_status", lambda state: None)
    monkeypatch.setattr(run_stack, "save_selected_profile", lambda module: pytest.fail("failed profile must not be persisted"))

    supervisor.switch("profiles.friendly_forces")

    assert starts == ["profiles.friendly_forces", "profiles.demo"]
    assert supervisor.profile_module == "profiles.demo"
    assert "restored profiles.demo" in supervisor.last_error

