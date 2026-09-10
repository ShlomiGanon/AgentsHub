"""orchestrator.group_routing: in-memory table, staleness refresh, scope resolution, deps scoping."""

import pytest

from agents.runtime import AgentRegistry
from orchestrator.flows import (
    FlowDeps,
    GroupNotRegisteredError,
    GroupRoutingTable,
    InvalidRoutingTargetError,
    MAIN_AGENT_TARGET,
    is_scoped_target,
    resolve_scope,
    scope_deps,
)
from persistence.contracts import NotFoundError
from protocols.model import CriticalityLevel, Protocol
from protocols.repository import ProtocolSet


class _MemoryGroups:
    """Just the four group methods of PersistenceInterface, in a dict."""

    def __init__(self):
        self.rows = {}
        self.list_calls = 0

    def list_groups(self):
        self.list_calls += 1
        return [dict(row) for row in self.rows.values()]

    def read_group(self, chat_id):
        return self.rows.get(chat_id)

    def write_group(self, chat_id, agent_name, label=""):
        self.rows[chat_id] = {"chat_id": chat_id, "agent_name": agent_name, "label": label, "created_at": "t"}

    def delete_group(self, chat_id):
        if chat_id not in self.rows:
            raise NotFoundError(chat_id)
        del self.rows[chat_id]


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


ROUTABLE = frozenset({"team_status_agent", "surveillance_agent"})


def _table(store, clock=None, refresh=60.0):
    return GroupRoutingTable(store, ROUTABLE, refresh_interval_seconds=refresh, clock=clock or _Clock())


# -- table -------------------------------------------------------------------


def test_load_reads_every_binding_from_persistence_once():
    store = _MemoryGroups()
    store.write_group("-1", "team_status_agent", "readiness")
    store.write_group("-2", "main_agent")
    table = _table(store)

    table.load()

    assert table.get("-1").agent_name == "team_status_agent"
    assert table.get("-1").label == "readiness"
    assert table.get("-2").agent_name == MAIN_AGENT_TARGET
    assert [b.chat_id for b in table.all()] == ["-1", "-2"]
    assert store.list_calls == 1  # gets within the refresh window hit memory only


def test_upsert_is_write_through_and_visible_without_reload():
    store = _MemoryGroups()
    table = _table(store)
    table.load()

    binding = table.upsert("-5", "surveillance_agent", "cameras")

    assert binding.chat_id == "-5"
    assert store.rows["-5"]["agent_name"] == "surveillance_agent"
    assert table.get("-5") == binding
    assert table.chat_ids_for("surveillance_agent") == ("-5",)


def test_upsert_accepts_main_agent_and_rejects_unknown_targets():
    table = _table(_MemoryGroups())
    table.load()

    table.upsert("-1", MAIN_AGENT_TARGET)

    with pytest.raises(InvalidRoutingTargetError):
        table.upsert("-2", "insights_agent")
    with pytest.raises(InvalidRoutingTargetError):
        table.upsert("-3", "nonexistent_agent")
    with pytest.raises(InvalidRoutingTargetError):
        table.upsert("   ", "team_status_agent")
    assert table.routable_targets == ("main_agent", "surveillance_agent", "team_status_agent")


def test_remove_deletes_from_persistence_and_memory_and_propagates_not_found():
    store = _MemoryGroups()
    table = _table(store)
    table.load()
    table.upsert("-1", "team_status_agent")

    table.remove("-1")

    assert table.get("-1") is None
    assert "-1" not in store.rows
    with pytest.raises(NotFoundError):
        table.remove("-1")


def test_external_writes_become_visible_after_the_refresh_interval():
    store = _MemoryGroups()
    clock = _Clock()
    table = _table(store, clock=clock, refresh=60.0)
    table.load()

    store.write_group("-9", "team_status_agent")  # e.g. cli.group_admin in another process
    assert table.get("-9") is None  # still within the window: memory wins

    clock.now += 61
    assert table.get("-9").agent_name == "team_status_agent"


def test_first_lookup_without_explicit_load_loads_lazily():
    store = _MemoryGroups()
    store.write_group("-1", "team_status_agent")
    table = _table(store)

    assert table.get("-1") is not None


# -- resolve_scope -----------------------------------------------------------


def test_private_chats_and_missing_metadata_are_unscoped():
    table = _table(_MemoryGroups())
    table.load()

    assert resolve_scope(table, "123", "private") is None
    assert resolve_scope(table, None, None) is None
    assert resolve_scope(table, "123", None) is None


def test_registered_group_resolves_to_its_agent_and_unregistered_group_raises():
    table = _table(_MemoryGroups())
    table.load()
    table.upsert("-100", "team_status_agent")

    assert resolve_scope(table, "-100", "supergroup") == "team_status_agent"
    assert resolve_scope(table, "-100", "group") == "team_status_agent"
    with pytest.raises(GroupNotRegisteredError) as excinfo:
        resolve_scope(table, "-200", "group")
    assert excinfo.value.chat_id == "-200"


def test_main_agent_binding_is_not_a_scoped_target():
    assert is_scoped_target(MAIN_AGENT_TARGET) is False
    assert is_scoped_target(None) is False
    assert is_scoped_target("team_status_agent") is True


# -- scope_deps --------------------------------------------------------------


class _NamedAgent:
    def __init__(self, name):
        self.name = name
        self.descriptor = None


def _protocol(name, *agents):
    return Protocol(
        name=name,
        description=f"{name} description",
        participating_agents=tuple(agents),
        approved_tools=(),
        expected_success_output="ok",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    )


def _deps():
    registry = AgentRegistry({
        name: _NamedAgent(name)
        for name in ("main_agent", "insights_agent", "history_agent", "team_status_agent", "surveillance_agent", "friendly_forces_agent")
    })
    protocol_set = ProtocolSet(protocols=(
        _protocol("report_team_availability", "team_status_agent"),
        _protocol("record_attendance_response", "team_status_agent"),
        _protocol("camera_sweep", "surveillance_agent"),
        _protocol("overall_situational_picture", "surveillance_agent", "team_status_agent", "friendly_forces_agent"),
        _protocol("history_and_status", "history_agent", "team_status_agent"),
    ))
    return FlowDeps(
        persistence=None, settings_store=None, registry=registry, protocol_set=protocol_set,
        event_type_registry=None, area_registry=None, history_query_service=None,
    )


def test_scope_deps_restricts_registry_to_bound_agent_plus_core_agents():
    scoped = scope_deps(_deps(), "team_status_agent")

    assert {a.name for a in scoped.registry.all()} == {"main_agent", "insights_agent", "history_agent", "team_status_agent"}
    assert scoped.registry.get("team_status_agent") is not None
    with pytest.raises(KeyError):
        scoped.registry.get("surveillance_agent")


def test_scope_deps_keeps_only_protocols_whose_participants_are_the_bound_agent_or_history():
    scoped = scope_deps(_deps(), "team_status_agent")

    assert {p.name for p in scoped.protocol_set.all()} == {
        "report_team_availability", "record_attendance_response", "history_and_status",
    }
    assert scoped.protocol_set.get("overall_situational_picture") is None


def test_scope_deps_shares_agent_instances_and_other_deps_with_the_unscoped_instance():
    deps = _deps()
    scoped = scope_deps(deps, "surveillance_agent")

    assert scoped.registry.get("surveillance_agent") is deps.registry.get("surveillance_agent")
    assert scoped.persistence is deps.persistence
    assert scoped.conversation_history_turns == deps.conversation_history_turns


def test_scope_deps_with_main_agent_or_none_returns_the_same_deps():
    deps = _deps()

    assert scope_deps(deps, MAIN_AGENT_TARGET) is deps
    assert scope_deps(deps, None) is deps
