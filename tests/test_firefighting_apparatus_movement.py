"""profiles/firefighting.py's FirefightingCrewStatusAgent -- incident-scoped responder tracking
(memory/continuity audit gap #2) for FIRE_002's own 'Ashed 3 dispatched to a specific fire'
narrative: apparatus dispatch must be recorded, and linking to an incident must come only from
an actual event on record for that area, never from area co-location alone."""

from datetime import datetime, timedelta, timezone

import profiles.firefighting as ff
from persistence.sqlite_store import SQLitePersistence
from protocols import CriticalityLevel


def _agent(tmp_path, monkeypatch):
    """Agent."""
    monkeypatch.setattr(ff.FirefightingCrewStatusAgent, "status_db_path", str(tmp_path / "crew.db"))
    monkeypatch.setattr(ff, "FIREFIGHTING_APPARATUS_DB_PATH", str(tmp_path / "apparatus.db"))
    monkeypatch.setattr(ff, "FIREFIGHTING_FIRES_DB_PATH", str(tmp_path / "fires.db"))
    monkeypatch.setattr(ff, "DB_PATH", str(tmp_path / "firefighting_history.db"))
    agent = ff.FirefightingCrewStatusAgent(model="test-model")
    agent.apparatus_store.ensure_apparatus(apparatus_id="APP-ASHED-3", callsign="Ashed 3", status="operational")
    agent.apparatus_store.ensure_apparatus(apparatus_id="APP-CARMEL-1", callsign="Carmel 1", status="operational")
    return agent


def _add_event(agent, *, area: str, minutes_ago: int = 5) -> str:
    """Add event."""
    persistence = SQLitePersistence(ff.DB_PATH)
    occurred_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return persistence.append_event(
        {
            "classification": "fire_incident",
            "area": area,
            "occurred_at": occurred_at,
            "received_at": occurred_at,
            "source": "test",
            "sender_identity": "test-sender",
            "raw_text": f"incident report for {area}",
        }
    )


def test_dispatched_apparatus_status_is_recorded(tmp_path, monkeypatch):
    """Dispatched apparatus status is recorded."""
    agent = _agent(tmp_path, monkeypatch)

    result = agent.update_apparatus_status("Ashed 3", "dispatched")

    assert "Ashed 3 status recorded: DISPATCHED" in result
    assert agent.apparatus_store.get_apparatus("Ashed 3")["status"] == "dispatched"


def test_dispatched_apparatus_with_no_area_given_names_no_one(tmp_path, monkeypatch):
    """Dispatched apparatus with no area given names no one."""
    agent = _agent(tmp_path, monkeypatch)

    result = agent.update_apparatus_status("Ashed 3", "dispatched")

    assert "currently at" not in result


def test_join_and_list_link_only_the_dispatched_apparatus_not_one_merely_relocated(tmp_path, monkeypatch):
    """The east_orchards-style case for apparatus: Carmel 1 is merely relocated to pine_ridge
    (no incident stated) while Ashed 3 is actually dispatched to a real fire there.
    list_incident_responders must return only Ashed 3."""

    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="pine_ridge")

    agent.update_apparatus_status("Carmel 1", "dispatched", current_area="pine_ridge")  # merely relocated

    join_result = agent.join_incident_response(identifier="Ashed 3", area="pine_ridge")
    list_result = agent.list_incident_responders(area="pine_ridge")

    assert "linked to the incident" in join_result
    assert "Ashed 3" in list_result
    assert "Carmel 1" not in list_result


def test_two_incidents_same_area_refuses_to_guess(tmp_path, monkeypatch):
    """Two incidents same area refuses to guess."""
    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="pine_ridge", minutes_ago=10)
    _add_event(agent, area="pine_ridge", minutes_ago=5)

    join_result = agent.join_incident_response(identifier="Ashed 3", area="pine_ridge")
    list_result = agent.list_incident_responders(area="pine_ridge")

    assert "Not linked" in join_result
    assert "unclear" in join_result
    assert "unclear" in list_result


def test_joining_a_new_incident_closes_the_previous_link_on_reassignment(tmp_path, monkeypatch):
    """Joining a new incident closes the previous link on reassignment."""
    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="pine_ridge")
    _add_event(agent, area="oak_valley")

    agent.join_incident_response(identifier="Ashed 3", area="pine_ridge")
    agent.join_incident_response(identifier="Ashed 3", area="oak_valley")

    assert "No one is currently linked" in agent.list_incident_responders(area="pine_ridge")
    assert "Ashed 3" in agent.list_incident_responders(area="oak_valley")


def test_leave_incident_response_closes_the_open_link(tmp_path, monkeypatch):
    """Leave incident response closes the open link."""
    agent = _agent(tmp_path, monkeypatch)
    _add_event(agent, area="pine_ridge")

    agent.join_incident_response(identifier="Ashed 3", area="pine_ridge")
    leave_result = agent.leave_incident_response(identifier="Ashed 3")

    assert "closed" in leave_result
    assert "No one is currently linked" in agent.list_incident_responders(area="pine_ridge")


def test_joining_an_area_with_no_recent_event_links_nothing(tmp_path, monkeypatch):
    """Joining an area with no recent event links nothing."""
    agent = _agent(tmp_path, monkeypatch)

    join_result = agent.join_incident_response(identifier="Ashed 3", area="pine_ridge")

    assert "Not linked" in join_result
    assert "No recent incident" in join_result


def test_report_apparatus_movement_protocol_is_declared_and_not_commander_only():
    """Report apparatus movement protocol is declared and not commander only."""
    protocol = next(p for p in ff.PROTOCOLS if p.name == "report_apparatus_movement")

    assert protocol.commander_only is False
    assert protocol.approved_tools == ("update_apparatus_status", "join_incident_response", "list_incident_responders")
    assert protocol.participating_agents == ("team_status_agent",)
    assert protocol.criticality == CriticalityLevel.LOW
