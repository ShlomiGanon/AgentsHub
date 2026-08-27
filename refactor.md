# Behavior-Preserving Codebase Refactor

## Summary

Reduce production modules from 104 to about 80 and test modules from 103 to about 76 while preserving runtime behavior, documented interfaces, and the explicit `Profile` and `Agent` entities.

Existing user changes, including `README.md`, will be preserved.

## Entity Preservation

### Profile

- Keep `LoadedProfile` as an immutable class with the same name, fields, and behavior.
- Keep `AgentSpec` and the profile specification contract.
- Keep `load_profile` and all public profile import paths.
- Keep every deployment profile independently loadable by module path.
- Preserve validation, profile hashing, resolved secrets, protocol definitions, isolation, and restart behavior.
- Validation helpers may move into the loader, but the Profile entity will not become a dictionary, tuple, or collection of unrelated parameters.

### Agent

- Keep `Agent` as the base class with the same inheritance and `process(text, allowed_tools)` contract.
- Keep all concrete Agent subclasses, including Main, History, Insights, Reference, and profile agents.
- Keep one module per concrete Agent.
- Preserve `AgentDescriptor`, its fields, tool metadata, registry behavior, permission enforcement, model routing, and result parsing.
- Private supporting primitives may move into `agents.runtime`, but Agent public imports, class names, inheritance, and behavior remain unchanged.
- Agent classes will not be replaced with functions or dictionaries.

## Implementation Stages

### 1. Stabilize the Baseline

- Synchronize this approved plan into `refactor.md`.
- Configure temporary test-tier environment variables.
- Add a narrow `bot.interface` facade for terminal tools.
- Replace private bot imports with facade imports.
- Permit only terminal tools to invoke `cli.user_admin`.
- Update architecture documentation and tests.
- Require all 834 tests to pass before continuing.

### 2. Consolidate Low-Risk Internals

- Merge private Agent descriptor/tooling support into `agents.runtime`, while preserving the `AgentDescriptor` class and Agent contracts.
- Move profile validation helpers into the profile loader, while preserving `LoadedProfile`, `AgentSpec`, and public paths.
- Merge protocol retry policy into the executor.
- Merge history summary generation into the scheduler.
- Replace `ProtocolEditResult` with its existing success string.

### 3. Consolidate History and Persistence

- Move history retrieval and precedent lookup into the query module.
- Preserve existing history facade exports.
- Move migrations into the schema module.
- Keep persistence interface, exceptions, and SQLite implementation separate.
- Test fresh and upgraded databases.

### 4. Consolidate Main-Agent Decisions

Merge Main Agent intent, risk, protocol selection, task formulation, judgment, and parse helpers into `main_agent`.

- Preserve the `MainAgent` class, inheritance, prompts, parsers, results, logging, and execution order.
- Keep flows, holds, queue, precedent, insights, and question flow separate.
- Do not remove flow functions without production callers.

### 5. Consolidate API Routes

Group internal routes into:

- `ingestion`: events and messages.
- `operations`: jobs, holds, and notifications.
- `management`: protocols, settings, and users.

Keep app assembly, authentication, and errors separate. Preserve every route, endpoint name, status code, response, and permission check.

### 6. Consolidate Bot Internals

- Merge approval and clarification into `holds`.
- Merge polling, cursor state, failures, results, and precedent notices into `notifications`.
- Merge profile and settings commands into `commands`.
- Merge startup errors and singleton locking into `startup`.
- Move incoming-message handling into `app`.
- Preserve both terminal command module paths.

Simplify only:

- `ProfileDiffStatus` → `bool`.
- `ProtocolWriteResult` and `SettingsWriteResult` → shared `WriteResult`.
- Remove unused `InternalError`.

No Profile or Agent class is included in class simplification.

### 7. Consolidate Tests and Documentation

- Group unit tests according to the new modules.
- Preserve every test case and assertion.
- Keep all 13 integration scenarios separate.
- Consolidate manual model checks into one non-collected script.
- Update architecture and operational documentation.
- Do not perform unrelated cleanup.

## Verification

After every stage:

- Run affected tests and `tests/test_architecture.py`.
- Search for stale imports, monkeypatch targets, documentation paths, and CLI references.
- Stop on the first regression and fix it before continuing.

Final acceptance:

- All 834 tests pass.
- `LoadedProfile`, `AgentSpec`, `Agent`, `AgentDescriptor`, concrete Agent classes, and their public contracts remain intact.
- HTTP, Telegram, CLI, database, profile, logging, and trace behavior remain identical.
- Fresh and existing databases both work.
- No import cycles or stale module paths remain.
- Production reaches approximately 80 modules without oversized replacements.

## Git Constraint

- Never run `git commit`.
- Never run `git push`.
- Never rewrite or reset Git history.
- Use only read-only Git commands such as `git status` and `git diff`.
- Leave every implementation change uncommitted for user review.

## Assumptions

- Public runtime behavior and declared imports are compatibility requirements.
- Private supporting modules may move.
- Profile and Agent entities, class contracts, and inheritance are permanently preserved.
- Implementation starts only after explicit user approval.

