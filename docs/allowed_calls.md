# Package Boundaries and Allowed Calls

Each subsystem exposes a package facade and keeps implementation details inside a small set of responsibility-focused modules. Cross-package calls must use a listed public surface; direct access to backend internals remains prohibited.

## Public surfaces

| Package | Canonical facade | Stable compatibility modules | Implementation modules |
|---|---|---|---|
| `persistence` | `persistence` | `persistence.interface`, `persistence.exceptions` | `contracts`, `schema`, `sqlite` |
| `config` | `config` | `config.base`, `config.settings_store` | `models`, `settings` |
| `auth` | `auth.permissions` | none | `permissions` |
| `registries` | `registries` | `registries.areas`, `registries.event_types` | `registry` |
| `profiles` | `profiles` | `profiles.loader`, `profiles.spec` | `contracts`, `loader`, `demo`, `example` |
| `agents` | `agents` | `agents.base`, `agents.registry`, `agents.results`, `agents.errors`, `agents.reference`, `agents.history` | `contracts`, `runtime`, `registry`, `builtins` |
| `protocols` | `protocols` | `protocols.model`, `protocols.loader`, `protocols.editor`, `protocols.executor` | `contracts`, `service`, `executor` |
| `history` | `history` | `history.interface`, `history.query` | `contracts`, `events`, `query`, `summaries` |
| `orchestrator` | `orchestrator.flows` | `orchestrator.main_agent`, `orchestrator.insights`, `orchestrator.precedent`, `orchestrator.queue` | `decisions`, `holds`, `question_flow`, `runtime`, `flows` |
| `api` | `api.app` | `api.auth`, `api.errors`, `api.ingestion`, `api.management`, `api.operations` | `contracts`, `http`, `routes`, `app` |
| `bot` | `bot.app`, `bot` | all former bot module paths remain aliases | `contracts`, `client`, `presentation`, `runtime`, `app` |
| `tools` | `tools` | `tools.logging_config`, `tools.tracing`, `tools._terminal_client_shared` | `observability`, `terminal`, executable clients, simulator |
| `cli` | shell entry points only | `cli.user_admin` | `user_admin` |

Compatibility modules are aliases registered by package facades; they do not correspond to duplicate physical files. New code should prefer the canonical facade or the physical responsibility module when working inside the same package.

## Dependency direction

- Persistence is the bottom layer and imports no application subsystem.
- Profiles, config, auth, registries, and observability do not call upward into API, bot, or orchestration.
- Agents, protocols, and history expose domain capabilities to orchestration.
- Orchestration is the only layer that coordinates business decisions.
- API translates HTTP into orchestration calls.
- Bot reaches the application only through HTTP and never imports API internals.
- CLI user administration is the only user-write path.
- Raw SQL remains confined to persistence implementation modules.

`tests/test_architecture.py` enforces the supported cross-package import graph. `tests/test_legacy_imports.py` separately proves that compatibility aliases resolve to the canonical module objects.
