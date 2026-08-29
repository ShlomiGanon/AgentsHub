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
| `history` | `history` | `history.interface`, `history.events`, `history.extraction`, `history.time_utils`, `history.write` | `contracts`, `event_pipeline`, `field_catalog`, `query`, `summaries` |
| `orchestrator` | `orchestrator.flows` | `orchestrator.main_agent`, `orchestrator.insights`, `orchestrator.precedent`, `orchestrator.decisions`, `orchestrator.question_flow`, `orchestrator.queue`, `orchestrator.runtime` | `reasoning`, `holds`, `event_queue`, `flows`, `capabilities` |
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

## Operation matrix

Maps every API route, bot command/callback, and message intent to its `RequestedOperation` (docs/vocabulary.md) and viewer availability. `ViewerAllowedAction` (docs/Next_Plan.md §5) is the sole viewer allowlist; this table only records the mapping from entry point to operation, it does not duplicate the allowlist itself.

| Entry point | `RequestedOperation` | Viewer allowed |
|---|---|---|
| `POST /Event` | `submit_event` | yes |
| `POST /Msg`, `POST /Msg/Stream` (entry gate, before intent is known) | `submit_message` | yes |
| `POST /Msg` — resolved `conversational` intent | `converse` | yes |
| `POST /Msg` — resolved `question` intent | `ask_question` | yes — own submitted events only |
| `POST /Msg` — resolved `report` intent | `report_event` | yes |
| `POST /Msg` — resolved `request` intent | `request_action` | yes |
| `GET /Protocol` | `list_protocols` | no |
| `POST /Protocol` | `create_protocol` | no |
| `PUT /Protocol/<name>` | `update_protocol` | no |
| `DELETE /Protocol/<name>` | `delete_protocol` | no |
| `GET /SYSTEM` — identity + `event_types` + `areas` slice | `view_profile_overview` | yes |
| `GET /SYSTEM` — agent names, protocol bodies, scheduler, queue/held-count slice | `view_system_internals` | no |
| `GET /SYSTEM` — settings slice | `view_settings` | no |
| `PUT /SYSTEM` | `change_settings` | no |
| `GET /User/<identity>` | `view_user_registration` | yes — own identity only |
| `GET /Commanders` | `view_commander_roster` | no |
| `GET /Job/<event_id>` | `view_job_status` | yes — own submitted events only |
| `POST /Clarify/<event_id>` | `resolve_clarification` | no |
| `POST /Approve/<event_id>` | `approve_run` | no |
| `GET /Notifications` | `poll_notifications` | no |
| bot `/profile view`, `/profile diff` | `view_profile_overview` | yes |
| bot `/profile add\|edit\|remove` | `create_protocol` / `update_protocol` / `delete_protocol` | no |
| bot `/settings view` | `view_settings` | no |
| bot `/settings set ...` | `change_settings` | no |
| bot free-text message | `submit_message`, then the resolved-intent operation above | yes, subject to the intent operation |
| bot approval callback (`approve:<event_id>:<choice>`) | `approve_run` | no |
| bot clarification callback (`clarify:<event_id>:<classification>`) | `resolve_clarification` | no |

`GET /SYSTEM` remains one endpoint, gated by `view_profile_overview`; `api/routes.py::get_system` builds its JSON response field-group by field-group, each behind its own operation (`view_system_internals` for `agents`/`protocols`/`queued_events`/`held_events`/`scheduler`, `view_settings` for `settings`), so a viewer's response has those fields absent entirely rather than gating the whole endpoint at once — see `docs/api_spec.md`'s `GET /SYSTEM` section for both response shapes.

This matrix reflects the completed implementation (docs/Next_Plan.md Stages 0–7, all complete as of 2026-08-29) — not a proposal.
