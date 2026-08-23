import json

from config.settings_store import SettingsStore


def test_first_run_takes_starting_values_from_profile_and_writes_file(tmp_path):
    db_path = str(tmp_path / "deployment.db")

    store = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)

    assert store.get_retry_count() == 3
    assert store.get_risk_threshold() == 0.5
    assert store.get_lookback_window_days() == 30
    assert (tmp_path / "deployment.db.settings.json").exists()


def test_later_run_prefers_the_settings_file_over_profile_starting_values(tmp_path):
    db_path = str(tmp_path / "deployment.db")

    first = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)
    first.set_risk_threshold(0.9)

    second = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)

    assert second.get_risk_threshold() == 0.9


def test_change_is_written_before_it_is_considered_confirmed(tmp_path):
    db_path = str(tmp_path / "deployment.db")
    settings_path = tmp_path / "deployment.db.settings.json"

    store = SettingsStore(db_path, starting_retry_count=3, starting_risk_threshold=0.5, starting_lookback_window_days=30)
    store.set_retry_count(7)

    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk["retry_count"] == 7


def test_settings_file_lives_beside_the_database_not_the_profile(tmp_path):
    db_path = str(tmp_path / "sub" / "deployment.db")
    (tmp_path / "sub").mkdir()

    SettingsStore(db_path, starting_retry_count=1, starting_risk_threshold=0.1, starting_lookback_window_days=1)

    assert (tmp_path / "sub" / "deployment.db.settings.json").exists()
