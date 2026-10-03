"""Telegram group-to-agent routing table, in-memory and write-through to persistence.

A bound agent is a protocol-selection hint only — never a filter that hides a protocol.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.flows import FlowDeps
    from persistence import PersistenceInterface

MAIN_AGENT_TARGET = "main_agent"
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})


class GroupNotRegisteredError(Exception):
    """A message arrived from a Telegram group that has no routing binding."""

    def __init__(self, chat_id: str):
        """Remember the unregistered chat_id for the caller to report."""

        self.chat_id = chat_id
        super().__init__(f"telegram group '{chat_id}' is not registered")


class InvalidRoutingTargetError(ValueError):
    """`agent_name` is neither a routable specialist nor `main_agent`."""


@dataclass(frozen=True)
class GroupBinding:
    """One Telegram group's bound agent and attendance-check settings."""

    chat_id: str
    agent_name: str
    label: str = ""
    auto_register: bool = False
    attendance_check_enabled: bool = False
    attendance_check_hour: int = 8


def _binding_from_record(record: dict) -> GroupBinding:
    """Build a GroupBinding from a persisted telegram_groups row."""

    hour = record.get("attendance_check_hour")
    enabled = record.get("attendance_check_enabled")
    return GroupBinding(
        str(record["chat_id"]),
        record["agent_name"],
        record.get("label") or "",
        bool(record.get("auto_register", False)),
        False if enabled is None else bool(enabled),
        int(hour) if hour is not None else 8,
    )


class GroupRoutingTable:
    """In-memory group bindings reloaded from persistence when stale."""

    def __init__(
        self,
        persistence: "PersistenceInterface",
        routable_agent_names: frozenset[str] | set[str],
        refresh_interval_seconds: float = 60.0,
        clock=time.monotonic,
    ):
        """Remember persistence, allowed agent names, and the refresh clock."""

        self._persistence = persistence
        self._routable = frozenset(routable_agent_names)
        self._refresh_interval = refresh_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._bindings: dict[str, GroupBinding] = {}
        self._loaded_at: float | None = None

    # --- loading ---

    def load(self) -> None:
        """(Re)load every binding from persistence; called once at startup and on staleness."""

        rows = self._persistence.list_groups()
        fresh = {str(row["chat_id"]): _binding_from_record(row) for row in rows}
        with self._lock:
            self._bindings = fresh
            self._loaded_at = self._clock()

    def _refresh_if_stale(self) -> None:
        """Reload from persistence when the in-memory table has aged out."""

        with self._lock:
            stale = self._loaded_at is None or (self._clock() - self._loaded_at) >= self._refresh_interval
        if stale:
            self.load()

    # -- reads -----------------------------------------------------------

    def get(self, chat_id: str) -> GroupBinding | None:
        """Return the binding for this chat, or None if unregistered."""

        self._refresh_if_stale()
        with self._lock:
            return self._bindings.get(str(chat_id))

    def all(self) -> tuple[GroupBinding, ...]:
        """Every current binding, ordered by chat_id."""

        self._refresh_if_stale()
        with self._lock:
            return tuple(sorted(self._bindings.values(), key=lambda binding: binding.chat_id))

    def chat_ids_for(self, agent_name: str) -> tuple[str, ...]:
        """Chat ids bound to this agent name."""

        return tuple(binding.chat_id for binding in self.all() if binding.agent_name == agent_name)

    @property
    def routable_targets(self) -> tuple[str, ...]:
        """Every value `upsert` accepts for `agent_name`, `main_agent` first."""

        return (MAIN_AGENT_TARGET, *sorted(self._routable - {MAIN_AGENT_TARGET}))

    # -- writes (write-through) -----------------------------------------

    def validate_target(self, agent_name: str) -> None:
        """Raise if agent_name is neither main_agent nor a routable specialist."""

        if agent_name != MAIN_AGENT_TARGET and agent_name not in self._routable:
            raise InvalidRoutingTargetError(
                f"'{agent_name}' is not a routable agent; allowed: {', '.join(self.routable_targets)}"
            )

    def upsert(
        self,
        chat_id: str,
        agent_name: str,
        label: str = "",
        *,
        attendance_check_enabled: bool | None = None,
        attendance_check_hour: int | None = None,
    ) -> GroupBinding:
        """Create or update a group's binding and write it through to persistence."""

        chat_id = str(chat_id).strip()
        if not chat_id:
            raise InvalidRoutingTargetError("chat_id must be a non-empty string")
        self.validate_target(agent_name)
        if attendance_check_hour is not None:
            hour = int(attendance_check_hour)
            if hour < 0 or hour > 23:
                raise InvalidRoutingTargetError("attendance_check_hour must be between 0 and 23")
            attendance_check_hour = hour
        self._persistence.write_group(
            chat_id,
            agent_name,
            label or "",
            attendance_check_enabled=attendance_check_enabled,
            attendance_check_hour=attendance_check_hour,
        )
        record = self._persistence.read_group(chat_id)
        if record is None:
            raise InvalidRoutingTargetError(f"telegram group '{chat_id}' was not stored")
        binding = _binding_from_record(record)
        with self._lock:
            self._bindings[chat_id] = binding
        return binding

    def register_telegram_group_if_missing(self, chat_id: str, label: str = "") -> GroupBinding:
        """Insert a pending group row when this chat_id is not already stored."""

        chat_id = str(chat_id).strip()
        if not chat_id:
            raise InvalidRoutingTargetError("chat_id must be a non-empty string")
        record = self._persistence.register_telegram_group_if_missing(chat_id, label or "")
        binding = _binding_from_record(record)
        with self._lock:
            self._bindings[chat_id] = binding
        return binding

    def approve(self, chat_id: str) -> GroupBinding:
        """Mark a pending group binding as approved."""

        record = self._persistence.approve_group(str(chat_id))
        binding = _binding_from_record(record)
        with self._lock:
            self._bindings[str(chat_id)] = binding
        return binding

    def remove(self, chat_id: str) -> None:
        """Delete a group binding from persistence and the in-memory table."""

        chat_id = str(chat_id)
        self._persistence.delete_group(chat_id)  # NotFoundError propagates untouched
        with self._lock:
            self._bindings.pop(chat_id, None)

    def rename(self, old_chat_id: str, new_chat_id: str) -> GroupBinding:
        """Rename a group's chat_id in place; persistence errors propagate unchanged."""

        old_chat_id = str(old_chat_id).strip()
        new_chat_id = str(new_chat_id).strip()
        record = self._persistence.rename_group(old_chat_id, new_chat_id)
        binding = _binding_from_record(record)
        with self._lock:
            self._bindings.pop(old_chat_id, None)
            self._bindings[new_chat_id] = binding
        return binding


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
    """True when the message is bound to a specialist rather than main_agent."""

    return agent_name is not None and agent_name != MAIN_AGENT_TARGET


def scope_deps(deps: "FlowDeps", agent_name: str) -> "FlowDeps":
    """A `FlowDeps` carrying `agent_name` as `preferred_agent_hint` for protocol
    selection's prompt -- registry and protocol set are otherwise unchanged.

    Every protocol stays a selection candidate and every agent stays available to
    execute one, from every group, regardless of which specialist that group is
    bound to: the bound agent is context/priority for the selection prompt only,
    never a filter that can make a protocol or its agent structurally unreachable.
    Everything on `deps` besides the hint is shared with the unscoped instance."""

    if not is_scoped_target(agent_name):
        return deps
    return replace(deps, preferred_agent_hint=agent_name)
