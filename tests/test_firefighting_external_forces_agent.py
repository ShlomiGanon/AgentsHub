"""Covers FirefightingExternalForcesAgent's two new mutual-aid tools (Profile Split Plan
section 4.2) -- dispatch_water_tankers and dispatch_aircraft -- mirroring
tests/test_friendly_forces_agent.py's coverage style for the base class's own four tools."""

import json

from agents import base
from agents import authenticated_request_identity
from profiles import firefighting as fire
from profiles.firefighting import FirefightingExternalForcesAgent
from profiles.simulation import simulation_user_telegram_id


def _agent(tmp_path, monkeypatch, model="m"):
    monkeypatch.setattr(fire, "FIREFIGHTING_OPERATIONS_DB_PATH", str(tmp_path / "operations.db"))
    return FirefightingExternalForcesAgent(model=model)


def _event(source):
    return {
        "source_message_id": source, "event_id": f"event-{source}",
        "occurred_at": "2026-09-09T14:30:00+03:00",
        "received_at": "2026-09-09T11:30:00+00:00",
    }


def test_constructed_with_a_model_like_any_other_agent(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, model="some-model")

    assert agent.model == "some-model"
    assert agent.name == "friendly_forces_agent"  # inherited registry key, unchanged (decision 1)


def test_exposes_the_inherited_dispatch_tools_and_fire_read_tool(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tools = {t.name: t for t in agent.exposed_tools()}

    assert set(tools) == {
        "dispatch_ambulance", "dispatch_police", "dispatch_firefighters", "dispatch_military",
        "dispatch_water_tankers", "dispatch_aircraft", "get_external_force_overview",
        "record_external_force_update",
    }
    assert tools["get_external_force_overview"].side_effecting is False
    assert tools["dispatch_water_tankers"].idempotent is True
    assert tools["dispatch_aircraft"].idempotent is True
    assert tools["dispatch_police"].idempotent is True


def test_dispatch_water_tankers_persists_a_simulated_request(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    token = base._current_allowed_tools.set(frozenset({"dispatch_water_tankers"}))
    try:
        with authenticated_request_identity("fire-commander"):
            args = _event("tanker-1")
            result = agent._wrapped_tools["dispatch_water_tankers"](
                location="chemical_plant", tanker_count=4, source_station="neighboring station",
                note="water curtain", **args,
            )
    finally:
        base._current_allowed_tools.reset(token)

    assert "chemical_plant" in result and "No real unit was dispatched" in result
    saved = agent.operations_store.list_updates()
    assert len(saved) == 1
    assert saved[0]["update_kind"] == "dispatch_request"
    assert '"count": 4' in saved[0]["facts_json"]
    assert '"source_station": "neighboring station"' in saved[0]["facts_json"]


def test_dispatch_water_tankers_is_blocked_when_not_allowed(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    token = base._current_allowed_tools.set(frozenset({"dispatch_aircraft"}))  # dispatch_water_tankers not allowed
    try:
        result = agent._wrapped_tools["dispatch_water_tankers"](location="chemical_plant")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_water_tankers_deduplicates_a_retried_event(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    token = base._current_allowed_tools.set(frozenset({"dispatch_water_tankers"}))
    try:
        with authenticated_request_identity("fire-commander"):
            args = _event("same-tanker-event")
            first = agent._wrapped_tools["dispatch_water_tankers"](location="chemical_plant", **args)
            second = agent._wrapped_tools["dispatch_water_tankers"](location="chemical_plant", **args)
    finally:
        base._current_allowed_tools.reset(token)

    assert "was recorded" in first and "already recorded" in second
    assert len(agent.operations_store.list_updates()) == 1


def test_dispatch_aircraft_persists_a_simulated_request(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    token = base._current_allowed_tools.set(frozenset({"dispatch_aircraft"}))
    try:
        with authenticated_request_identity("fire-commander"):
            result = agent._wrapped_tools["dispatch_aircraft"](
                location="pine_ridge", aircraft_count=2, note="firebreak support", **_event("aircraft-1")
            )
    finally:
        base._current_allowed_tools.reset(token)

    assert "pine_ridge" in result and "No real unit was dispatched" in result
    saved = agent.operations_store.list_updates()[0]
    assert saved["update_kind"] == "dispatch_request"
    assert '"count": 2' in saved["facts_json"]
    assert '"note": "firebreak support"' in saved["facts_json"]


def test_dispatch_aircraft_is_blocked_when_not_allowed(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)

    token = base._current_allowed_tools.set(frozenset({"dispatch_water_tankers"}))  # dispatch_aircraft not allowed
    try:
        result = agent._wrapped_tools["dispatch_aircraft"](location="pine_ridge")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_authenticated_police_persona_cannot_be_persisted_as_a_fire_unit(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    sender = simulation_user_telegram_id(4)
    context = {
        "event_id": "police-fire-report-1", "source_message_id": "police-source-1",
        "scenario_time": "2026-09-09T11:00:00+03:00",
        "received_at": "2026-09-27T11:00:00+00:00",
        "raw_text": "דיווח משטרה: ניידת במקום, עשן ליד כביש 444.",
        "validated_event_fields": {},
    }
    token = base._current_allowed_tools.set(frozenset({"record_external_force_update"}))
    try:
        with authenticated_request_identity(sender, event_context=context):
            result = agent._wrapped_tools["record_external_force_update"](
                force_id="firefighters-road-444", force_kind="firefighters", count=1,
                status="arrived", location="כביש 444", notes="invented summary",
                source_message_id="wrong-source", event_id="wrong-event",
                occurred_at="2030-01-01T00:00:00Z", received_at="2030-01-01T00:00:00Z",
                verification_status="verified", summary="wrong summary",
            )
    finally:
        base._current_allowed_tools.reset(token)

    assert result == "External-force status recorded."
    force = agent.operations_store.list_external_forces()[0]
    assert force["force_kind"] == "police"
    assert force["force_id"] == "police-report:police-fire-report-1"
    assert force["location"] == "כביש 444"
    update = agent.operations_store.list_updates()[0]
    assert update["event_id"] == "police-fire-report-1"
    assert update["source_message_id"] == "police-fire-report-1:police-source-1"
    assert update["verification_status"] == "unverified"
    assert "דיווח משטרה" in update["summary"]


def test_force_overview_reads_only_current_run_and_requested_time(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    store = agent.operations_store
    store.reset_current_state(
        now="2026-09-09T07:00:00+00:00", run_started_at="2026-09-27T09:00:00+00:00",
    )
    for source, event, received, occurred, force_id, location in (
        ("old", "old-run", "2026-09-27T08:59:00+00:00", "2026-09-09T12:00:00+03:00", "old-force", "old area"),
        ("current", "current-run", "2026-09-27T09:01:00+00:00", "2026-09-09T14:00:00+03:00", "current-force", "pine_ridge"),
        ("future", "current-run", "2026-09-27T09:02:00+00:00", "2026-09-09T15:00:00+03:00", "future-force", "future area"),
    ):
        store.record_incident_update(
            source_message_id=source, event_id=event, update_kind="external_force",
            summary="reported force", occurred_at=occurred, received_at=received,
            external_force={"force_id": force_id, "force_kind": "police", "count": 1,
                            "status": "reported", "location": location, "notes": "unverified"},
            facts={"external_force": {"force_id": force_id, "force_kind": "police", "count": 1,
                                      "status": "reported", "location": location, "notes": "unverified"}},
        )
    allowed = base._current_allowed_tools.set(frozenset({"dispatch_aircraft"}))
    try:
        with authenticated_request_identity("fire-commander"):
            request_result = agent._wrapped_tools["dispatch_aircraft"](
                location="quarry_junction", aircraft_count=1,
                **{**_event("dispatch-current-run"), "received_at": "2026-09-27T09:03:00+00:00"},
            )
    finally:
        base._current_allowed_tools.reset(allowed)

    view = json.loads(agent.get_external_force_overview("2026-09-09T14:30:00+03:00"))
    assert [row["force_id"] for row in view["external_forces"]] == ["current-force"]
    assert view["external_forces"][0]["location"] == "pine_ridge"
    assert "was recorded" in request_result
    assert len(view["dispatch_requests"]) == 1
    assert view["dispatch_requests"][0]["location"] == "quarry_junction"
