"""Telegram group -> agent routing table (in-memory, DB-backed, write-through).

A deployment may bind a Telegram group chat to one specialist agent (or to
`main_agent` for full, unscoped routing). Messages that arrive from a bound
group are *hard-scoped*: the Main Agent still classifies intent, but the
`FlowDeps` it reasons over only expose that agent's protocols and tools (plus
`history_agent`, so historical questions keep working everywhere).

The table is loaded from persistence once at API startup and updated
write-through on every `upsert`/`remove` made through the running process.
Writes that bypass the process (the offline `cli.group_admin` command) become
visible after `refresh_interval_seconds`, when the next lookup re-reads the
backing table.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from protocols import ProtocolSet

if TYPE_CHECKING:
    from orchestrator.flows import FlowDeps
    from persistence import PersistenceInterface

MAIN_AGENT_TARGET = "main_agent"
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})

# Core agents every scoped view keeps, regardless of which specialist a group
# is bound to: the orchestrator itself, its insight/judgment helper, and the
# history narrator (history questions are allowed in every group).
_ALWAYS_VISIBLE_AGENTS = frozenset({"main_agent", "insights_agent", "history_agent"})
_HISTORY_AGENT = "history_agent"


class GroupNotRegisteredError(Exception):
    """A message arrived from a Telegram group that has no routing binding."""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        super().__init__(f"telegram group '{chat_id}' is not registered")


class InvalidRoutingTargetError(ValueError):
    """`agent_name` is neither a routable specialist nor `main_agent`."""


@dataclass(frozen=True)
class GroupBinding:
    chat_id: str
    agent_name: str
    label: str = ""
    auto_register: bool = False


class GroupRoutingTable:
    def __init__(
        self,
        persistence: "PersistenceInterface",
        routable_agent_names: frozenset[str] | set[str],
        refresh_interval_seconds: float = 60.0,
        clock=time.monotonic,
    ):
        self._persistence = persistence
        self._routable = frozenset(routable_agent_names)
        self._refresh_interval = refresh_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._bindings: dict[str, GroupBinding] = {}
        self._loaded_at: float | None = None

    # -- loading ---------------------------------------------------------

    def load(self) -> None:
        """(Re)load every binding from persistence; called once at startup and on staleness."""

        rows = self._persistence.list_groups()
        fresh = {
            str(row["chat_id"]): GroupBinding(
                str(row["chat_id"]),
                row["agent_name"],
                row.get("label") or "",
                bool(row.get("auto_register", False)),
            )
            for row in rows
        }
        with self._lock:
            self._bindings = fresh
            self._loaded_at = self._clock()

    def _refresh_if_stale(self) -> None:
        with self._lock:
            stale = self._loaded_at is None or (self._clock() - self._loaded_at) >= self._refresh_interval
        if stale:
            self.load()

    # -- reads -----------------------------------------------------------

    def get(self, chat_id: str) -> GroupBinding | None:
        self._refresh_if_stale()
        with self._lock:
            return self._bindings.get(str(chat_id))

    def all(self) -> tuple[GroupBinding, ...]:
        self._refresh_if_stale()
        with self._lock:
            return tuple(sorted(self._bindings.values(), key=lambda binding: binding.chat_id))

    def chat_ids_for(self, agent_name: str) -> tuple[str, ...]:
        return tuple(binding.chat_id for binding in self.all() if binding.agent_name == agent_name)

    @property
    def routable_targets(self) -> tuple[str, ...]:
        """Every value `upsert` accepts for `agent_name`, `main_agent` first."""

        return (MAIN_AGENT_TARGET, *sorted(self._routable - {MAIN_AGENT_TARGET}))

    # -- writes (write-through) -----------------------------------------

    def validate_target(self, agent_name: str) -> None:
        if agent_name != MAIN_AGENT_TARGET and agent_name not in self._routable:
            raise InvalidRoutingTargetError(
                f"'{agent_name}' is not a routable agent; allowed: {', '.join(self.routable_targets)}"
            )

    def upsert(self, chat_id: str, agent_name: str, label: str = "") -> GroupBinding:
        chat_id = str(chat_id).strip()
        if not chat_id:
            raise InvalidRoutingTargetError("chat_id must be a non-empty string")
        self.validate_target(agent_name)
        self._persistence.write_group(chat_id, agent_name, label or "")
        record = self._persistence.read_group(chat_id)
        binding = GroupBinding(
            chat_id,
            agent_name,
            label or "",
            bool(record and record.get("auto_register", False)),
        )
        with self._lock:
            self._bindings[chat_id] = binding
        return binding

    def register_telegram_group_if_missing(self, chat_id: str, label: str = "") -> GroupBinding:
        chat_id = str(chat_id).strip()
        if not chat_id:
            raise InvalidRoutingTargetError("chat_id must be a non-empty string")
        record = self._persistence.register_telegram_group_if_missing(chat_id, label or "")
        binding = GroupBinding(
            str(record["chat_id"]),
            record["agent_name"],
            record.get("label") or "",
            bool(record.get("auto_register", False)),
        )
        with self._lock:
            self._bindings[chat_id] = binding
        return binding

    def approve(self, chat_id: str) -> GroupBinding:
        record = self._persistence.approve_group(str(chat_id))
        binding = GroupBinding(
            str(record["chat_id"]),
            record["agent_name"],
            record.get("label") or "",
            False,
        )
        with self._lock:
            self._bindings[str(chat_id)] = binding
        return binding

    def remove(self, chat_id: str) -> None:
        chat_id = str(chat_id)
        self._persistence.delete_group(chat_id)  # NotFoundError propagates untouched
        with self._lock:
            self._bindings.pop(chat_id, None)


# -- scope resolution --------------------------------------------------------


def resolve_scope(table: GroupRoutingTable, chat_id: str | None, chat_type: str | None) -> str | None:
    """Which agent a message is scoped to.

    Returns None for private chats (or when the caller sent no chat metadata —
    HTTP callers other than the bot), the bound agent name for a registered
    group, and raises `GroupNotRegisteredError` for a group with no binding.
    A `main_agent` binding also returns `main_agent`; callers treat that as
    unscoped."""

    if not chat_id or not chat_type or chat_type not in GROUP_CHAT_TYPES:
        return None
    binding = table.get(chat_id)
    if binding is None:
        raise GroupNotRegisteredError(str(chat_id))
    return binding.agent_name


def is_scoped_target(agent_name: str | None) -> bool:
    return agent_name is not None and agent_name != MAIN_AGENT_TARGET


def scope_deps(deps: "FlowDeps", agent_name: str) -> "FlowDeps":
    """A `FlowDeps` whose registry and protocol set only expose `agent_name` (+ history).

    Protocols survive only if every participating agent is the bound agent or
    `history_agent` — multi-agent protocols drop out of a single-agent group by
    design. Everything else on `deps` (persistence, settings, registries) is
    shared with the unscoped instance."""

    if not is_scoped_target(agent_name):
        return deps
    visible_agents = _ALWAYS_VISIBLE_AGENTS | {agent_name}
    allowed_participants = {agent_name, _HISTORY_AGENT}
    scoped_protocols = tuple(
        protocol
        for protocol in deps.protocol_set.all()
        if set(protocol.participating_agents) <= allowed_participants
    )
    return replace(
        deps,
        registry=deps.registry.restricted_to(visible_agents),
        protocol_set=ProtocolSet(protocols=scoped_protocols),
    )
