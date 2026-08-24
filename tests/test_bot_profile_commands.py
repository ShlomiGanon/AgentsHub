"""bot/profile_commands.py (work_plan.md §8.7)."""

import asyncio

from auth.permissions import PermissionLevel

from bot.api_client import ProfileDiffStatus, ProfileView, ProtocolView, ProtocolWriteResult
from bot.deps import BotDeps
from bot.profile_commands import NOTHING_CHANGED_NOTICE, profile_diff_status, view_profile, write_protocol
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

    text = _run(view_profile(_deps(api)))

    assert "reference_agent" in text
    assert "status_check" in text
    assert "LOW" in text
    assert "no approval required" in text
    assert "fire" in text and "medical" in text
    assert "north_sector" in text


def test_diff_status_reports_a_pending_restart():
    api = FakeBotApiClient(profile_diff_status=ProfileDiffStatus(differs_from_running=True))
    text = _run(profile_diff_status(_deps(api)))
    assert "pending" in text.lower()


def test_diff_status_reports_no_pending_restart():
    api = FakeBotApiClient(profile_diff_status=ProfileDiffStatus(differs_from_running=False))
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
    api = FakeBotApiClient(protocol_write_result=ProtocolWriteResult(accepted=True, message="removed"))
    text = _run(write_protocol(_deps(api), COMMANDER, "remove", {"name": "p"}))
    assert "removed" in text
    assert NOTHING_CHANGED_NOTICE in text


def test_successful_write_always_states_nothing_changed_until_restart():
    api = FakeBotApiClient(protocol_write_result=ProtocolWriteResult(accepted=True, message="added protocol 'p'"))
    text = _run(write_protocol(_deps(api), COMMANDER, "add", {"approval_flag": True}))
    assert NOTHING_CHANGED_NOTICE in text


def test_rejected_write_is_reported_as_rejected():
    api = FakeBotApiClient(protocol_write_result=ProtocolWriteResult(accepted=False, message="unknown agent 'x'"))
    text = _run(write_protocol(_deps(api), COMMANDER, "add", {"approval_flag": True}))
    assert "Rejected" in text
    assert "unknown agent" in text
