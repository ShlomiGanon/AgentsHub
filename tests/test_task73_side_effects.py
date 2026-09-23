from agents import FriendlyForcesAgent, authenticated_request_identity, trusted_event_metadata
from persistence import (
    OperationalScope,
    open_operational_dispatch_store,
    open_persistence,
    operational_scope_context,
)


def _dispatch_context(scope):
    return (
        operational_scope_context(scope),
        trusted_event_metadata({"operational_scope": scope}),
        authenticated_request_identity("commander"),
    )


def test_friendly_dispatch_is_persisted_verified_and_survives_reopen(tmp_path):
    db_path = str(tmp_path / "dispatch.sqlite")
    persistence = open_persistence(db_path)
    persistence.close()
    dispatch_store = open_operational_dispatch_store(db_path)
    agent = FriendlyForcesAgent(model="m")
    agent.bind_dispatch_store(dispatch_store)
    scope = OperationalScope.live()

    with _dispatch_context(scope)[0], _dispatch_context(scope)[1], _dispatch_context(scope)[2]:
        result = agent.execute_tool(
            "dispatch_water_tankers",
            {"location": "chemical plant", "tanker_count": 4},
            ["dispatch_water_tankers"],
        )

    assert result.tool_receipts and result.tool_receipts[0].success
    rows = dispatch_store.list_dispatches(scope=scope)
    assert len(rows) == 1
    assert rows[0]["force_type"] == "water_tankers"
    assert rows[0]["quantity"] == 4
    assert rows[0]["status"] == "dispatched"

    reopened = open_persistence(db_path)
    reopened_store = open_operational_dispatch_store(db_path)
    assert reopened_store.list_dispatches(scope=scope) == rows
    reopened.close()


def test_dispatch_state_isolated_between_live_and_simulation_runs(tmp_path):
    db_path = str(tmp_path / "dispatch-scopes.sqlite")
    persistence = open_persistence(db_path)
    persistence.close()
    store = open_operational_dispatch_store(db_path)
    agent = FriendlyForcesAgent(model="m")
    agent.bind_dispatch_store(store)
    scopes = (
        OperationalScope.live(),
        OperationalScope.simulation("SEC_001", "run-a"),
        OperationalScope.simulation("FIRE_002", "run-b"),
    )

    for index, scope in enumerate(scopes):
        with operational_scope_context(scope), trusted_event_metadata({"operational_scope": scope}), authenticated_request_identity("commander"):
            agent.execute_tool(
                "dispatch_aircraft",
                {"location": f"sector-{index}", "aircraft_count": 1},
                ["dispatch_aircraft"],
            )

    assert [row["target"] for row in store.list_dispatches(scope=scopes[0])] == ["sector-0"]
    assert [row["target"] for row in store.list_dispatches(scope=scopes[1])] == ["sector-1"]
    assert [row["target"] for row in store.list_dispatches(scope=scopes[2])] == ["sector-2"]


def test_dispatch_store_failure_produces_failed_receipt_and_no_state(tmp_path):
    db_path = str(tmp_path / "dispatch-failure.sqlite")
    persistence = open_persistence(db_path)
    persistence.close()
    store = open_operational_dispatch_store(db_path)
    agent = FriendlyForcesAgent(model="m")
    agent.bind_dispatch_store(store)
    scope = OperationalScope.simulation("FIRE_002", "run-failure")

    original = store.create_dispatch

    def fail(**kwargs):
        raise RuntimeError("simulated dispatch persistence failure")

    store.create_dispatch = fail
    with operational_scope_context(scope), trusted_event_metadata({"operational_scope": scope}), authenticated_request_identity("commander"):
        try:
            agent.execute_tool(
                "dispatch_aircraft",
                {"location": "ridge", "aircraft_count": 1},
                ["dispatch_aircraft"],
            )
        except Exception as exc:
            receipts = exc.tool_receipts
        else:
            raise AssertionError("dispatch failure unexpectedly succeeded")

    assert receipts and receipts[0].success is False
    assert store.list_dispatches(scope=scope) == []
    store.create_dispatch = original
