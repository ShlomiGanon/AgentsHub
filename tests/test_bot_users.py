"""bot/users.py (work_plan.md §8.2)."""

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
