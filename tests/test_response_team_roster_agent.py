"""profiles/response_team.py's ResponseTeamRosterAgent -- incident-scoped responder tracking
(memory/continuity audit gap #2), rebuilt to link a responder to the one specific real event
they are responding to, never to an area alone. Replaces an earlier, rejected area-co-location
design: a squad member merely stationed in an area (e.g. their normal post) must never be
reported as "with" someone actually responding to a real incident there (the east_orchards case
below), and "who else is with me" must never guess when more than one recent incident shares an
area."""

from datetime import datetime, timedelta, timezone

from agents.runtime import authenticated_request_identity
from persistence.sqlite_store import SQLitePersistence
from profiles.response_team import ResponseTeamRosterAgent


def _agent(tmp_path, monkeypatch):
    db_path = str(tmp_path / "roster.db")
    monkeypatch.setattr(ResponseTeamRosterAgent, "status_db_path", db_path)
    agent = ResponseTeamRosterAgent(model="test-model")
    for identity, name in (("gil", "Gil"), ("dan", "Dan"), ("yuval", "Yuval")):
        agent.status_store.register_member(identity, name)
    agent.status_store.approve_roster("commander-1")
    return agent


def _add_event(agent, *, area: str, minutes_ago: int = 5) -> str:
    persistence = SQLitePersistence(agent.status_db_path)
    occurred_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return persistence.append_event(
        {
            "classification": "security_incident",
            "area": area,
            "occurred_at": occurred_at,
            "received_at": occurred_at,
            "source": "test",
            "sender_identity": "test-sender",
            "raw_text": f"incident report for {area}",
        }
    )


def test_join_and_list_link_only_the_actual_responder_not_a_merely_stationed_member(tmp_path, monkeypatch):
    """The east_orchards case: yuval is merely stationed at east_orchards (his normal post,
    unrelated to any incident) at the same time gil is actually responding to a real event
    there. list_incident_responders must return only gil."""

    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="east_orchards")

    with authenticated_request_identity("yuval"):
        agent.report_team_movement(area="east_orchards")  # merely stationed, not joining

    with authenticated_request_identity("gil"):
        join_result = agent.join_incident_response(area="east_orchards")
        list_result = agent.list_incident_responders(area="east_orchards")

    assert "Linked to the incident" in join_result
    assert "Gil" in list_result
    assert "Yuval" not in list_result


def test_two_incidents_same_area_refuses_to_guess(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="east_orchards", minutes_ago=10)
    _add_event(agent, area="east_orchards", minutes_ago=5)

    with authenticated_request_identity("gil"):
        join_result = agent.join_incident_response(area="east_orchards")
        list_result = agent.list_incident_responders(area="east_orchards")

    assert "Not linked" in join_result
    assert "unclear" in join_result
    assert "unclear" in list_result
    assert agent.incident_store.find_open_link("gil") is None


def test_joining_a_new_incident_closes_the_previous_link_on_reassignment(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="east_orchards")
    _add_event(agent, area="west_gate")

    with authenticated_request_identity("gil"):
        agent.join_incident_response(area="east_orchards")
        agent.join_incident_response(area="west_gate")
        east_orchards_responders = agent.list_incident_responders(area="east_orchards")
        west_gate_responders = agent.list_incident_responders(area="west_gate")

    assert "No one is currently linked" in east_orchards_responders
    assert "Gil" in west_gate_responders


def test_leave_incident_response_closes_the_open_link(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="east_orchards")

    with authenticated_request_identity("gil"):
        agent.join_incident_response(area="east_orchards")
        leave_result = agent.leave_incident_response()
        list_result = agent.list_incident_responders(area="east_orchards")

    assert "closed" in leave_result
    assert "No one is currently linked" in list_result


def test_joining_an_area_with_no_recent_event_links_nothing(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    with authenticated_request_identity("gil"):
        join_result = agent.join_incident_response(area="west_gate")
        list_result = agent.list_incident_responders(area="west_gate")

    assert "Not linked" in join_result
    assert "No recent incident" in join_result
    assert "No recent incident" in list_result
