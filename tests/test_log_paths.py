"""Profile log directory helper."""

from pathlib import Path

from tools.log_paths import child_log_paths, log_dir_for, profile_slug, server_jsonl_path


def test_profile_slug_uses_the_last_module_segment():
    """Profile slug uses the last module segment."""
    assert profile_slug("profiles.firefighting") == "firefighting"
    assert profile_slug("firefighting") == "firefighting"


def test_log_dir_uses_profile_slug(tmp_path, monkeypatch):
    """Log dir uses profile slug."""
    monkeypatch.delenv("AGENTSHUB_LOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    path = log_dir_for("profiles.firefighting")
    assert path.resolve() == (tmp_path / "data" / "logs" / "firefighting").resolve()
    assert path.is_dir()


def test_log_dir_override_skips_the_slug(tmp_path, monkeypatch):
    """Log dir override skips the slug."""
    monkeypatch.setenv("AGENTSHUB_LOG_DIR", str(tmp_path / "custom"))
    path = log_dir_for("profiles.response_team")
    assert path == tmp_path / "custom"
    assert path.is_dir()


def test_child_and_server_paths_live_under_the_profile_dir(tmp_path, monkeypatch):
    """Child and server paths live under the profile dir."""
    monkeypatch.setenv("AGENTSHUB_LOG_DIR", str(tmp_path / "logs"))
    stdout, stderr = child_log_paths("profiles.response_team", "api")
    assert stdout == tmp_path / "logs" / "api.stdout.log"
    assert stderr == tmp_path / "logs" / "api.stderr.log"
    assert server_jsonl_path("profiles.response_team") == tmp_path / "logs" / "server.jsonl"
    assert stdout.parent == Path(tmp_path / "logs")


def test_supervisor_log_lives_under_the_profile_dir(tmp_path, monkeypatch):
    """Supervisor log lives under the profile dir."""
    from tools.log_paths import supervisor_log_path

    monkeypatch.setenv("AGENTSHUB_LOG_DIR", str(tmp_path / "logs"))
    assert supervisor_log_path("profiles.response_team") == tmp_path / "logs" / "stack.stderr.log"
