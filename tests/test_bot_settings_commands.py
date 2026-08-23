"""bot/settings_commands.py (work_plan.md §8.8)."""

import asyncio

import pytest

from auth.permissions import PermissionLevel

from bot.api_client import SettingsView, SettingsWriteResult
from bot.deps import BotDeps
from bot.settings_commands import change_setting, view_settings
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
    text = _run(view_settings(_deps(api)))
    assert "3" in text and "0.6" in text and "30" in text


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
    api = FakeBotApiClient(settings_write_result=SettingsWriteResult(accepted=True, message="retry_count set to 5"))
    text = _run(change_setting(_deps(api), COMMANDER, "retry_count", "5"))
    assert "immediately" in text.lower()
    assert api.calls[-1] == ("write_setting", "retry_count", 5)


def test_valid_risk_threshold_is_parsed_as_float():
    api = FakeBotApiClient(settings_write_result=SettingsWriteResult(accepted=True, message="ok"))
    _run(change_setting(_deps(api), COMMANDER, "risk_threshold", "0.75"))
    assert api.calls[-1] == ("write_setting", "risk_threshold", 0.75)


def test_rejected_write_is_reported():
    api = FakeBotApiClient(settings_write_result=SettingsWriteResult(accepted=False, message="store unavailable"))
    text = _run(change_setting(_deps(api), COMMANDER, "retry_count", "2"))
    assert "Rejected" in text
    assert "store unavailable" in text
