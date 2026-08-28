# Deep Behavior-Preserving Refactor Plan

## Implementation Status — Complete

Completed on 2026-08-28.

- Production implementation modules: 63 → 42.
- Production lines: 11,613 → 8,629.
- Classes/functions preserved: 106/521 → 106/521.
- Comment and docstring lines: 3,021 → 494 (83.6% reduction).
- Legacy import paths remain supported by package-level compatibility aliases.
- The English final-tree catalog contains 170 first-party files and is enforced by a completeness test.
- Full acceptance: 879 passed, 0 failed; the six executable `--help` smoke checks also pass.

Implementation deviations:

- Existing test modules were not physically merged. Keeping scenario isolation preserved every original assertion and avoided turning the test tree into a few oversized files; compatibility and catalog coverage were added instead.
- Historical reports and the append-only progress log retain old path references as historical evidence. All live documentation and production imports use the new responsibility layout.
- `orchestrator/question_flow.py` remains separate because synchronous historical Q&A is a distinct responsibility and the package still meets its five-module target.
- No dependency, database schema, prompt, route, public signature, executable path, or business-rule change was introduced.

## 1. Objective

Restructure the entire codebase around a small number of responsibility-focused files per subsystem, remove historical and redundant comments, use clearer file names, and preserve all observable behavior.

This is an architectural refactor only. It must not change business decisions, prompts, persistence semantics, HTTP and Telegram contracts, command behavior, concurrency, logging, or deployment configuration.

Implementation starts only after explicit user approval of this plan.

## 2. Verified Baseline

Repository measurements taken before planning:

- 13 production subsystem packages.
- 63 production Python modules, excluding `__init__.py` files.
- 11,613 production lines.
- 106 production classes and 521 functions/methods.
- 655 comment lines and 2,366 docstring lines.
- 68 collected test modules, including 13 integration modules.
- 834 tests collected.
- 834 tests pass when the documented test-tier and fixture environment variables are supplied.
- Current worktree was clean before this plan was written.

Current concentration points:

- `orchestrator/flows.py`: 617 lines and the complete event state machine.
- `persistence/sqlite_backend.py`: 502 lines plus a separate 262-line schema module.
- `bot/`: 12 production modules and 2,375 lines, with contracts, transport, presentation, polling, and startup spread across many small files.
- `api/`: route groups already merged, but the merged files retain repeated imports, repeated module narratives, and mixed serialization logic.
- `tools/`: 1,880 lines, largely logging explanations and duplicated terminal-client behavior.
- Model-tier environment resolution is repeated in the API, bot, and user-admin entry points.
- Area and event-type registries are structurally duplicated.

## 3. Non-Negotiable Behavior Contract

The refactor must preserve the following exactly unless the user separately approves a behavior change.

### 3.1 Domain and AI behavior

- Agent names, roles, system prompts, model routing, API-key routing, timeouts, and tool metadata.
- `Agent.process(text, allowed_tools)` behavior and per-call tool authorization.
- CrewAI construction, dynamic tool signatures, raw-output parsing, and exception translation.
- Profile validation, secret resolution, profile hashing, immutability, model-tier assignment, and startup failure messages.
- Main Agent intent, risk, selection, formulation, rewrite, judgment, and question-routing prompts and parsers.
- Precedent lookup, clarification, approval, retry, insight, and terminal-outcome decisions.
- Exact step ordering and the prohibition on retrying completed non-idempotent side effects.

### 3.2 External interfaces

- Every HTTP method, route, request field, response body, status code, error class, authentication check, and authorization check.
- Telegram commands, callback payloads, formatted messages, message splitting, replies, notification targeting, and notification cursor behavior.
- CLI arguments, help behavior, stdout/stderr text, exit codes, and executable module paths.
- These executable paths remain physical modules: `api.app`, `bot.app`, `cli.user_admin`, `tools.simulator`, `tools.terminal_client_commander`, and `tools.terminal_client_viewer`.

### 3.3 Persistence and runtime behavior

- SQLite schema, migration order, `PRAGMA user_version`, indexes, JSON encodings, timestamps, transactions, and error translation.
- Existing database upgrade behavior and fresh-database creation behavior.
- Atomic profile and settings writes.
- Single SQLite writer ordering, serial event ordering, scheduler lifecycle, bot single-instance locking, and notification polling.
- Trace-ID and stage propagation across request and worker threads.
- Log event names, levels, structured fields, console formats, database log sink, and debug-gated raw model I/O.

### 3.4 Compatibility boundary

- Private file names and private monkeypatch targets may change; all first-party callers and tests must move with them.
- Public symbols, class names, dataclass field order, enum values, exception messages, function signatures, and executable paths remain stable.
- Package roots become the canonical cross-package facades: `from agents import Agent`, `from persistence import open_persistence`, and so on.
- Previously documented public module imports remain available through centralized aliases/re-exports in each package's `__init__.py`; physical one-line compatibility files will not be retained.
- A compatibility test will import every supported legacy path and verify symbol identity.

## 4. Target Design Rules

1. Each subsystem exposes one package-root facade. Cross-package imports use only that facade.
2. Each subsystem has no more than five non-entry-point implementation files unless an executable wrapper must remain physical.
3. Passive classes are grouped in that subsystem's `contracts.py`: frozen dataclasses, enums, literals, exceptions, ABCs, and protocols.
4. Stateful service classes stay beside the behavior they implement. `SQLitePersistence`, `SummaryScheduler`, `SerialEventQueue`, HTTP clients, and Telegram clients are not moved into a generic class dump.
5. Each file owns one named responsibility. A file may contain several closely related functions or classes when they change for the same reason.
6. Package facades contain imports and compatibility aliases only; no business logic.
7. Dependency direction remains downward. Persistence never imports application layers, bot reaches the system only over HTTP, and API delegates decisions to the orchestrator.
8. No new top-level package is introduced.
9. Imports at runtime must be acyclic. `TYPE_CHECKING` imports may be used for type annotations.
10. Target size is normally 80–400 lines per implementation file. Files over 500 lines require a responsibility-based justification, not arbitrary splitting.

## 5. Target Production File Layout

The target is approximately 42 production modules excluding `__init__.py`, down from 63. Every subsystem will have between one and five implementation modules.

### 5.1 `agents/` — four implementation modules

- `agents/__init__.py` — Public facade and legacy import aliases.
- `agents/contracts.py` — `AgentResult`, `ToolInfo`, `AgentDescriptor`, output constants, parsing, and all agent exceptions.
- `agents/runtime.py` — `Agent`, the `tool` decorator, per-call tool enforcement, CrewAI loading, dynamic tool construction, and invocation.
- `agents/registry.py` — `AgentRegistry`, duplicate-name validation, descriptor lookup, and registry construction.
- `agents/builtins.py` — `HistoryAgent` and `ReferenceAgent`.

Merge and rename map:

- `base.py` + `adapter.py` + behavioral parts of `runtime.py` -> `runtime.py`.
- `results.py` + `errors.py` + passive descriptor types -> `contracts.py`.
- `history.py` + `reference.py` -> `builtins.py`.
- `registry.py` remains, with imports redirected through contracts/runtime.

### 5.2 `api/` — four implementation modules

- `api/__init__.py` — Public facade and legacy import aliases.
- `api/contracts.py` — `ApiContext`, `ApiError`, and all typed API error classes.
- `api/http.py` — Authentication, authorization, common request parsing, and error-to-response handlers.
- `api/routes.py` — All endpoint blueprints, job/hold serialization, system management, and notification payload serialization, grouped internally by endpoint family.
- `api/app.py` — Dependency assembly, queue dispatch, Flask app creation, environment-root startup, and the physical `python -m api.app` entry point.

Merge and rename map:

- `auth.py` + handler behavior from `errors.py` -> `http.py`.
- error classes from `errors.py` + `ApiContext` -> `contracts.py`.
- `ingestion.py` + `operations.py` + `management.py` -> `routes.py`.
- Repeated imports and repeated mid-file module docstrings are removed during the merge.

### 5.3 `auth/` — one implementation module

- `auth/__init__.py` — Public facade.
- `auth/permissions.py` — `PermissionLevel`, the action table, and the shared permission check.

No behavior move is required.

### 5.4 `bot/` — five implementation modules

- `bot/__init__.py` — Public facade used by terminal tools and legacy import aliases.
- `bot/contracts.py` — Bot DTOs, literals, `BotDeps`, caller context, bot exceptions, `BotApiClient`, and `TelegramClient` ABCs.
- `bot/client.py` — `HttpApiClient`, `UnimplementedApiClient`, HTTP request/error translation, and the PTB Telegram implementation.
- `bot/presentation.py` — User resolution, permission refusal, commands, holds, formatting, notification formatting, and notification dispatch.
- `bot/runtime.py` — Notification polling, persistent cursor, startup validation, and single-instance lock.
- `bot/app.py` — Dependency construction, incoming-message routing, Telegram handler registration, environment-root startup, and the physical `python -m bot.app` entry point.

Merge and rename map:

- `api_client.py` passive types and ABC -> `contracts.py`; implementations -> `client.py`.
- `http_api_client.py` -> `client.py`.
- `telegram_client.py` ABC -> `contracts.py`; PTB implementation -> `client.py`.
- `deps.py` and passive startup errors -> `contracts.py`.
- `commands.py` + `holds.py` + `formatting.py` + `users.py` + dispatch portions of `notifications.py` -> `presentation.py`.
- polling/cursor portions of `notifications.py` + lock/startup behavior from `startup.py` -> `runtime.py`.
- `interface.py` becomes a package-facade alias and is removed physically.

### 5.5 `cli/` — one implementation module

- `cli/__init__.py` — Package marker.
- `cli/user_admin.py` — Parser, user administration, output, and physical module entry point.

The only internal change is reuse of centralized model-tier environment resolution.

### 5.6 `config/` — two implementation modules

- `config/__init__.py` — Public facade and legacy import aliases.
- `config/models.py` — Debug flags, `TierModel`, `BaseConfig`, model-tier construction, and the shared environment resolver used only by executable roots.
- `config/settings.py` — Live runtime settings and atomic JSON persistence.

Merge and rename map:

- `base.py` -> `models.py` and gains the currently duplicated tier-environment resolver without changing its error text.
- `settings_store.py` -> `settings.py`.

### 5.7 `history/` — four implementation modules

- `history/__init__.py` — Public facade and legacy import aliases.
- `history/contracts.py` — Extraction, event-envelope, query-answer, source, precedent, and history error types.
- `history/events.py` — Timestamp normalization, extraction, initial/incremental writes, state validation, and outcome writes.
- `history/query.py` — Range retrieval, exact-source selection, question answering, most-recent lookup, and precedent search.
- `history/summaries.py` — Summary generation, stale detection, reconciliation, and `SummaryScheduler`.

Merge and rename map:

- `extraction.py` + `write.py` + `time_utils.py` -> `events.py`.
- passive classes and errors from those modules and `query.py` -> `contracts.py`.
- `scheduler.py` -> `summaries.py`.
- `interface.py` becomes the package facade and is removed physically.

### 5.8 `orchestrator/` — five implementation modules

- `orchestrator/__init__.py` — Public facade and legacy import aliases.
- `orchestrator/contracts.py` — Flow, intent, selection, formulation, risk, verdict, hold, and agent-selection result types and orchestration errors.
- `orchestrator/decisions.py` — Main/Insights agents, prompt builders/parsers, conversational and historical question routing, protocol selection, formulation, judgment, insight generation, and precedent closure decisions.
- `orchestrator/holds.py` — Clarification/approval creation, authorization, resolution, and persisted hold transitions.
- `orchestrator/runtime.py` — `SerialEventQueue` and worker lifecycle.
- `orchestrator/flows.py` — Public event/request/message state machine and resume paths.

Merge and rename map:

- `main_agent.py` + `insights.py` + `question_flow.py` + `precedent.py` -> `decisions.py`, with passive result types moved to `contracts.py`.
- `queue.py` -> `runtime.py`.
- `holds.py` and `flows.py` remain behavior boundaries but import only the new contracts and decision surface.

### 5.9 `persistence/` — three implementation modules

- `persistence/__init__.py` — The only public facade and legacy import aliases.
- `persistence/contracts.py` — `PersistenceInterface`, `PersistenceError`, `NotFoundError`, and the backend factory contract.
- `persistence/schema.py` — SQLite DDL, immutable migration history, table/index constants, and migration runner.
- `persistence/sqlite.py` — Encoding/decoding helpers, serialized writer, reads, writes, transaction boundaries, notification side effects, and `SQLitePersistence`.

Merge and rename map:

- `interface.py` + `exceptions.py` -> `contracts.py`.
- `sqlite_backend.py` -> `sqlite.py`.
- `schema.py` remains separate because migration history and runtime I/O change for different reasons.

### 5.10 `profiles/` — four implementation modules

- `profiles/__init__.py` — Public facade and legacy import aliases.
- `profiles/contracts.py` — `AgentSpec`, `LoadedProfile`, profile constants, required shape declarations, and typed loader/validation errors.
- `profiles/loader.py` — Import, secret resolution, construction, validation, file hashing, and immutable profile loading.
- `profiles/demo.py` — Runnable demo deployment profile.
- `profiles/example.py` — Authoring reference profile, renamed from `reference.py` to state its role clearly.

Merge and rename map:

- `spec.py` + passive classes/errors from `loader.py` -> `contracts.py`.
- `reference.py` -> `example.py`.

Profile source files will retain legacy imports through package aliases where that avoids changing their byte-level profile hash unnecessarily.

### 5.11 `protocols/` — three implementation modules

- `protocols/__init__.py` — Public facade and legacy import aliases.
- `protocols/contracts.py` — `CriticalityLevel`, `Protocol`, `Step`, execution result types, and protocol errors.
- `protocols/service.py` — Read-only protocol set, loading, profile-source editing, rendering, and atomic replacement.
- `protocols/executor.py` — Retry eligibility, backoff, task rewriting, step execution, and ordered protocol execution.

Merge and rename map:

- `model.py` + passive types/errors from `executor.py` and `editor.py` -> `contracts.py`.
- `loader.py` + `editor.py` -> `service.py`.
- `executor.py` retains behavioral execution only.

### 5.12 `registries/` — one implementation module

- `registries/__init__.py` — Public facade and legacy import aliases.
- `registries/registry.py` — Shared immutable closed-set behavior plus the preserved `AreaRegistry` and `EventTypeRegistry` names and builders.

Merge and rename map:

- `areas.py` + `event_types.py` -> `registry.py`.

The two public registry classes remain distinct types even if they share a private implementation helper.

### 5.13 `tools/` — five implementation modules

- `tools/__init__.py` — Public facade and legacy import aliases.
- `tools/observability.py` — Trace/stage context, console formatters, database log handler, logging configuration, and debug model-I/O logging.
- `tools/terminal.py` — Shared console Telegram client, observing API client, terminal identity lifecycle, notification helpers, and the shared commander/viewer REPL engine.
- `tools/terminal_client_commander.py` — Thin physical entry-point wrapper selecting commander mode.
- `tools/terminal_client_viewer.py` — Thin physical entry-point wrapper selecting viewer mode.
- `tools/simulator.py` — Sensor-event simulator and physical entry point.

Merge and rename map:

- `logging_config.py` + `tracing.py` -> `observability.py`.
- `_terminal_client_shared.py` + common logic from both terminal clients -> `terminal.py`.
- Commander/viewer modules keep only parser/mode selection required to preserve current executable paths.

### 5.14 `fixtures/` — two maintained data responsibilities

- `fixtures/seed_events.py` — Historical seed dataset and its loader.
- `fixtures/profiles/minimal_profile.py` — Minimal deployment profile used by loading and integration tests.

Package markers remain where Python imports require them. Fixture records, ordering, contradictions, timestamps, and expected coverage cases remain byte-for-byte unless an import-only edit is necessary. Fixture code is not merged into tests because it is reusable input, not test behavior.

### 5.15 Root configuration and executable support

- Keep `README.md`, `instructions.md`, `.env.example`, `load-env.ps1`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt`, and `conftest.py` as distinct responsibilities.
- Do not read, rewrite, or catalog secret values from `.env`.
- Keep runtime and development dependency lists separate.
- Reduce repeated test-tier setup explanations in `conftest.py`, but keep fixture behavior and fail-fast messages unchanged.
- Remove the stray tracked or untracked `$null` artifact only if a final ownership check proves it is accidental and the user approves deletion; the refactor will not assume that.

### 5.16 `docs/` — live documentation plus preserved historical artifacts

Live documentation will be reduced to a small responsibility-based set:

- `docs/architecture.md` — Package boundaries, dependency direction, runtime flows, vocabulary, and non-obvious invariants; absorbs the live content of `allowed_calls.md` and `vocabulary.md`.
- `docs/api.md` — Current HTTP contract; renamed from `api_spec.md` after all links are updated.
- `docs/profile_authoring.md` — Profile and agent authoring contract; consolidates `profile_spec.md`, `agent_authoring.md`, and the maintained code example.
- `docs/operator_guide.md` — Startup, administration, Telegram connection, troubleshooting, and operational checks; absorbs `how_to_connect_telegram.md`.
- `docs/file_catalog.md` — English catalog of the final repository tree.

Historical sources are handled conservatively:

- `docs/work_plan.md` remains the original specification record.
- `docs/progress.md` remains append-only.
- Binary PDF/PPTX reference artifacts remain unchanged and are cataloged.
- Readiness reports, investigations, reviews, questions, and link notes may be consolidated into `docs/engineering_history.md` only after every unique statement and link is retained and all inbound references are updated.
- No historical document is deleted merely to meet a file-count target; lossless consolidation must be demonstrated in the diff.

## 6. Comment and Documentation Policy

Production comments will be minimized by the following mechanical rules:

- Module docstrings: one to three lines stating responsibility, not implementation history.
- Public API docstrings: concise contract and non-obvious failure semantics only.
- Private function docstrings: removed unless the function has a surprising invariant.
- Inline comments: explain only why code must be unusual; never restate the next statement.
- Remove mission numbers, audit narratives, “found/fixed” history, future-work essays, and duplicated architecture descriptions from production code.
- Preserve short explanations for CrewAI signature injection, context propagation across threads, non-idempotent retry prevention, atomic replacement, SQLite timestamp comparison, migration immutability, and notification writes sharing a transaction with state changes.
- Move any still-useful long rationale into architecture documentation rather than deleting the knowledge.

Acceptance checks:

- Production references to `work_plan`, mission numbers, and historical audit prose are eliminated except where a public compatibility requirement genuinely names a document.
- Comment/docstring lines fall materially from the current 3,021-line baseline.
- No behavior assertion is replaced by a comment.

## 7. Test Suite Consolidation

The suite will be reorganized after production imports stabilize.

- Keep all 834 existing test cases and assertions.
- Add characterization tests before moving risky code; the final count must be at least 834.
- Consolidate unit files by target responsibility, not into package-sized thousand-line files.
- Keep the 13 integration scenarios separate because each is an executable end-to-end behavior contract.
- Merge `api_fakes.py`, `bot_fakes.py`, and generic helpers into responsibility-named support modules only when imports remain clear.
- Keep the real-model sanity check non-collected.
- Add `test_legacy_imports.py` for every supported old public module path.
- Add `test_file_catalog.py` to ensure every tracked first-party file appears in the final catalog.

Target unit groups:

- Agents: contracts/runtime and registry/built-ins.
- API: runtime, ingestion, operations, and management.
- Bot: contracts/client, presentation, and runtime/app.
- History: events, queries, and summaries.
- Orchestrator: decisions, holds, and flows/runtime.
- Persistence: schema/migrations and SQLite conformance.
- Profiles, protocols, config/auth/registries, tools/CLI, and architecture/compatibility.

## 8. Behavior Snapshots Added Before Movement

Before changing production structure, add or record:

1. Flask route map: method, path, endpoint, success code, and representative error bodies.
2. Bot callback payloads and representative formatted messages.
3. CLI parser/help snapshots, outputs, and exit codes.
4. Agent prompts, parsed result types, tool schemas, exception types, and error strings.
5. Profile field values, dataclass field order, validation aggregation, and hash behavior.
6. SQLite fresh schema, each migration step, final `user_version`, indexes, and representative encoded rows.
7. Log event names, levels, required structured fields, trace correlation, and debug gating.
8. Queue ordering, resume-after-restart behavior, scheduler reconciliation, and cursor persistence.

Snapshots must assert semantic structures rather than unstable formatting where formatting is not itself part of the contract.

## 9. Implementation Sequence

### Phase 0 — Freeze the baseline

- Save the test-tier environment recipe without secrets.
- Run all 834 tests.
- Add the missing behavior snapshots above.
- Record the current public symbol/signature manifest, Flask route map, SQLite schema, and CLI entry points.
- Do not start movement until the strengthened baseline is green.

### Phase 1 — Introduce package facades and contract modules

- Define canonical package-root exports.
- Move passive types first, preserving class names, field order, frozen status, enum values, and exception inheritance.
- Add legacy aliases/re-exports centrally in package `__init__.py` files.
- Update the architecture test to allow only package-root cross-subsystem imports.
- Prove imports are acyclic and legacy imports resolve to the same objects.

### Phase 2 — Consolidate leaf subsystems

- Refactor `config`, `auth`, `registries`, `profiles`, and `protocols`.
- Centralize model-tier environment resolution.
- Merge duplicated registries while preserving distinct public types.
- Merge protocol load/edit behavior and keep execution separate.
- Run affected tests, architecture tests, profile-loading tests, and protocol source-edit tests after each package.

### Phase 3 — Consolidate the agent framework

- Move contracts and built-in agents.
- Combine Agent behavior and CrewAI adapter behavior without changing the invocation choke point.
- Keep dynamic tool schemas and monkeypatchable framework loading testable through the new runtime boundary.
- Run all agent, retry, logging, and real-CrewAI-construction tests.

### Phase 4 — Consolidate persistence and history

- Move the persistence interface/errors and rename the SQLite backend.
- Leave migration SQL byte-for-byte and order-for-order equivalent.
- Consolidate history events/time/write logic and rename the scheduler module.
- Run migration tests against fresh and upgraded database fixtures.
- Run persistence conformance, event, history accuracy, query, scheduler, and late-arrival tests.

### Phase 5 — Consolidate orchestration

- Move result types to contracts.
- Merge decision-only modules into `decisions.py`.
- Rename the queue runtime.
- Keep `flows.py` as the state-machine facade and `holds.py` as the durable human-decision boundary.
- Compare every terminal branch, persisted transition, log sequence, and resume path against baseline tests.

### Phase 6 — Consolidate API

- Move API context/errors, common HTTP boundary behavior, and route builders.
- Merge route files without changing route decorators or registration order.
- Keep queue submission timing and immediate `202` acknowledgments identical.
- Run every API unit and integration test plus the route-map snapshot.

### Phase 7 — Consolidate bot and terminal tools

- Move DTOs/interfaces first, then transports, then presentation, then runtime.
- Merge command/hold/notification presentation without changing strings or callback formats.
- Merge terminal engines while retaining physical commander/viewer wrappers.
- Run bot, HTTP-client, Telegram-client, notification, approval, clarification, command, failure, and terminal-entry tests.

### Phase 8 — Reduce comments and consolidate tests

- Remove historical production commentary only after the relevant invariant is enforced by a test or documented centrally.
- Move tests into the new responsibility groups without deleting tests or assertions.
- Search for stale imports, monkeypatch targets, old file references, and duplicate implementation paths.
- Delete superseded physical modules only after all callers have moved and compatibility aliases pass.

### Phase 9 — Documentation and final catalog

- Update `README.md`, `docs/allowed_calls.md`, `docs/api_spec.md`, `docs/profile_spec.md`, `docs/operator_guide.md`, and architecture documentation.
- Append the required refactor completion entries to `docs/progress.md`; never rewrite its existing history.
- Create `docs/file_catalog.md` in English from the final tree, after all moves and deletions.
- Catalog every tracked first-party file, including production, tests, fixtures, root configuration, documentation, and binary reference artifacts.
- Exclude generated caches, virtual environments, secrets, and untracked runtime databases.
- Each catalog row contains: path, category, public/private status, and one concise purpose statement.
- Add a test that fails when a tracked first-party file is missing from the catalog or a catalog path no longer exists.

### Phase 10 — Full acceptance

- Run the entire suite with the documented non-secret test environment.
- Run import-cycle and architecture checks.
- Run fresh-database and migration-upgrade tests.
- Run CLI help/smoke checks for all six physical executable module paths.
- Run stale-reference searches over code and documentation.
- Review the final diff for accidental prompt, SQL, message, route, or error-string changes.

## 10. Per-Phase Gate

Every phase must satisfy all of the following before the next begins:

- Relevant focused tests pass.
- `tests/test_architecture.py` passes.
- Legacy import compatibility tests pass.
- No new circular import is introduced.
- No behavior snapshot changes without explicit user approval.
- No new warning, skip, xfail, or flaky retry is used to hide a regression.
- `git diff --check` passes.
- Stale old-module imports and monkeypatch targets are zero for the completed subsystem.

If a regression appears, stop at that phase and fix it before continuing.

## 11. Final Acceptance Criteria

- At least all original 834 tests pass, plus new characterization/catalog/compatibility tests.
- Production implementation modules are reduced from 63 to approximately 42, with no subsystem exceeding five implementation modules except physical executable wrappers.
- Every target file has one documented responsibility.
- Public class names, dataclass fields, enums, exceptions, signatures, and executable paths remain stable.
- HTTP, Telegram, CLI, profile, AI, protocol, persistence, logging, tracing, queue, scheduler, settings, and notification behavior is unchanged.
- Fresh databases and every supported existing schema version work.
- Current profile files remain loadable and profile-editing remains atomic.
- No raw SQL escapes persistence.
- Bot still reaches the application only over HTTP.
- Comments are minimal and explain only non-obvious rationale.
- `docs/file_catalog.md` is English, complete, and mechanically checked against the final tracked tree.
- No stale imports, stale documentation paths, duplicate implementations, or orphaned compatibility files remain.

## 12. Git and Scope Constraints

- Do not commit, push, reset, or rewrite Git history.
- Preserve unrelated user changes if the worktree becomes dirty.
- Keep implementation changes uncommitted for review.
- Do not add dependencies unless consolidation cannot be completed with the current runtime and the user approves the addition.
- Do not change business rules or data structures under the label of cleanup.
- Any unavoidable behavior change requires a separate proposal and explicit approval before implementation.

## 13. Planned Final File Catalog Format

`docs/file_catalog.md` will be generated only after the final filesystem layout exists. Its content will be English and use this structure:

```markdown
# File Catalog

| Path | Category | Visibility | Purpose |
|---|---|---|---|
| `agents/runtime.py` | Production | Private | Implements agent invocation and per-call tool enforcement. |
| `api/app.py` | Production | Public entry point | Builds and starts the Flask API process. |
| `tests/test_agent_runtime.py` | Test | Internal | Verifies agent invocation, tool schemas, permissions, and error translation. |
```

The catalog is a final artifact, not a forecast: it must describe the actual post-refactor tree and pass the catalog completeness test.
