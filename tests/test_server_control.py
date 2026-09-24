import importlib
import json

import pytest

import run_stack
from config import TierModel
from config import server_control
from profiles.loader import load_profile


def test_only_the_two_operational_profiles_are_selectable(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "standby-squad-test-token")
    monkeypatch.setenv("FIREFIGHTING_BOT_TOKEN", "firefighting-test-token")

    profiles = server_control.discover_profiles()

    assert {profile.module_path for profile in profiles} == {
        "profiles.standby_squad",
        "profiles.firefighting",
    }


def test_each_selectable_profile_has_three_simulations(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "standby-squad-test-token")
    monkeypatch.setenv("FIREFIGHTING_BOT_TOKEN", "firefighting-test-token")

    for profile in server_control.discover_profiles():
        module = importlib.import_module(profile.module_path)
        assert len(module.SIMULATIONS) == 3, profile.module_path


def test_selectable_profiles_share_the_environment_runtime_ports(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "standby-squad-test-token")
    monkeypatch.setenv("FIREFIGHTING_BOT_TOKEN", "firefighting-test-token")
    monkeypatch.setenv("API_PORT", "7777")
    monkeypatch.setenv("SIMULATOR_PORT", "7778")
    core_model = TierModel(model="openai/test-core", api_key="test-key")
    sub_model = TierModel(model="openai/test-sub", api_key="test-key")

    discovered = server_control.discover_profiles()
    assert {profile.api_port for profile in discovered} == {7777}
    assert {profile.simulator_port for profile in discovered} == {7778}

    loaded = [
        load_profile(profile.module_path, core_model=core_model, sub_model=sub_model)
        for profile in discovered
    ]
    assert {profile.api_port for profile in loaded} == {7777}
    assert {profile.simulator_port for profile in loaded} == {7778}


def test_control_channel_accepts_only_discovered_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSHUB_CONTROL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTSHUB_SUPERVISOR", "1")
    server_control.write_status(supervisor_pid=123, state="running")

    command_id = server_control.submit_command("switch_profile", profile_module="profiles.standby_squad")
    command = server_control.consume_command()
    assert command["id"] == command_id
    assert command["profile_module"] == "profiles.standby_squad"
    for unavailable_profile in ("profiles.response_team", "profiles.fire_station", "os"):
        with pytest.raises(ValueError):
            server_control.submit_command("switch_profile", profile_module=unavailable_profile)


@pytest.mark.parametrize("module_path", ["profiles.standby_squad", "profiles.firefighting"])
def test_selected_profile_survives_outside_databases(tmp_path, monkeypatch, module_path):
    monkeypatch.setenv("AGENTSHUB_CONTROL_DIR", str(tmp_path))
    server_control.save_selected_profile(module_path)
    assert server_control.load_selected_profile() == module_path


def test_successful_profile_switch_persists_the_new_last_used_profile(monkeypatch):
    supervisor = run_stack.StackSupervisor("profiles.standby_squad", python_executable="python")
    starts = []
    saved = []
    monkeypatch.setattr(run_stack, "available_profile", lambda module: object())
    monkeypatch.setattr(supervisor, "stop", lambda: None)
    monkeypatch.setattr(supervisor, "start", lambda: starts.append(supervisor.profile_module))
    monkeypatch.setattr(run_stack, "save_selected_profile", saved.append)

    supervisor.switch("profiles.firefighting")

    assert supervisor.profile_module == "profiles.firefighting"
    assert starts == ["profiles.firefighting"]
    assert saved == ["profiles.firefighting"]


def test_failed_profile_switch_rolls_back_and_keeps_error_for_admin(monkeypatch):
    supervisor = run_stack.StackSupervisor("profiles.standby_squad", python_executable="python")
    starts = []
    monkeypatch.setattr(run_stack, "available_profile", lambda module: object())
    monkeypatch.setattr(supervisor, "stop", lambda: None)

    def fake_start():
        starts.append(supervisor.profile_module)
        if supervisor.profile_module == "profiles.firefighting":
            raise RuntimeError("startup failed")

    monkeypatch.setattr(supervisor, "start", fake_start)
    monkeypatch.setattr(supervisor, "_status", lambda state: None)
    monkeypatch.setattr(run_stack, "save_selected_profile", lambda module: pytest.fail("failed profile must not be persisted"))

    supervisor.switch("profiles.firefighting")

    assert starts == ["profiles.firefighting", "profiles.standby_squad"]
    assert supervisor.profile_module == "profiles.standby_squad"
    assert "restored profiles.standby_squad" in supervisor.last_error

