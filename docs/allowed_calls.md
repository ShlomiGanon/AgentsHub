# Allowed Calls

One short reference for which package may import which, per work_plan.md §1.1.
This is the document the import-graph test (`tests/test_architecture.py`)
enforces mechanically — if this file and that test ever disagree, the test
is checking the wrong thing and must be fixed to match this file.

## The rule

Every package exposes a fixed set of **entry-point modules** — the only
modules another package may import from it. Everything else inside a
package is private to that package. A package may always import its own
internal modules freely; the restriction is only on *cross-package*
imports.

## Entry points per package

| Package | Entry-point module(s) | Notes |
|---|---|---|
| `persistence` | `persistence.interface`, `persistence.exceptions` | Never `persistence.sqlite_backend`, `persistence.schema`, or `persistence.migrations` directly — those are engine-specific and stay behind the interface. Construct a concrete backend via `persistence.interface.open_persistence(db_path)`, the one place that decides which engine is built. The interface's operations include `update_event` (added in Mission 2, alongside `append_event`, beyond §2.7's original literal list — see `docs/progress.md`). |
| `config` | `config.base`, `config.settings_store` | |
| `auth` | `auth.permissions` | |
| `registries` | `registries.event_types`, `registries.areas` | Read-only closed sets built from a `LoadedProfile` at startup. |
| `profiles` | `profiles.loader`, `profiles.spec` | `profiles.validate` is called only by `profiles.loader`, not directly by other packages. |
| `tools` | `tools.logging_config`, `tools.tracing` | Shared helpers belonging to no subsystem. |
| `cli` | none (not importable) | `cli.user_admin` is an entry point run from the shell, never imported by another package. |
| `agents` | `agents.registry`, `agents.results`, `agents.errors`, `agents.reference` | `agents.reference` is the reference agent (§3.11) — a concrete agent module a profile constructs directly, not a query interface. Every future concrete agent (a real domain specialist) becomes an entry point the same way, one module per agent. `agents.base`, `agents.descriptor`, `agents.tooling`, `agents.adapter` stay internal — only agents/ package files import them. |
| `protocols` | `protocols.model`, `protocols.loader`, `protocols.editor`, `protocols.executor` | `protocols.retry` stays internal, called only by `protocols.executor` — parallel to `profiles.validate`'s status. |
| `history` | `history.query` *(lands in §5)* | |
| `orchestrator` | `orchestrator.flows` *(lands in §6)* | |
| `api` | `api.app` *(lands in §7)* | |
| `bot` | `bot.app` *(lands in §8)* | |

## Who may call whom

- **`orchestrator`** calls everything — it is the only component that makes
  judgment calls and coordinates every other subsystem.
- **`persistence`** calls nothing outside itself. It is the bottom of the
  stack; every other subsystem depends on it, never the reverse.
- **`bot`** calls only `api`. It has no other path into the system.
- **`api`** calls `orchestrator`, `profiles`, `config`, `auth`, `persistence`.
- **`agents`**, **`protocols`**, **`history`** are called by `orchestrator`
  and by each other's entry points where the work plan says so (e.g. the
  protocol executor calls into `agents`); none of them call `orchestrator`,
  `api`, or `bot`.
- **`profiles`**, **`config`**, **`auth`**, **`registries`**, **`tools`** are
  low-level and may be called by anything; none of them call upward into
  `orchestrator`, `api`, or `bot`.
- **`cli`** stands alone. It calls `profiles` (to resolve a database path)
  and `persistence`/`auth` directly for user administration. Nothing else
  calls `cli`, and `cli` is the *only* path that writes users — no code in
  `api`, `bot`, or `orchestrator` may do the same (work_plan.md §1.10).

## Cross-cutting exception (1.8)

`tools.logging_config` and `tools.tracing` are the one deliberate exception
to "import only entry points": because structured logging and trace-ID
propagation must be reachable from literally every module that logs
anything, every package may import them directly, including from internal
(non-entry-point) modules. No other cross-cutting exception exists — adding
one requires updating this document first.
