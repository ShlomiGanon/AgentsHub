"""Profile-declared admin-panel tables.

A profile that wants an editable admin-panel table for one of its own persisted stores declares
a flat tuple of `AdminTable` entries as a module-level `ADMIN_TABLES` constant -- the same
"declare, don't construct" convention `profiles.simulation`'s `SimulationGroup`/
`SimulationPersona` and `profiles.contracts.AgentSpec` already use. `profiles.loader.load_profile`
reads it with a plain `getattr(profile_module, "ADMIN_TABLES", ())`, so a profile that declares
none is completely unaffected, and `api/admin_tables.py`'s one generic blueprint renders every
declared table the same way regardless of which profile it came from.

Every field/cascade validation lives in the store method a table's `write_fn` calls into, never
in the admin layer itself -- `AdminColumn` carries just enough metadata to render an HTML form
field and validate its shape (required, one of `choices`), not business rules.
"""

from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class AdminColumn:
    """One column of an `AdminTable`. `name` is the underlying row/dict key; `choices` (for
    `kind="select"`) should mirror the backing store's own CHECK constraint, if it has one, so
    the admin panel never offers a value the database itself would reject. `editable=False` is
    for primary keys and system-stamped timestamps -- a structural rule applied uniformly across
    every table, not a per-table exception."""

    name: str
    label: str
    kind: Literal["text", "number", "select", "checkbox", "readonly", "datetime"] = "text"
    choices: tuple[str, ...] = ()
    editable: bool = True
    required: bool = False


@dataclass(frozen=True)
class AdminTable:
    """One editable admin-panel table. `list_fn`/`get_fn`/`write_fn`/`delete_fn` each take the
    profile's own `FlowDeps` first -- a profile's `ADMIN_TABLES` entry is expected to close over
    its own store/agent instance and expose thin wrappers around the store methods the rest of
    the system already uses, never issue its own ad hoc SQL. `write_fn` is called with every
    editable column's submitted value in one dict; any cascade a field change implies (e.g. a
    drone leaving an active mission, an attendance approval stamp) happens inside the store
    method it calls, transparently -- there is deliberately no separate "action" primitive here,
    every field is edited the same way."""

    key: str
    label: str
    primary_key: str
    columns: "tuple[AdminColumn, ...]"
    list_fn: "Callable[[object], list[dict]]"
    get_fn: "Callable[[object, str], dict | None]"
    write_fn: "Callable[[object, dict], None]"
    delete_fn: "Callable[[object, str], None] | None" = None
    allow_create: bool = False
