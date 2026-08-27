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
| `persistence` | `persistence.interface`, `persistence.exceptions` | Never import `persistence.sqlite_backend` or `persistence.schema` directly outside the package; schema and migrations remain engine-specific behind `open_persistence`. |
| `config` | `config.base`, `config.settings_store` | |
| `auth` | `auth.permissions` | |
| `registries` | `registries.event_types`, `registries.areas` | Read-only closed sets built from a `LoadedProfile` at startup. |
| `profiles` | `profiles.loader`, `profiles.spec` | Validation is owned by `profiles.loader`; `LoadedProfile`, `AgentSpec`, and the public loading contract remain unchanged. |
| `tools` | `tools.logging_config`, `tools.tracing` | Shared helpers belonging to no subsystem. |
| `cli` | none (not generally importable) | `cli.user_admin` is a shell entry point. The terminal frontends in `tools` are the sole caller-specific exception: they invoke the same command function so user writes still have one path. |
| `agents` | `agents.registry`, `agents.results`, `agents.errors`, `agents.reference`, `agents.base`, `agents.history` | Concrete agents remain one class per module. `agents.base` preserves the Agent contract; `agents.runtime` and `agents.adapter` remain internal framework support. |
| `protocols` | `protocols.model`, `protocols.loader`, `protocols.editor`, `protocols.executor` | Execution and its retry policy share the executor entry point. |
| `history` | `history.interface`, `history.query` | `history.interface` exposes writes, extraction, scheduler hooks, and `storage_timestamp`; `history.query` owns historical Q&A, range retrieval, and precedent lookup. Other `history/` modules stay internal. |
| `orchestrator` | `orchestrator.flows` | |
| `api` | `api.app` | `api.app.create_app(module_path)` assembles the running API. Internal routes are grouped in `ingestion`, `operations`, and `management`; authentication and error mapping remain separate. |
| `bot` | `bot.app`, `bot.interface` | `bot.app` is the runtime entry point. `bot.interface` is the narrow supported integration surface used by the two terminal frontends; bot-to-API communication remains a network boundary. |

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
