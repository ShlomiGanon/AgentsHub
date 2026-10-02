"""/Groups CRUD, /TeamStatus/AttendanceCheck, and Telegram-group scoping of /Msg."""

import dataclasses

import pytest

from agents.reference import ReferenceAgent
from agents.runtime import build_agent_registry
from api.app import build_app, build_group_routing
from protocols.loader import ProtocolSet
from protocols.model import CriticalityLevel, Protocol
from tests.api_fakes import COMMANDER_IDENTITY, VIEWER_IDENTITY, auth_headers, build_context, happy_path_agent, protocols, teardown_ctx
from tests.crewai_fakes import install_crewai_stub

GROUP = "-100200300"


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch):
    install_crewai_stub(monkeypatch)


class _OtherAgent(ReferenceAgent):
    name = "other_agent"


class _FakeAttendanceAgent:
    """Duck-types TeamStatusAgent's `open_scheduled_cycle` for the endpoint test."""

    name = "team_status_agent"

    def __init__(self, opened):
        self.opened = opened
        self.calls = []

    def open_scheduled_cycle(self, now_iso=None, *, force=False, check_hour=None):
        self.calls.append((now_iso, force, check_hour))
        return self.opened


def _two_agent_ctx(tmp_path, main_agent, extra_agents=()):
    """build_context plus a second specialist and a protocol only it participates in."""

    ctx = build_context(tmp_path, main_agent=main_agent)
    history_agent = ctx.deps.registry.get("history_agent")
    reference_agent = ctx.deps.registry.get("reference_agent")
    registry = build_agent_registry({"history_agent": history_agent}, [reference_agent, _OtherAgent(model="m"), *extra_agents])
    other_protocol = Protocol(
        name="other_protocol",
        description="applies only to the other agent",
        participating_agents=("other_agent",),
        approved_tools=("check_status",),
        expected_success_output="other result",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )
    deps = dataclasses.replace(ctx.deps, registry=registry, protocol_set=ProtocolSet(protocols=(*protocols(), other_protocol)))
    return dataclasses.replace(ctx, deps=deps, group_routing=build_group_routing(ctx.deps.persistence, registry))


def _group_message(text, identity, chat_id=GROUP, chat_type="supergroup", **extra):
    return {
        "text": text,
        "sender_identity": identity,
        "conversation_id": f"telegram:{chat_id}:main",
        "telegram_chat_id": chat_id,
        "telegram_chat_type": chat_type,
        **extra,
    }


# -- /Groups -----------------------------------------------------------------


def test_group_listing_is_commander_only(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    assert client.get("/Groups", headers=auth_headers(VIEWER_IDENTITY)).status_code == 403
    resp = client.get("/Groups", headers=auth_headers(COMMANDER_IDENTITY))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["groups"] == []
    assert body["routable_agents"] == ["main_agent", "reference_agent"]


def test_group_bindings_are_created_listed_and_removed(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    headers = auth_headers(COMMANDER_IDENTITY)

    put = client.put(f"/Groups/{GROUP}", headers=headers, json={"agent_name": "reference_agent", "label": "ops room"})
    assert put.status_code == 200
    assert put.get_json() == {
        "chat_id": GROUP, "agent_name": "reference_agent", "label": "ops room", "auto_register": False,
        "attendance_check_enabled": True, "attendance_check_hour": 8,
    }

    listed = client.get("/Groups", headers=headers).get_json()["groups"]
    assert listed == [{
        "chat_id": GROUP, "agent_name": "reference_agent", "label": "ops room", "auto_register": False,
        "attendance_check_enabled": True, "attendance_check_hour": 8,
    }]
    # Write-through: the in-memory table and the DB agree without a reload.
    assert ctx.group_routing.get(GROUP).agent_name == "reference_agent"
    assert ctx.deps.persistence.read_group(GROUP)["agent_name"] == "reference_agent"

    assert client.put(f"/Groups/{GROUP}", headers=headers, json={"agent_name": "main_agent"}).status_code == 200
    assert ctx.group_routing.get(GROUP).agent_name == "main_agent"

    deleted = client.delete(f"/Groups/{GROUP}", headers=headers)
    assert deleted.status_code == 200
    assert ctx.group_routing.get(GROUP) is None
    assert client.delete(f"/Groups/{GROUP}", headers=headers).status_code == 404


def test_group_writes_reject_viewers_and_unroutable_agents(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    assert client.put(f"/Groups/{GROUP}", headers=auth_headers(VIEWER_IDENTITY), json={"agent_name": "reference_agent"}).status_code == 403
    assert client.delete(f"/Groups/{GROUP}", headers=auth_headers(VIEWER_IDENTITY)).status_code == 403

    missing = client.put(f"/Groups/{GROUP}", headers=auth_headers(COMMANDER_IDENTITY), json={})
    assert missing.status_code == 400

    for bad_agent in ("history_agent", "insights_agent", "no_such_agent"):
        resp = client.put(f"/Groups/{GROUP}", headers=auth_headers(COMMANDER_IDENTITY), json={"agent_name": bad_agent})
        assert resp.status_code == 400, bad_agent
        assert bad_agent in resp.get_json()["message"]
    assert ctx.group_routing.all() == ()


# -- /Msg scoping ------------------------------------------------------------


def test_private_chat_messages_are_unaffected_by_group_routing(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "hi"
    ctx = build_context(tmp_path, main_agent=agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(VIEWER_IDENTITY), json={
        "text": "hello", "sender_identity": VIEWER_IDENTITY,
        "telegram_chat_id": VIEWER_IDENTITY, "telegram_chat_type": "private",
    })

    assert resp.status_code == 200
    assert resp.get_json()["taken_as"] == "conversational"


def test_a_message_from_an_unregistered_group_is_refused(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="conversational")
    ctx = build_context(tmp_path, main_agent=agent)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(COMMANDER_IDENTITY), json=_group_message("hello", COMMANDER_IDENTITY))

    assert resp.status_code == 403
    assert GROUP in resp.get_json()["message"]
    assert agent.calls == []  # refused before any model call


def test_a_message_from_a_bound_group_still_sees_every_agent_and_protocol(tmp_path, teardown_ctx):
    """The group's bound agent is a context hint/priority for protocol selection only,
    never a hard filter (orchestrator/group_routing.py::scope_deps) -- every agent and
    protocol stays visible from every group, including this group's own."""

    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "scoped hi"
    ctx = _two_agent_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "reference_agent", "ops")
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(COMMANDER_IDENTITY), json=_group_message("hey", COMMANDER_IDENTITY))

    assert resp.status_code == 200
    assert resp.get_json()["answer"] == "scoped hi"
    prompt = next(call for call in agent.calls if "Reply naturally and directly" in call)
    assert '"name": "reference_agent"' in prompt
    assert '"name": "status_check"' in prompt
    assert '"name": "other_agent"' in prompt
    assert '"name": "other_protocol"' in prompt


def test_a_group_bound_to_main_agent_is_unscoped(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="conversational")
    agent._dispatch["Reply naturally and directly"] = "full hi"
    ctx = _two_agent_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "main_agent")
    client = build_app(ctx).test_client()

    resp = client.post("/Msg", headers=auth_headers(COMMANDER_IDENTITY), json=_group_message("hey", COMMANDER_IDENTITY))

    assert resp.status_code == 200
    prompt = next(call for call in agent.calls if "Reply naturally and directly" in call)
    assert '"name": "other_agent"' in prompt
    assert '"name": "other_protocol"' in prompt


def test_a_protocol_hint_outside_the_group_scope_is_accepted(tmp_path, teardown_ctx):
    """An explicit protocol_hint names a declared protocol regardless of which agent the
    group is bound to -- the bound agent is a selection-prompt hint only, never a
    restriction on an explicit, caller-asserted protocol name."""

    agent = happy_path_agent(intent="conversational")
    ctx = _two_agent_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "reference_agent")
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Msg", headers=auth_headers(COMMANDER_IDENTITY),
        json=_group_message("run it", COMMANDER_IDENTITY, protocol_hint="other_protocol"),
    )

    assert resp.status_code == 200
    assert resp.get_json()["protocol"] == "other_protocol"


def test_a_protocol_hint_inside_the_group_scope_takes_the_fast_path(tmp_path, teardown_ctx):
    agent = happy_path_agent(intent="conversational")
    ctx = _two_agent_ctx(tmp_path, agent)
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "reference_agent")
    client = build_app(ctx).test_client()

    resp = client.post(
        "/Msg", headers=auth_headers(COMMANDER_IDENTITY),
        json=_group_message("status?", COMMANDER_IDENTITY, protocol_hint="status_check"),
    )

    assert resp.status_code == 200
    assert resp.get_json()["protocol"] == "status_check"


# -- /TeamStatus/AttendanceCheck --------------------------------------------


def test_attendance_check_404s_when_no_attendance_specialist_is_registered(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    assert client.post("/TeamStatus/AttendanceCheck", headers=auth_headers(VIEWER_IDENTITY), json={}).status_code == 403
    assert client.post("/TeamStatus/AttendanceCheck", headers=auth_headers(COMMANDER_IDENTITY), json={}).status_code == 404


def test_attendance_check_opens_a_due_cycle_and_names_the_bound_groups(tmp_path, teardown_ctx):
    opened = {"cycle_key": "2026-09-10", "opened_at": "o", "deadline_at": "2026-09-10T06:00:00+00:00", "members_required": ["Alex Cohen"]}
    attendance_agent = _FakeAttendanceAgent(opened)
    ctx = _two_agent_ctx(tmp_path, happy_path_agent(), extra_agents=(attendance_agent,))
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "team_status_agent", "readiness")
    ctx.group_routing.upsert("-7", "reference_agent")
    client = build_app(ctx).test_client()

    resp = client.post("/TeamStatus/AttendanceCheck", headers=auth_headers(COMMANDER_IDENTITY), json={"now_iso": "2026-09-10T05:00:00+00:00"})

    assert resp.status_code == 200
    assert resp.get_json() == {
        "opened": True,
        "agent_name": "team_status_agent",
        "cycle_key": "2026-09-10",
        "deadline_at": "2026-09-10T06:00:00+00:00",
        "members_required": ["Alex Cohen"],
        "target_chat_ids": [GROUP],
    }
    assert attendance_agent.calls == [("2026-09-10T05:00:00+00:00", False, 8)]


def test_attendance_check_reports_not_opened_when_nothing_is_due(tmp_path, teardown_ctx):
    attendance_agent = _FakeAttendanceAgent(None)
    ctx = _two_agent_ctx(tmp_path, happy_path_agent(), extra_agents=(attendance_agent,))
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.post("/TeamStatus/AttendanceCheck", headers=auth_headers(COMMANDER_IDENTITY), json={"force": True})

    assert resp.status_code == 200
    assert resp.get_json() == {"opened": False, "agent_name": "team_status_agent", "target_chat_ids": []}
    assert attendance_agent.calls == [(None, True, None)]


def test_attendance_check_omits_disabled_groups_from_targets(tmp_path, teardown_ctx):
    opened = {"cycle_key": "2026-09-10", "opened_at": "o", "deadline_at": "d", "members_required": []}
    attendance_agent = _FakeAttendanceAgent(opened)
    ctx = _two_agent_ctx(tmp_path, happy_path_agent(), extra_agents=(attendance_agent,))
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "team_status_agent", "readiness", attendance_check_enabled=False)
    ctx.group_routing.upsert("-8", "team_status_agent", "active", attendance_check_enabled=True, attendance_check_hour=10)
    client = build_app(ctx).test_client()

    resp = client.post("/TeamStatus/AttendanceCheck", headers=auth_headers(COMMANDER_IDENTITY), json={"force": True})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["opened"] is True
    assert body["target_chat_ids"] == ["-8"]
    assert attendance_agent.calls == [(None, True, 10)]


def test_group_put_updates_attendance_settings_without_resetting_on_label_edit(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()
    headers = auth_headers(COMMANDER_IDENTITY)

    client.put(f"/Groups/{GROUP}", headers=headers, json={
        "agent_name": "reference_agent", "label": "ops",
        "attendance_check_enabled": False, "attendance_check_hour": 11,
    })
    again = client.put(f"/Groups/{GROUP}", headers=headers, json={"agent_name": "reference_agent", "label": "ops 2"})

    assert again.get_json()["attendance_check_enabled"] is False
    assert again.get_json()["attendance_check_hour"] == 11
    stored = ctx.deps.persistence.read_group(GROUP)
    assert stored["attendance_check_enabled"] is False
    assert stored["attendance_check_hour"] == 11


def test_real_attendance_agent_opens_claims_and_respects_group_hour(tmp_path, teardown_ctx):
    from datetime import datetime, timezone

    from agents.team_status_agent import TeamStatusAgent

    class _LiveAttendanceAgent(TeamStatusAgent):
        name = "team_status_agent"
        status_db_path = str(tmp_path / "crew-status.db")

    agent = _LiveAttendanceAgent(model="test-model")
    opened_at = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    agent.register_member("101", "Alex Cohen", opened_at.isoformat())
    agent.approve_roster("commander-1", opened_at.isoformat())
    ctx = _two_agent_ctx(tmp_path, happy_path_agent(), extra_agents=(agent,))
    teardown_ctx.append(ctx)
    ctx.group_routing.upsert(GROUP, "team_status_agent", "readiness", attendance_check_hour=8)
    client = build_app(ctx).test_client()
    headers = auth_headers(COMMANDER_IDENTITY)
    now = opened_at.isoformat()

    first = client.post("/TeamStatus/AttendanceCheck", headers=headers, json={"now_iso": now})
    assert first.get_json()["opened"] is True
    assert first.get_json()["target_chat_ids"] == [GROUP]

    again = client.post("/TeamStatus/AttendanceCheck", headers=headers, json={"now_iso": now})
    assert again.get_json()["opened"] is False

    forced = client.post("/TeamStatus/AttendanceCheck", headers=headers, json={"now_iso": now, "force": True})
    assert forced.get_json()["opened"] is True
    assert forced.get_json()["cycle_key"] == first.get_json()["cycle_key"]

    claimed = client.post("/TeamStatus/AttendanceCheck", headers=headers, json={"now_iso": now})
    assert claimed.get_json()["opened"] is True
    spent = client.post("/TeamStatus/AttendanceCheck", headers=headers, json={"now_iso": now})
    assert spent.get_json()["opened"] is False

    later_hour = client.put(
        f"/Groups/{GROUP}",
        headers=headers,
        json={"agent_name": "team_status_agent", "label": "readiness", "attendance_check_hour": 10},
    )
    assert later_hour.status_code == 200
    too_early = datetime(2026, 9, 4, 5, 0, tzinfo=timezone.utc).isoformat()  # 08:00 next day, hour is now 10
    not_due = client.post("/TeamStatus/AttendanceCheck", headers=headers, json={"now_iso": too_early})
    assert not_due.get_json()["opened"] is False


def test_group_put_rejects_an_invalid_attendance_hour(tmp_path, teardown_ctx):
    ctx = build_context(tmp_path)
    teardown_ctx.append(ctx)
    client = build_app(ctx).test_client()

    resp = client.put(
        f"/Groups/{GROUP}",
        headers=auth_headers(COMMANDER_IDENTITY),
        json={"agent_name": "reference_agent", "attendance_check_hour": 24},
    )

    assert resp.status_code == 400
    assert ctx.group_routing.get(GROUP) is None
