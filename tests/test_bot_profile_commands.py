"""bot/commands.py (work_plan.md §8.7)."""

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
