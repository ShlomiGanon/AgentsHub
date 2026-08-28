# Package Boundaries and Allowed Calls

Each subsystem exposes a package facade and keeps implementation details inside a small set of responsibility-focused modules. Cross-package calls must use a listed public surface; direct access to backend internals remains prohibited.

## Public surfaces

| Package | Canonical facade | Stable compatibility modules | Implementation modules |
|---|---|---|---|
| `persistence` | `persistence` | `persistence.interface`, `persistence.exceptions`, `persistence.sqlite`, `persistence.sqlite_backend` | `contracts`, `schema`, `sqlite_store` |
| `config` | `config` | `config.base`, `config.models`, `config.settings`, `config.settings_store` | `environment`, `live_settings` |
| `auth` | `auth.permissions` | none | `permissions` |
| `profiles` | `profiles` | `profiles.loader`, `profiles.spec`, `profiles.example`, `profiles.reference` | `contracts`, `loader`, `demo`, `template` |
| `agents` | `agents` | `agents.adapter`, `agents.base`, `agents.registry`, `agents.results`, `agents.errors`, `agents.builtins`, `agents.reference`, `agents.history` | `contracts`, `runtime`, `standard_agents` |
| `protocols` | `protocols` | `protocols.model`, `protocols.loader`, `protocols.editor`, `protocols.service` | `contracts`, `repository`, `executor` |
| `history` | `history` | `history.interface`, `history.events`, `history.extraction`, `history.time_utils`, `history.write` | `contracts`, `event_pipeline`, `query`, `summaries` |
| `orchestrator` | `orchestrator.flows` | `orchestrator.main_agent`, `orchestrator.insights`, `orchestrator.precedent`, `orchestrator.decisions`, `orchestrator.question_flow`, `orchestrator.queue`, `orchestrator.runtime` | `reasoning`, `holds`, `event_queue`, `flows` |
| `api` | `api.app` | `api.contracts`, `api.auth`, `api.errors`, `api.http`, `api.ingestion`, `api.management`, `api.operations` | `request_boundary`, `routes`, `app` |
| `bot` | `bot.app`, `bot` | all former bot module paths remain aliases | `contracts`, `transports`, `interactions`, `background_services`, `app` |
| `tools` | `tools` | `tools.logging_config`, `tools.tracing`, `tools.terminal`, `tools._terminal_client_shared` | `observability`, `terminal_support`, executable clients, simulator |
| `cli` | shell entry points only | `cli.user_admin` | `user_admin` |

Compatibility modules are aliases registered by package facades; they do not correspond to duplicate physical files. New code should prefer the canonical facade or the physical responsibility module when working inside the same package.

## Dependency direction

- Persistence is the bottom layer and imports no application subsystem.
- Profiles, config, auth, and observability do not call upward into API, bot, or orchestration. Area and event-type registries are immutable profile contracts.
- Agents, protocols, and history expose domain capabilities to orchestration.
- Orchestration is the only layer that coordinates business decisions.
- API translates HTTP into orchestration calls.
- Bot reaches the application only through HTTP and never imports API internals.
- CLI user administration is the only user-write path.
- Raw SQL remains confined to persistence implementation modules.

`tests/test_architecture.py` enforces the supported cross-package import graph. `tests/test_legacy_imports.py` separately proves that compatibility aliases resolve to the canonical module objects.
