"""agents/neighboring_forces_agent.py's shared, parameterized NeighboringForcesAgent base class
(docs/Admin_Tables_Plan.md section 3) -- default dispatch/capacity/ETA behavior, independent of
either profile that subclasses it. profiles/response_team.py's own "squad" override and
profiles/firefighting.py's own force kinds are covered by their own test files, not duplicated
here."""

from agents import NeighboringForcesAgent
from agents import runtime as agent_runtime


class _TestNeighboringForcesAgent(NeighboringForcesAgent):
    dispatch_db_path = ""
    force_bases = {"police": "station_a", "ambulance": "station_b"}
    force_pool_size = 2
    force_busy_seconds = 3600


def _agent(tmp_path):
    _TestNeighboringForcesAgent.dispatch_db_path = str(tmp_path / "forces.db")
    return _TestNeighboringForcesAgent(model="test-model")


def _call_tool(agent, name, **kwargs):
    token = agent_runtime._current_allowed_tools.set(frozenset({name}))
    try:
        return agent._wrapped_tools[name](**kwargs)
    finally:
        agent_runtime._current_allowed_tools.reset(token)


def test_constructor_requires_a_dispatch_db_path():
    class _NoPathAgent(NeighboringForcesAgent):
        dispatch_db_path = ""

    try:
        _NoPathAgent(model="m")
        assert False, "expected TypeError"
    except TypeError:
        pass


def test_descriptor_and_tools_exposed(tmp_path):
    agent = _agent(tmp_path)

    assert agent.name == "neighboring_forces_agent"
    tool_names = set(agent._wrapped_tools.keys())
    assert tool_names == {"dispatch_neighboring_force", "list_neighboring_force_dispatches"}


def test_dispatch_succeeds_within_the_pool_and_records_a_real_row(tmp_path):
    agent = _agent(tmp_path)

    result = _call_tool(agent, "dispatch_neighboring_force", kind="police", target_area="downtown", unit_count=2)

    assert "dispatch recorded, en route to downtown" in result
    assert agent.take_resource_unavailable_signal() is None
    rows = agent.dispatch_store.list_dispatches()
    assert len(rows) == 1
    assert rows[0]["origin_area"] == "station_a"


def test_dispatch_signals_resource_unavailable_once_the_pool_is_exhausted(tmp_path):
    agent = _agent(tmp_path)
    _call_tool(agent, "dispatch_neighboring_force", kind="police", target_area="downtown", unit_count=2)

    result = _call_tool(agent, "dispatch_neighboring_force", kind="police", target_area="downtown", unit_count=1)

    assert "police dispatch failed" in result
    assert "only 0 of 2 police unit(s) currently available, 1 requested" in result
    assert agent.take_resource_unavailable_signal() is not None


def test_unknown_kind_asks_for_clarification_and_records_nothing(tmp_path):
    agent = _agent(tmp_path)

    result = _call_tool(agent, "dispatch_neighboring_force", kind="bulldozer", target_area="downtown")

    assert "Clarification required" in result
    assert "ambulance" in result and "police" in result
    assert agent.dispatch_store.list_dispatches() == []


def test_missing_target_area_asks_for_clarification(tmp_path):
    # Uses pytest's own tmp_path fixture, not a manually-managed tempfile.TemporaryDirectory --
    # the latter tries to rmtree() immediately on context-manager exit, before this function's
    # own `agent`/its held-open sqlite3 connection goes out of scope and releases its Windows
    # file lock, which raised a PermissionError on cleanup. tmp_path's own (later, pytest-owned)
    # cleanup doesn't race this way.
    class _A(NeighboringForcesAgent):
        dispatch_db_path = ""
        force_bases = {"police": "station_a"}

    _A.dispatch_db_path = str(tmp_path / "forces.db")
    agent = _A(model="m")
    result = _call_tool(agent, "dispatch_neighboring_force", kind="police", target_area="")
    assert "target_area is required" in result


def test_list_neighboring_force_dispatches_reports_every_recorded_row(tmp_path):
    agent = _agent(tmp_path)
    _call_tool(agent, "dispatch_neighboring_force", kind="ambulance", target_area="downtown", unit_count=1)

    result = _call_tool(agent, "list_neighboring_force_dispatches")

    assert "ambulance" in result
    assert "downtown" in result
    assert "EN_ROUTE" in result.upper()


def test_default_eta_is_45_for_same_area_and_180_for_different_areas_with_no_eta_fn(tmp_path):
    agent = _agent(tmp_path)

    same_area = _call_tool(agent, "dispatch_neighboring_force", kind="police", target_area="station_a", unit_count=1)
    assert "ETA=45s" in same_area


def test_custom_eta_fn_is_used_when_provided(tmp_path):
    class _A(NeighboringForcesAgent):
        dispatch_db_path = ""
        force_bases = {"police": "station_a"}
        eta_fn = staticmethod(lambda origin, target: 999)

    _A.dispatch_db_path = str(tmp_path / "forces.db")
    agent = _A(model="m")
    result = _call_tool(agent, "dispatch_neighboring_force", kind="police", target_area="downtown", unit_count=1)
    assert "ETA=999s" in result
