"""Covers FirefightingExternalForcesAgent's two new mutual-aid tools (Profile Split Plan
section 4.2) -- dispatch_water_tankers and dispatch_aircraft -- mirroring
tests/test_friendly_forces_agent.py's coverage style for the base class's own four tools."""

from agents import base
from profiles.firefighting import FirefightingExternalForcesAgent


def test_constructed_with_a_model_like_any_other_agent():
    agent = FirefightingExternalForcesAgent(model="some-model")

    assert agent.model == "some-model"
    assert agent.name == "friendly_forces_agent"  # inherited registry key, unchanged (decision 1)


def test_exposes_the_four_inherited_tools_plus_the_two_new_ones():
    agent = FirefightingExternalForcesAgent(model="m")
    tools = {t.name: t for t in agent.exposed_tools()}

    assert set(tools) == {
        "dispatch_ambulance", "dispatch_police", "dispatch_firefighters", "dispatch_military",
        "dispatch_water_tankers", "dispatch_aircraft",
    }
    for tool_info in tools.values():
        assert tool_info.side_effecting is True
        assert tool_info.idempotent is False


def test_dispatch_water_tankers_records_the_request_and_confirms():
    agent = FirefightingExternalForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_water_tankers"}))
    try:
        result = agent._wrapped_tools["dispatch_water_tankers"](
            location="chemical_plant", tanker_count=4, source_station="neighboring station", note="water curtain"
        )
    finally:
        base._current_allowed_tools.reset(token)

    assert "chemical_plant" in result
    assert len(agent.dispatches_recorded) == 1
    assert "tanker_count=4" in agent.dispatches_recorded[0]
    assert "source_station=neighboring station" in agent.dispatches_recorded[0]
    assert "note=water curtain" in agent.dispatches_recorded[0]


def test_dispatch_water_tankers_is_blocked_when_not_allowed():
    agent = FirefightingExternalForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_aircraft"}))  # dispatch_water_tankers not allowed
    try:
        result = agent._wrapped_tools["dispatch_water_tankers"](location="chemical_plant")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_water_tankers_genuinely_records_each_call_it_receives():
    agent = FirefightingExternalForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_water_tankers"}))
    try:
        agent._wrapped_tools["dispatch_water_tankers"](location="chemical_plant")
        agent._wrapped_tools["dispatch_water_tankers"](location="chemical_plant")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.dispatches_recorded) == 2


def test_dispatch_aircraft_records_the_request_and_confirms():
    agent = FirefightingExternalForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_aircraft"}))
    try:
        result = agent._wrapped_tools["dispatch_aircraft"](
            location="pine_ridge", aircraft_count=2, note="firebreak support"
        )
    finally:
        base._current_allowed_tools.reset(token)

    assert "pine_ridge" in result
    assert len(agent.dispatches_recorded) == 1
    assert "aircraft_count=2" in agent.dispatches_recorded[0]
    assert "aircraft_type=firefighting" in agent.dispatches_recorded[0]  # default value
    assert "note=firebreak support" in agent.dispatches_recorded[0]


def test_dispatch_aircraft_is_blocked_when_not_allowed():
    agent = FirefightingExternalForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_water_tankers"}))  # dispatch_aircraft not allowed
    try:
        result = agent._wrapped_tools["dispatch_aircraft"](location="pine_ridge")
    finally:
        base._current_allowed_tools.reset(token)

    assert "not permitted" in result
    assert agent.dispatches_recorded == []


def test_dispatch_aircraft_genuinely_records_each_call_it_receives():
    agent = FirefightingExternalForcesAgent(model="m")

    token = base._current_allowed_tools.set(frozenset({"dispatch_aircraft"}))
    try:
        agent._wrapped_tools["dispatch_aircraft"](location="pine_ridge")
        agent._wrapped_tools["dispatch_aircraft"](location="pine_ridge")
    finally:
        base._current_allowed_tools.reset(token)

    assert len(agent.dispatches_recorded) == 2
