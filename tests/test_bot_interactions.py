"""Bot profile, settings, and user interactions."""

import asyncio

from auth.permissions import PermissionLevel

from bot.api_client import ProfileView, ProtocolView, WriteResult
from bot.deps import BotDeps
from bot.commands import NOTHING_CHANGED_NOTICE, profile_diff_status, view_profile, write_protocol
from bot.users import CallerContext
from tests.bot_fakes import FakeBotApiClient


def _run(coro):
    return asyncio.run(coro)


VIEWER = CallerContext(telegram_identity="v1", level=PermissionLevel.VIEWER)
COMMANDER = CallerContext(telegram_identity="c1", level=PermissionLevel.COMMANDER)

VIEW = ProfileView(
    profile_name="demo",
    agent_names=("reference_agent",),
    protocols=(ProtocolView(name="status_check", description="checks status", criticality="LOW", approval_flag=False),),
    event_types=("fire", "medical"),
    areas=("north_sector",),
)


def _deps(api):
    return BotDeps(loaded_profile=None, telegram_client=None, api_client=api)


def test_view_shows_agents_protocols_with_flags_event_types_and_areas():
    api = FakeBotApiClient(profile_view=VIEW)

    text = _run(view_profile(_deps(api), "v1"))

    assert "reference_agent" in text
    assert "status_check" in text
    assert "LOW" in text
    assert "no approval required" in text
    assert "fire" in text and "medical" in text
    assert "north_sector" in text


def test_view_forwards_the_real_callers_identity_to_the_api_client():
    # Problem 1's fix: the API client, not just bot/*'s own client-side
    # check, needs to see who's really asking, so its own server-side
    # permission check has something real to enforce against.
    api = FakeBotApiClient(profile_view=VIEW)

    _run(view_profile(_deps(api), "v1"))

    assert api.calls == [("get_profile_view", "v1")]


def test_diff_status_reports_a_pending_restart():
    api = FakeBotApiClient(profile_diff_status=True)
    text = _run(profile_diff_status(_deps(api)))
    assert "pending" in text.lower()


def test_diff_status_reports_no_pending_restart():
    api = FakeBotApiClient(profile_diff_status=False)
    text = _run(profile_diff_status(_deps(api)))
    assert "no restart is pending" in text.lower()


def test_viewer_cannot_write_a_protocol():
    api = FakeBotApiClient()
    text = _run(write_protocol(_deps(api), VIEWER, "add", {"approval_flag": False}))
    assert "edit_profile" in text
    assert not api.calls


def test_add_without_explicit_approval_flag_is_refused():
    api = FakeBotApiClient()
    text = _run(write_protocol(_deps(api), COMMANDER, "add", {"name": "p"}))
    assert "approval_flag" in text
    assert "explicitly" in text
    assert not api.calls


def test_remove_needs_no_approval_flag():
    api = FakeBotApiClient(protocol_write_result=WriteResult(accepted=True, message="removed"))
    text = _run(write_protocol(_deps(api), COMMANDER, "remove", {"name": "p"}))
    assert "removed" in text
    assert NOTHING_CHANGED_NOTICE in text


def test_successful_write_always_states_nothing_changed_until_restart():
    api = FakeBotApiClient(protocol_write_result=WriteResult(accepted=True, message="added protocol 'p'"))
    text = _run(write_protocol(_deps(api), COMMANDER, "add", {"approval_flag": True}))
    assert NOTHING_CHANGED_NOTICE in text


def test_write_forwards_the_real_callers_identity_to_the_api_client():
    api = FakeBotApiClient(protocol_write_result=WriteResult(accepted=True, message="added"))

    _run(write_protocol(_deps(api), COMMANDER, "add", {"approval_flag": True}))

    assert api.calls == [("write_protocol", "add", {"approval_flag": True}, "c1")]


def test_rejected_write_is_reported_as_rejected():
    api = FakeBotApiClient(protocol_write_result=WriteResult(accepted=False, message="unknown agent 'x'"))
    text = _run(write_protocol(_deps(api), COMMANDER, "add", {"approval_flag": True}))
    assert "Rejected" in text
    assert "unknown agent" in text


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


def test_settings_view_forwards_the_real_callers_identity_to_the_api_client():
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


import asyncio

from auth.permissions import PermissionLevel

from bot import app, users
from bot.users import CallerContext, check_permission, resolve_caller
from tests.bot_fakes import FakeBotApiClient


def _run(coro):
    return asyncio.run(coro)


def test_unregistered_identity_is_refused_and_named_not_registered():
    api = FakeBotApiClient(users={})

    result = _run(resolve_caller(api, "stranger"))

    assert result.status == "unregistered"
    assert "not a registered user" in result.refusal_message
    assert "stranger" in result.refusal_message


def test_registered_viewer_resolves_with_viewer_level():
    api = FakeBotApiClient(users={"v1": "viewer"})

    result = _run(resolve_caller(api, "v1"))

    assert result.status == "ok"
    assert result.caller.level == PermissionLevel.VIEWER


def test_registered_commander_resolves_with_commander_level():
    api = FakeBotApiClient(users={"c1": "commander"})

    result = _run(resolve_caller(api, "c1"))

    assert result.status == "ok"
    assert result.caller.level == PermissionLevel.COMMANDER


def test_check_permission_allows_when_level_is_sufficient():
    caller = CallerContext(telegram_identity="c1", level=PermissionLevel.COMMANDER)
    assert check_permission(caller, "approve_run") is None


def test_check_permission_refuses_and_names_the_action():
    caller = CallerContext(telegram_identity="v1", level=PermissionLevel.VIEWER)

    refusal = check_permission(caller, "approve_run")

    assert refusal is not None
    assert "approve_run" in refusal
    assert "v1" in refusal
    assert "viewer" in refusal.lower()


def test_no_user_management_capability_exists_in_users_module():
    forbidden_names = {"add_user", "write_user", "remove_user", "delete_user", "change_user", "list_users"}
    assert forbidden_names.isdisjoint(dir(users))


def test_no_user_management_command_is_registered_by_the_bot():
    # §8.2: "Provide no command that adds, changes, or removes a user, and
    # no command that reports the user list." bot.app registers exactly
    # /profile and /settings — neither manages users.
    assert app.REGISTERED_COMMANDS == ("profile", "settings")
