"""Regression: every protocol (not only safety_critical ones) stays a selection
candidate, and every agent stays executable, from every declared simulation group in a
profile -- the group's bound agent is a context hint/priority for protocol_selection's
prompt only, never a hard filter (orchestrator/group_routing.py::scope_deps). One test
per group, per profile -- response_team and firefighting are the two profiles with
declared SIMULATION_GROUPS today.

This is the direct regression test for the diagnostic finding that response_team's own
security-incident reports (messages arriving in "response_team"/"external_forces"
groups, neither bound to surveillance_agent) were silently excluded from the
protocol_selection candidate list before this fix -- confirmed against real model_io
logs during this session's investigation. `safety_critical` is retained on Protocol as
a declarative marker (protocols/contracts.py) but no longer gates inclusion here.
"""

import profiles.firefighting as firefighting
import profiles.response_team as response_team
from agents.runtime import AgentRegistry
from orchestrator.flows import FlowDeps
from orchestrator.group_routing import scope_deps
from orchestrator.main_agent import select_protocol
from protocols.repository import ProtocolSet


class _NamedAgent:
    def __init__(self, name):
        self.name = name
        self.descriptor = None


class _ScriptedMainAgent:
    def __init__(self, response_text, status="success"):
        self._response_text = response_text
        self._status = status
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


def _deps_for(protocols):
    agent_names = {"main_agent", "insights_agent", "history_agent"}
    for protocol in protocols:
        agent_names.update(protocol.participating_agents)
    registry = AgentRegistry({name: _NamedAgent(name) for name in agent_names})
    return FlowDeps(
        persistence=None, settings_store=None, registry=registry,
        protocol_set=ProtocolSet(protocols=tuple(protocols)),
        event_type_registry=None, area_registry=None, history_query_service=None,
    )


def _assert_every_protocol_selectable_from_every_group(profile_module):
    protocols = profile_module.PROTOCOLS
    all_names = {p.name for p in protocols}
    deps = _deps_for(protocols)

    for group in profile_module.SIMULATION_GROUPS:
        scoped = scope_deps(deps, group.agent_name)
        scoped_names = {p.name for p in scoped.protocol_set.all()}
        assert scoped_names == all_names, (
            f"group {group.key!r} (bound to {group.agent_name!r}) does not expose the full "
            f"protocol list -- missing {all_names - scoped_names}"
        )
        assert scoped.preferred_agent_hint == group.agent_name
        registered = {a.name for a in scoped.registry.all()}
        needed = {name for protocol in protocols for name in protocol.participating_agents}
        assert needed <= registered, (
            f"group {group.key!r}'s registry is missing agent(s) {needed - registered}"
        )


def test_response_team_full_protocol_list_selectable_from_every_group():
    _assert_every_protocol_selectable_from_every_group(response_team)


def test_firefighting_full_protocol_list_selectable_from_every_group():
    _assert_every_protocol_selectable_from_every_group(firefighting)


# -- a camera-status report sent in the roster group reaches update_camera_status ----


def test_response_team_camera_status_report_in_roster_group_reaches_update_camera_status():
    """"כיתת כוננות" (the "response_team" simulation group) is bound to roster_agent, not
    surveillance_agent -- a camera-status report arriving there must still be able to
    reach update_camera_status. Uses a scripted Main Agent (no real model call) to prove
    the mechanism: the full protocol list (including update_camera_status) and the
    roster_agent preference hint both reach the same selection prompt, and a selection
    outside the hinted domain is still accepted, not rejected as an unknown/filtered
    candidate."""

    protocols = response_team.PROTOCOLS
    deps = scope_deps(_deps_for(protocols), "roster_agent")
    assert deps.preferred_agent_hint == "roster_agent"

    agent = _ScriptedMainAgent(
        "SELECTED: update_camera_status\n"
        "REASON: camera 3 near the east gate is stuck on a frozen frame, a plain camera "
        "equipment-status observation."
    )
    raw_text = "מצלמה 3 ליד השער המזרחי תקועה על תמונה קפואה, נדרש טכנאי."

    selection = select_protocol(
        agent, raw_text, classification="camera_status", area="east_gate", description=None,
        protocols=deps.protocol_set.all(), risk_level="low",
        preferred_agent_hint=deps.preferred_agent_hint,
    )

    assert selection.status == "selected"
    assert selection.protocol_name == "update_camera_status"
    prompt_sent = agent.calls[0][0]
    assert "update_camera_status" in prompt_sent
    assert "roster_agent" in prompt_sent
