"""Task 60 — importing a profile is read-only; seeding happens at explicit bootstrap.

Importing a configuration module used to create its data directory, and loading
the profile for inspection reached the repository's real databases. Both made a
diagnostic able to change the baseline it was inspecting, and both made the
provenance of a database row impossible to reason about after the fact.
"""

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_MODULES = (
    "profiles.unified_test",
    "profiles.demo",
    "profiles.friendly_forces",
    "profiles.sub_agent_surveillance",
    "profiles.sub_agent_team_status",
)


def _import_in_subprocess(module_path: str, *, extra_body: str = "") -> subprocess.CompletedProcess:
    """Import `module_path` with every persistent write made fatal."""

    script = textwrap.dedent(
        f"""
        import json, pathlib, sqlite3, sys
        sys.path.insert(0, {str(REPO_ROOT)!r})

        writes = []
        real_connect = sqlite3.connect
        real_mkdir = pathlib.Path.mkdir

        def connect(database, *args, **kwargs):
            writes.append(["sqlite3.connect", str(database)])
            raise RuntimeError("persistent write during import")

        def mkdir(self, *args, **kwargs):
            writes.append(["Path.mkdir", str(self)])
            raise RuntimeError("directory creation during import")

        sqlite3.connect = connect
        pathlib.Path.mkdir = mkdir

        failure = None
        try:
            import {module_path} as profile
            {extra_body}
        except BaseException as exc:
            failure = f"{{type(exc).__name__}}: {{exc}}"

        sqlite3.connect = real_connect
        pathlib.Path.mkdir = real_mkdir
        print(json.dumps({{"writes": writes, "failure": failure}}))
        """
    )
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180
    )


def _import_report(module_path: str, *, extra_body: str = "") -> dict:
    import json

    result = _import_in_subprocess(module_path, extra_body=extra_body)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- 1. importing the profile performs no persistent writes -----------------


@pytest.mark.parametrize("module_path", PROFILE_MODULES)
def test_importing_a_profile_performs_no_persistent_writes(module_path):
    report = _import_report(module_path)

    assert report["writes"] == []
    assert report["failure"] is None


def test_reading_declared_paths_is_also_write_free():
    """Declaring a database path must not be the same as creating it."""

    report = _import_report(
        "profiles.unified_test",
        extra_body="assert profile.DB_PATH and profile.RESETTABLE_DATABASES",
    )

    assert report["writes"] == []
    assert report["failure"] is None


# --- 4. repeated imports are side-effect-free -------------------------------


def test_repeated_imports_stay_side_effect_free():
    report = _import_report(
        "profiles.unified_test",
        extra_body=(
            "import importlib\n            "
            "importlib.reload(profile)\n            "
            "importlib.reload(profile)"
        ),
    )

    assert report["writes"] == []
    assert report["failure"] is None


# --- 6. LIVE state is not altered merely by importing configuration ---------


def _fingerprints() -> dict:
    data_dir = REPO_ROOT / "data"
    if not data_dir.is_dir():
        return {}
    return {
        str(path.relative_to(REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(data_dir.rglob("*.db"))
    }


def test_importing_configuration_does_not_alter_the_real_databases():
    before = _fingerprints()

    for module_path in PROFILE_MODULES:
        report = _import_report(module_path)
        assert report["writes"] == []

    assert _fingerprints() == before


def test_importing_a_profile_creates_no_data_directory(tmp_path, monkeypatch):
    """A fresh checkout has no data/ directory; importing must not make one."""

    report = _import_report("profiles.unified_test")

    assert report["writes"] == []


# --- 2/3. explicit bootstrap still provisions, and is idempotent ------------


@pytest.fixture
def isolated_unified(monkeypatch, tmp_path):
    from profiles import unified_test

    history = str(tmp_path / "history.db")
    surveillance = str(tmp_path / "surveillance.db")
    team_status = str(tmp_path / "team-status.db")
    monkeypatch.setattr(unified_test, "DB_PATH", history)
    monkeypatch.setattr(unified_test, "UNIFIED_SURVEILLANCE_DB_PATH", surveillance)
    monkeypatch.setattr(unified_test, "UNIFIED_TEAM_STATUS_DB_PATH", team_status)
    monkeypatch.setattr(unified_test.UnifiedSurveillanceAgent, "surveillance_db_path", surveillance)
    monkeypatch.setattr(unified_test.UnifiedTeamStatusAgent, "status_db_path", team_status)
    monkeypatch.setattr(unified_test, "RESETTABLE_DATABASES", (history, surveillance, team_status))
    return unified_test, history, surveillance, team_status


def test_explicit_bootstrap_provisions_canonical_state(isolated_unified):
    from persistence import open_persistence, open_surveillance_persistence, open_team_status_persistence

    unified_test, history, surveillance, team_status = isolated_unified
    assert not Path(history).exists()

    unified_test.ensure_seed_data()

    store = open_persistence(history)
    try:
        assert store.read_user("bot-service") is not None
    finally:
        store.close()

    team = open_team_status_persistence(team_status)
    assert team.roster_is_approved() is True
    assert {member["telegram_identity"] for member in team.list_members()} >= {
        "commander_user", "viewer_user", "1001", "1002", "1003",
    }
    assert team.latest_cycle() is not None

    cameras = open_surveillance_persistence(surveillance).list_cameras()
    assert cameras, "canonical camera seed was not reconciled"


def test_explicit_bootstrap_is_idempotent(isolated_unified):
    from persistence import open_team_status_persistence

    unified_test, _history, _surveillance, team_status = isolated_unified

    unified_test.ensure_seed_data()
    team = open_team_status_persistence(team_status)
    first_members = [dict(member) for member in team.list_members(approved_only=False)]
    first_cycle = team.latest_cycle()

    unified_test.ensure_seed_data()
    unified_test.ensure_seed_data()

    team_again = open_team_status_persistence(team_status)
    assert [dict(member) for member in team_again.list_members(approved_only=False)] == first_members
    assert team_again.latest_cycle() == first_cycle


def test_the_stack_supervisor_bootstraps_the_profile_explicitly(monkeypatch):
    """The declared seed runs on a real start, and nowhere else."""

    import run_stack

    calls = []

    class _Module:
        @staticmethod
        def ensure_seed_data():
            calls.append("seeded")

    monkeypatch.setattr(run_stack.importlib, "import_module", lambda path: _Module)

    supervisor = run_stack.StackSupervisor("profiles.unified_test")

    assert supervisor.bootstrap() is True
    assert calls == ["seeded"]


def test_a_profile_without_a_seed_hook_is_skipped(monkeypatch):
    import run_stack

    class _Module:
        pass

    monkeypatch.setattr(run_stack.importlib, "import_module", lambda path: _Module)

    assert run_stack.StackSupervisor("profiles.demo").bootstrap() is False


# --- 5. simulation provisioning only when explicitly requested --------------


def test_simulation_provisioning_is_not_triggered_by_import():
    report = _import_report("profiles.unified_test")

    assert report["writes"] == []


def test_simulation_provisioning_requires_an_explicit_call(isolated_unified, monkeypatch):
    from persistence import open_persistence
    from profiles import ensure_simulation_entities
    from profiles.loader import load_profile
    from unittest.mock import MagicMock

    monkeypatch.setenv("BOT_TOKEN", "fake-token")
    unified_test, history, _surveillance, _team_status = isolated_unified

    store = open_persistence(history)
    try:
        before = {user["telegram_identity"] for user in store.list_users()}
        assert not any(identity.startswith("9000") for identity in before)

        loaded = load_profile(
            "profiles.unified_test", core_model=MagicMock(), sub_model=MagicMock()
        )
        after_load = {user["telegram_identity"] for user in store.list_users()}
        assert after_load == before, "loading a profile provisioned simulation identities"

        ensure_simulation_entities(store, loaded)
        after_call = {user["telegram_identity"] for user in store.list_users()}
        assert any(identity.startswith("9000") for identity in after_call)
    finally:
        store.close()
