"""Profiles/firefighting.py's FirefightingExternalForcesAgent -- a thin subclass of the shared, persisted
agents.neighboring_forces_agent.NeighboringForcesAgent, replacing the previous in-memory
dispatch_police/dispatch_ambulance/dispatch_water_tankers/dispatch_aircraft tools with the one
shared dispatch_neighboring_force(kind, target_area, unit_count, note) tool, backed by a real
dispatch-log table and the same busy-window remaining-capacity mechanism response_team.py has."""

import profiles.firefighting as ff


def _agent(tmp_path, monkeypatch):
    """Agent."""
    monkeypatch.setattr(ff.FirefightingExternalForcesAgent, "dispatch_db_path", str(tmp_path / "forces.db"))
    return ff.FirefightingExternalForcesAgent(model="test-model")


def test_constructed_with_a_model_like_any_other_agent(tmp_path, monkeypatch):
    """Constructed with a model like any other agent."""
    agent = _agent(tmp_path, monkeypatch)

    assert agent.model == "test-model"
    assert agent.name == "neighboring_forces_agent"


def test_exposes_exactly_the_one_shared_dispatch_tool(tmp_path, monkeypatch):
    """Exposes exactly the one shared dispatch tool."""
    agent = _agent(tmp_path, monkeypatch)
    tools = {t.name: t for t in agent.exposed_tools()}

    assert set(tools) == {"dispatch_neighboring_force", "list_neighboring_force_dispatches"}
    assert tools["dispatch_neighboring_force"].side_effecting is True
    assert tools["dispatch_neighboring_force"].idempotent is False


def test_dispatch_water_tankers_records_a_real_row_and_confirms(tmp_path, monkeypatch):
    """Dispatch water tankers records a real row and confirms."""
    agent = _agent(tmp_path, monkeypatch)

    # unit_count=2, not more: FORCE_POOL_SIZE is 2 for every kind (profiles/firefighting.py
    # section 3.1) -- requesting more than the pool holds is exactly what the resource-
    # unavailable test below already covers.
    result = agent.dispatch_neighboring_force(kind="water_tankers", target_area="chemical_plant", unit_count=2)

    assert "dispatch recorded, en route to chemical_plant" in result
    rows = agent.dispatch_store.list_dispatches()
    assert len(rows) == 1
    assert rows[0]["force_kind"] == "water_tankers"
    assert rows[0]["origin_area"] == "chemical_plant"  # profiles/firefighting.py's FORCE_BASES
    assert rows[0]["unit_count"] == 2


def test_dispatch_aircraft_records_a_real_row_with_its_own_home_area(tmp_path, monkeypatch):
    """Dispatch aircraft records a real row with its own home area."""
    agent = _agent(tmp_path, monkeypatch)

    result = agent.dispatch_neighboring_force(kind="aircraft", target_area="chemical_plant", unit_count=2)

    assert "dispatch recorded" in result
    rows = agent.dispatch_store.list_dispatches()
    assert rows[0]["origin_area"] == "pine_ridge"  # profiles/firefighting.py's FORCE_BASES


def test_dispatch_neighboring_force_signals_resource_unavailable_once_the_pool_is_exhausted(tmp_path, monkeypatch):
    """Dispatch neighboring force signals resource unavailable once the pool is exhausted."""
    agent = _agent(tmp_path, monkeypatch)
    agent.dispatch_neighboring_force(kind="police", target_area="ornim_street", unit_count=2)

    result = agent.dispatch_neighboring_force(kind="police", target_area="ornim_street", unit_count=1)

    assert "dispatch failed" in result
    assert agent.take_resource_unavailable_signal() is not None


def test_an_unknown_force_kind_asks_for_clarification_and_records_nothing(tmp_path, monkeypatch):
    """An unknown force kind asks for clarification and records nothing."""
    agent = _agent(tmp_path, monkeypatch)

    result = agent.dispatch_neighboring_force(kind="bulldozer", target_area="chemical_plant")

    assert result.ok is False
    assert "Clarification required" in result.text
    assert agent.dispatch_store.list_dispatches() == []


def test_calling_a_removed_tool_name_directly_no_longer_exists(tmp_path, monkeypatch):
    """Calling a removed tool name directly no longer exists."""
    agent = _agent(tmp_path, monkeypatch)

    assert not hasattr(agent, "dispatch_water_tankers")
    assert not hasattr(agent, "dispatch_police")
