"""bot/commands.py (work_plan.md §8.8)."""

import asyncio

import pytest

from auth.permissions import PermissionLevel

from bot.api_client import SettingsView, WriteResult
from bot.deps import BotDeps
from bot.commands import change_setting, view_settings
from bot.users import CallerContext
from tests.bot_fakes import FakeBotApiClient


def _run(coro):
    return asyncio.run(coro)


VIEWER = CallerContext(telegram_identity="v1", level=PermissionLevel.VIEWER)
COMMANDER = CallerContext(telegram_identity="c1", level=PermissionLevel.COMMANDER)


def _deps(api):
    return BotDeps(loaded_profile=None, telegram_client=None, api_client=api)


def test_view_shows_all_three_current_values():
    api = FakeBotApiClient(settings_view=SettingsView(retry_count=3, risk_threshold=0.6, lookback_window_days=30))
    text = _run(view_settings(_deps(api), "v1"))
    assert "3" in text and "0.6" in text and "30" in text


def test_view_forwards_the_real_callers_identity_to_the_api_client():
    api = FakeBotApiClient(settings_view=SettingsView(retry_count=3, risk_threshold=0.6, lookback_window_days=30))

    _run(view_settings(_deps(api), "v1"))

    assert api.calls == [("get_settings_view", "v1")]


def test_viewer_cannot_change_a_setting():
    api = FakeBotApiClient()
    text = _run(change_setting(_deps(api), VIEWER, "retry_count", "5"))
    assert "change_settings" in text
    assert not api.calls


@pytest.mark.parametrize(
    "field,raw_value,expected_fragment",
    [
        ("retry_count", "-1", "cannot be negative"),
        ("retry_count", "abc", "whole number"),
        ("risk_threshold", "1.5", "between 0.0 and 1.0"),
        ("risk_threshold", "not-a-number", "must be a number"),
        ("lookback_window_days", "0", "at least 1"),
        ("lookback_window_days", "-3", "at least 1"),
        ("bogus_field", "1", "unknown setting"),
    ],
)
def test_invalid_values_are_refused_before_reaching_the_api(field, raw_value, expected_fragment):
    api = FakeBotApiClient()
    text = _run(change_setting(_deps(api), COMMANDER, field, raw_value))
    assert expected_fragment in text
    assert not api.calls


def test_valid_change_confirms_immediate_effect_not_next_start():
    api = FakeBotApiClient(settings_write_result=WriteResult(accepted=True, message="retry_count set to 5"))
    text = _run(change_setting(_deps(api), COMMANDER, "retry_count", "5"))
    assert "immediately" in text.lower()
    assert api.calls[-1] == ("write_setting", "retry_count", 5, "c1")


def test_valid_risk_threshold_is_parsed_as_float():
    api = FakeBotApiClient(settings_write_result=WriteResult(accepted=True, message="ok"))
    _run(change_setting(_deps(api), COMMANDER, "risk_threshold", "0.75"))
    assert api.calls[-1] == ("write_setting", "risk_threshold", 0.75, "c1")


def test_rejected_write_is_reported():
    api = FakeBotApiClient(settings_write_result=WriteResult(accepted=False, message="store unavailable"))
    text = _run(change_setting(_deps(api), COMMANDER, "retry_count", "2"))
    assert "Rejected" in text
    assert "store unavailable" in text

"""bot/startup.py (work_plan.md §8.1's "run one bot per deployment")."""

import pytest

from bot.startup import AlreadyRunningError, SingleInstanceLock


def test_acquire_creates_the_lock_file_with_the_pid(tmp_path):
    import os

    lock_path = tmp_path / "deployment.db.bot.lock"
    lock = SingleInstanceLock(lock_path)

    lock.acquire()

    assert lock_path.exists()
    assert lock_path.read_text() == str(os.getpid())
    lock.release()


def test_a_second_lock_on_the_same_path_is_refused(tmp_path):
    lock_path = tmp_path / "deployment.db.bot.lock"
    first = SingleInstanceLock(lock_path)
    second = SingleInstanceLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_release_removes_the_lock_file_so_a_new_process_can_start(tmp_path):
    lock_path = tmp_path / "deployment.db.bot.lock"
    first = SingleInstanceLock(lock_path)
    first.acquire()
    first.release()

    assert not lock_path.exists()
    second = SingleInstanceLock(lock_path)
    second.acquire()
    second.release()


def test_release_without_acquire_does_not_raise(tmp_path):
    lock = SingleInstanceLock(tmp_path / "never_acquired.lock")
    lock.release()  # must not raise


def test_context_manager_releases_on_exit(tmp_path):
    lock_path = tmp_path / "deployment.db.bot.lock"

    with SingleInstanceLock(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()
