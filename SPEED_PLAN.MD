# AgentsHub Current Speed and Runtime Implementation Plan

## 1. Purpose

This document is the authoritative, sequential implementation plan for the speed, observability, Deep Debug, localization, and client-experience changes approved after the findings in `SPEED.MD`.

The stages are ordered so that each can be implemented and verified before the next begins. Behavior-changing optimizations that were explicitly deferred are documented in `docs/Next_Plan.md` and are not part of this implementation.

All source identifiers, comments, docstrings, prompts, and technical documentation must be written in English. Hebrew is permitted only inside the Hebrew user-interface message catalog and user-provided test data.

## 2. Locked Decisions

### 2.1 Runtime startup and reuse

- CrewAI must be imported and initialized during server startup rather than on the first user request.
- Startup must make one real, minimal model call for every unique Provider/Model combination configured by the active profile.
- Repeated agents using the same Provider/Model pair must not cause duplicate warmup calls.
- A warmup failure, provider timeout, invalid credential, invalid model, or invalid warmup response must fail startup. Degraded startup is forbidden.
- The startup provider call uses the profile's 30-second model timeout.
- LLM object reuse is allowed only when the matching `ProviderCapabilities` entry explicitly declares the implementation thread-safe.
- An undeclared or non-thread-safe provider must continue to create an isolated LLM instance per invocation.
- Cached state must never contain request text, conversation history, tool allowlists, trace state, output, or mutable per-invocation policy.
- Readability and explicit cache keys take priority over clever or implicit reuse.

### 2.2 Invocation limits

- Every profile must define `MAX_ITER = 8`.
- Every profile must define `MODEL_TIMEOUT_SECONDS = 30`.
- Both fields are required; missing or invalid values fail profile loading.
- `MAX_ITER` is passed explicitly to every CrewAI agent invocation.
- `MODEL_TIMEOUT_SECONDS` is passed to the provider/network layer, not only to CrewAI's outer `max_execution_time`.
- No provider-level automatic retry is added. Existing application retry and repair behavior remains authoritative.
- The existing `RETRY_COUNT` remains unchanged. In current profiles, `RETRY_COUNT = 3` still means three total attempts for eligible protocol steps.
- The current planner, risk/selection split, Insights/Judgment split, serial queue, and CrewAI tool loop remain unchanged.
- `STAGE_MODEL_POLICIES` is not connected to runtime in this implementation.

### 2.3 Telemetry and durable diagnostic data

- Every actual provider request inside CrewAI must produce structured telemetry.
- User-visible LLM trace messages contain only Provider/Model, latency in milliseconds, and consumed token counts.
- Deeper technical data remains in structured logs and SQLite.
- Stage and model telemetry currently marked `telemetry_only` must also be persisted to SQLite by trace ID.
- When `DEEP_DEBUG=true`, full raw prompts and raw model responses are persisted to SQLite.
- When `DEEP_DEBUG` is absent or false, raw prompts and raw responses are not persisted.
- Raw prompts and raw responses are never sent as live Deep Debug messages.
- No automatic retention or pruning is added for Deep Debug records.
- Full chain-of-thought is never requested, generated for display, or exposed.

### 2.4 Deep Debug

- `DEEP_DEBUG` is an environment variable, not a profile field.
- Missing `DEEP_DEBUG` means disabled.
- Live Deep Debug is available only to authenticated commanders. The server performs the authorization check.
- Viewer requests must not receive agent names, protocol names, tool names, decisions, or other protected Deep Debug content.
- Messages are generated deterministically from structured event details and fixed message templates. They must not trigger an LLM call.
- Deep Debug messages show the path in real time, including receipt by the Main Agent, validated intent or decision, routing to another agent, tool or step result, return to the Main Agent, holds, retries, failures, and final outcome when those events occur.
- Each trace update is sent as a separate message. The normal status message is not repeatedly edited for Deep Debug.
- The rendered Deep Debug message text is not stored. Its structured source details are stored.
- Telegram and commander CLI receive the same ordered Deep Debug events.

### 2.5 Streaming removal and status-message lifecycle

- Word-by-word and token-by-token streaming are not implemented.
- `POST /Msg/Stream` is removed from code, tests, and documentation.
- Every user message immediately receives one localized status message.
- English status: `The model is thinking...`
- Hebrew status: `המודל חושב...`
- For a synchronous conversation or question, the status message is edited once to the final answer.
- For an asynchronous report or request, the status message is edited once to an ACK containing the `Task ID` as soon as queue admission succeeds.
- The final asynchronous result continues to arrive later through the existing notification path as a separate message.
- A failure before `Task ID` creation edits the status message to the localized error.
- If the final text is longer than Telegram's 4096-character limit, the status message is edited to the first chunk and later chunks are sent as new messages in order.
- If editing fails, the final answer is sent as a new message and the stale status message is deleted best-effort.
- A failure to delete the stale status message must not hide or fail the final response.

### 2.6 Telegram and CLI parity

- The CLI is a one-to-one behavioral simulator of the Telegram user and commander experience.
- Every status, edit, ACK, `Task ID`, final result, error, hold, notification, translation, split message, and Deep Debug event must appear and behave equivalently in Telegram and CLI.
- Presentation behavior must be implemented through shared contracts and formatting code, not duplicated branches with separate strings.
- CLI command names and Telegram command names remain stable in English, including `/holds` and `/profile`.
- Command descriptions, help text, and responses use the profile language.

### 2.7 Localization and centralized user-interface messages

- Add a `messages` package containing `en.py`, `he.py`, and `model_messages.py`.
- Every profile must define `DEFAULT_LANGUAGE` as exactly `en` or `he`.
- Missing or invalid `DEFAULT_LANGUAGE` fails startup.
- The server selects the fixed UI language once during profile loading. It does not call an external model to detect language.
- Fixed UI text always uses `DEFAULT_LANGUAGE` for the lifetime of the server.
- Natural model-generated responses may follow the user's language. They are not forced to `DEFAULT_LANGUAGE`.
- `en.py` and `he.py` contain all fixed user-facing UI messages.
- `model_messages.py` contains only user-facing formulation templates and instructions for text that a model should phrase naturally.
- Internal routing, extraction, validation, and other technical prompts remain next to their logic.
- Internal log messages, identifiers, error codes, HTTP behavior, and integration fields remain stable in English.
- API human-readable error messages use `DEFAULT_LANGUAGE`.
- API `error_code` values and HTTP status codes remain stable and language-independent.
- Startup validates that English and Hebrew catalogs contain identical keys.
- Startup validates that matching templates use identical placeholder names.
- No local user-facing hardcoded string may remain in API, bot, CLI, orchestration fallback, hold, or notification code after migration.

## 3. Explicit Non-Goals

The current implementation must not:

- enable the merged message planner;
- merge risk assessment and protocol selection;
- merge insight generation and judgment;
- move judgment to Insights Agent yet;
- enable a policy-aware worker pool;
- parallelize event processing or protocol steps;
- replace CrewAI's tool loop with a deterministic tool plan;
- activate `STAGE_MODEL_POLICIES`;
- add provider-level retries;
- add streaming under another endpoint name;
- replace SQLite or introduce a broker;
- weaken authorization, disclosure filtering, validation, ordering, or side-effect rules;
- add automatic log retention or deletion.

The deferred items above belong only in `docs/Next_Plan.md`.

## 4. Target Architecture

### 4.1 Shared message layer

The `messages` package will provide one typed lookup surface:

```text
profile DEFAULT_LANGUAGE
          |
          v
messages catalog loader ---- validates keys and placeholders at startup
          |
          +---- API error and response formatting
          +---- Telegram presentation
          +---- CLI presentation
          +---- holds and notifications
          +---- deterministic Deep Debug rendering
          +---- user-facing model formulation templates
```

Call sites pass semantic values such as `task_id`, `reason`, `protocol_name`, or `event_id`. They do not concatenate localized sentences.

### 4.2 Diagnostic data and live delivery

```text
structured runtime event
          |
          +---- SQLite log_entries (structured details, trace_id)
          +---- local structured log / OTLP
          +---- Deep Debug trace feed when enabled
                        |
                        +---- commander authorization
                        +---- deterministic localized renderer
                        +---- Telegram commander client
                        +---- commander CLI client
```

The persisted record contains fields, not the rendered Deep Debug sentence. The client requests trace entries by the trace ID it generated before sending the original message.

### 4.3 Request presentation lifecycle

```text
client sends message with trace ID
          |
          +---- immediately show localized thinking status
          |
          +---- run POST /Msg while optional commander trace polling runs
                    |
                    +---- synchronous: edit status -> final/error
                    |
                    +---- asynchronous: edit status -> ACK + Task ID
                                                  |
                                                  +---- later notification -> final result
```

## 5. Stage 0: Freeze Baseline and Create the Change Inventory

### Objective

Record the exact pre-change behavior and enumerate every affected string, route, model boundary, and client presentation path before editing runtime behavior.

### File work

- `SPEED.MD`: use the existing findings as the diagnostic baseline; do not rewrite historical measurements.
- `docs/progress.md`: append a `SPEED_PLAN Stage 0` entry when complete.
- `docs/work_plan.md`: append a pointer to this implementation plan without rewriting historical entries.
- `api/`, `bot/`, `tools/`, `orchestrator/`, `history/`, `auth/`, and `protocols/`: inventory user-facing literals with `rg`.
- `agents/runtime.py`, `orchestrator/reasoning.py`, and `history/query.py`: inventory every `Agent.process` and CrewAI provider boundary.
- `tests/`: inventory assertions that depend on exact English UI text.

### Execution steps

1. Capture `git status --short` and preserve all unrelated user changes.
2. Record the installed CrewAI and `python-telegram-bot` versions.
3. Run or reuse the existing offline mocked latency baseline.
4. Export representative trace records for each primary route without raw secrets.
5. Create a user-facing string inventory grouped by API, Telegram, CLI, holds, notifications, fallback responses, and model-formulation templates.
6. Create a provider-call inventory grouped by stage and agent.
7. Identify all tests that monkeypatch `crewai.LLM`, `Agent`, logging handlers, Telegram send/edit methods, or profile contracts.

### Success gate

- Every affected runtime boundary has an owner file and a test location.
- No user-facing literal category is unaccounted for.
- Baseline provider-call counts and latency are recorded before optimization.
- No production behavior has changed.

## 6. Stage 1: Add Localization Contracts and Catalog Validation

### Objective

Create the central language infrastructure before moving any existing strings.

### File work

- `messages/__init__.py`: expose the supported-language enum or literal, catalog resolver, formatter, and validation entry point.
- `messages/en.py`: define the complete English catalog as structured immutable data.
- `messages/he.py`: define the matching Hebrew catalog.
- `messages/model_messages.py`: define only user-facing model-formulation templates and instructions.
- `messages/catalog.py` or an equivalently focused module: implement typed lookup, formatting, key comparison, and placeholder extraction.
- `profiles/contracts.py`: add required `DEFAULT_LANGUAGE` to required profile fields and `LoadedProfile`.
- `profiles/loader.py`: validate `en` or `he`, load the catalog, and fail startup on catalog mismatch.
- `profiles/demo.py` and `profiles/template.py`: add explicit `DEFAULT_LANGUAGE` values selected for those deployments.
- Test-only profile fixtures under `tests/`: add explicit language values.
- `tests/test_profile_loader.py` and new `tests/test_messages.py`: validate supported values, missing fields, key parity, placeholder parity, lookup, and formatting failure behavior.

### Implementation rules

1. Catalog keys are English identifiers.
2. Catalog values are immutable strings or explicitly typed templates.
3. Placeholder validation uses parsed format fields rather than string heuristics.
4. Unexpected formatting arguments and missing formatting arguments fail clearly in development and tests.
5. Internal exception types and error codes remain English.
6. Catalog resolution happens once per loaded profile and is passed through dependencies; call sites do not read environment variables.
7. Hebrew text exists only in `messages/he.py` and Hebrew-specific test expectations or fixtures.

### Success gate

- A profile missing `DEFAULT_LANGUAGE` fails validation.
- An unsupported language fails validation.
- Missing keys or mismatched placeholders fail before the server starts.
- English and Hebrew lookups return the same semantic fields.
- No existing user-facing string has been migrated yet unless required to prove the interface.

## 7. Stage 2: Migrate All Fixed User-Facing Messages

### Objective

Remove fixed user-facing prose from local modules and route it through the shared catalog.

### File work by area

#### API

- `api/request_boundary.py`: separate stable `error_code` from localized human-readable `message`; localize all exposed error prose.
- `api/routes.py`: localize ACKs, clarifications, fallback answers, system responses, protocol-management responses, and validation messages that reach users.
- `api/app.py`: pass the selected message catalog into API context.

#### Bot

- `bot/interactions.py`: replace command help, hold prompts, notification formatting, failure formatting, and refusal messages with catalog keys.
- `bot/background_services.py`: consume already-formatted semantic messages or the shared formatter; do not own prose.
- `bot/app.py`: localize command responses and presentation failures.
- `bot/transports.py`: keep transport exceptions technical internally; expose localized messages only through the presentation layer.

#### CLI

- `tools/terminal_support.py`: centralize rendering of prompts, mode labels, waiting messages, notifications, errors, and goodbye text.
- `tools/terminal_client_commander.py` and `tools/terminal_client_viewer.py`: remove local UI literals and use the same presentation events as Telegram.
- Keep command tokens such as `/holds` and `/profile` unchanged.

#### Orchestration and history

- `orchestrator/reasoning.py`: move only user-facing formulation templates and model instructions to `messages/model_messages.py`; retain routing/extraction/validation prompts locally.
- `orchestrator/flows.py`: localize user-visible fallback, hold, and status text without changing decision logic.
- `orchestrator/holds.py`: localize prompts and user-visible hold failures while keeping hold kinds and persisted fields stable in English.
- `orchestrator/capabilities.py`: keep metadata contracts in English; use `model_messages.py` when asking the model for natural user-facing prose.
- `history/query.py` and `history/field_catalog.py`: keep field identifiers and meanings in English for model grounding; localize only deterministic presentation labels exposed directly to users.
- `protocols/executor.py`: keep internal failure causes structured; localize only the final exposed rendering.

### Execution steps

1. Migrate API messages and update API contract tests.
2. Migrate hold and notification messages.
3. Migrate Telegram messages.
4. Migrate CLI messages.
5. Migrate orchestration and history fallback messages.
6. Move user-facing model formulation templates to `model_messages.py`.
7. Run `rg` using the Stage 0 inventory to prove no scoped literal remains.
8. Preserve log messages, identifiers, prompts that are explicitly internal, and machine-readable values in English.

### Success gate

- Every fixed message visible to a user is selected by `DEFAULT_LANGUAGE`.
- API `error_code` and HTTP status remain identical across languages.
- Model-generated natural responses are not forced to the default UI language.
- Routing, extraction, and validation prompts remain local.
- Commander/viewer authorization and disclosure behavior is unchanged.

## 8. Stage 3: Build the Shared Status and Edit Lifecycle

### Objective

Implement one presentation contract used by both Telegram and CLI for thinking status, one edit, long-message continuation, and edit-failure fallback.

### File work

- `bot/contracts.py`: add a transport-neutral status-message handle and presentation operations for create, edit, send continuation, and delete.
- `bot/transports.py`: implement Telegram send, edit, delete, and ordered continuation behavior using `python-telegram-bot`.
- `bot/app.py`: create the status before every free-text request and resolve it according to synchronous, asynchronous, or error result.
- `bot/interactions.py`: build semantic final/ACK/error presentation payloads from shared message keys.
- `tools/terminal_support.py`: implement the CLI status handle and edit-equivalent presentation.
- `tools/terminal_client_commander.py` and `tools/terminal_client_viewer.py`: use the same lifecycle without duplicating response decisions.
- `tests/test_bot_transports.py`, `tests/test_bot_app.py`, terminal-client tests, and shared presentation tests.

### Execution steps

1. Define the shared semantic result types: synchronous final, asynchronous ACK, pre-admission error, and later notification.
2. Add `send_status` returning a handle.
3. Add `edit_status` with one-call enforcement.
4. Split final content before editing. Edit with the first chunk and send remaining chunks in order.
5. On edit failure, send all final chunks as new messages.
6. After a successful fallback send, delete the stale status best-effort.
7. Ensure delete failure is logged but does not alter the successful user response.
8. For asynchronous admission, edit status to localized ACK plus `Task ID`; never edit it again for the final job result.
9. Deliver later results through the existing notification flow.
10. Implement equivalent CLI transcript events. Interactive terminals may redraw; captured/non-interactive output must still expose an unambiguous edit event for exact tests.

### Success gate

- Every free-text Telegram and CLI request displays status immediately.
- Synchronous success or error causes exactly one edit attempt.
- Asynchronous admission causes exactly one edit to ACK and preserves later notification delivery.
- Long responses preserve all text and ordering.
- Edit failure delivers the answer and attempts cleanup.
- Telegram and CLI scenario tests assert identical semantic event sequences.

## 9. Stage 4: Remove the Misleading Streaming Surface

### Objective

Delete the endpoint that waits for full completion before emitting SSE and remove streaming from public documentation.

### File work

- `api/routes.py`: remove `POST /Msg/Stream` and its generator.
- Remove imports used only by that endpoint, including Flask streaming helpers when no longer needed.
- `profiles/contracts.py` and related optimization contracts: remove or deprecate `streaming_enabled` only if no other runtime consumer requires it. Prefer an additive compatibility parse that ignores the old setting if removing it would break old profiles unexpectedly.
- `profiles/demo.py` and `profiles/template.py`: remove misleading streaming configuration.
- `README.md`, `docs/api_spec.md`, `docs/file_catalog.md`, and relevant operator documentation: remove streaming claims and document status-plus-edit behavior.
- Delete or rewrite SSE tests to assert route absence and the new presentation lifecycle.

### Execution steps

1. Prove no current bot or CLI consumer calls `/Msg/Stream`.
2. Remove the route.
3. Decide compatibility handling for the old profile field without activating any behavior.
4. Remove stale documentation and examples.
5. Add a route test proving `/Msg/Stream` is unavailable.

### Success gate

- `/Msg/Stream` is absent from the route map.
- No documentation advertises word-by-word streaming.
- Telegram and CLI status editing covers the waiting experience.
- Existing `/Msg` behavior and contracts remain intact apart from localization.

## 10. Stage 5: Persist Complete Structured Telemetry

### Objective

Make stage and model timing reconstructable by trace ID in SQLite while preserving structured local logging and OTLP behavior.

### File work

- `tools/observability.py`: revise durable-log filtering so approved stage/model telemetry records are persisted; keep a precise allowlist rather than persisting arbitrary noisy third-party records.
- `persistence/sqlite_store.py`: reuse `write_log_entry` and `fetch_log_entries`; add cursor-based trace reads and a commit notification condition if needed for live polling.
- `persistence/contracts.py`: add the minimal cursor/wait methods required by the trace feed.
- `persistence/schema.py`: add indexes only if measured query plans require them. Existing `idx_log_entries_trace_id` should be evaluated before adding anything.
- `agents/runtime.py`: add structured outer invocation fields consistently.
- `orchestrator/event_queue.py`, `api/app.py`, and existing stage call sites: confirm trace propagation and telemetry classification.
- `tests/test_observability.py` or existing observability tests, plus persistence tests.

### Data requirements

Persist at least:

- trace ID;
- event name and stage;
- agent name when applicable;
- provider and model;
- attempt number;
- success/error status and termination reason;
- monotonic duration and latency milliseconds;
- timeout value;
- input, output, cache, and total tokens when available;
- queue wait and execution timing;
- tool name and result status without adding user-facing prose;
- validation/repair/retry classification.

### Raw model I/O rules

1. Read `DEEP_DEBUG` once at startup through a strict boolean parser.
2. Persist raw prompt and response fields only when enabled.
3. Do not include raw prompt/response in live trace messages.
4. Do not prune these records automatically.
5. Never log API keys or authorization headers.

### Success gate

- `fetch_log_entries(trace_id)` reconstructs all business decisions plus stage and model timings.
- Raw model I/O is absent when Deep Debug is off and present when on.
- No rendered Deep Debug sentence is stored.
- SQLite overhead is measured and reported; the mocked benchmark regression remains below the agreed quality threshold, with any deviation documented for user review.

## 11. Stage 6: Add Exact CrewAI Provider-Call Telemetry

### Objective

Observe every real provider request made inside a CrewAI kickoff, not only the outer `Agent.process` call.

### File work

- `agents/runtime.py`: install the supported CrewAI/LiteLLM event hooks or callbacks once at startup; correlate inner calls with the existing trace and stage context.
- `tools/observability.py`: normalize provider request start/end/error events into the approved schema.
- `agents/contracts.py`: extend `ProviderCapabilities` only where necessary for usage and event support.
- `api/app.py`: initialize hooks before warmup so warmup calls are also measured.
- `tests/test_agent_runtime.py`: simulate multi-call CrewAI tool loops and verify one record per provider request.

### Implementation rules

1. Use the installed CrewAI version's public event mechanism where available.
2. Do not monkeypatch generic HTTP libraries globally.
3. Preserve ContextVar trace and stage values across CrewAI worker threads or callbacks explicitly.
4. Count every request attempt separately.
5. Record usage only from provider metadata; do not estimate tokens for the user-visible trace.
6. When usage is unavailable, emit an explicit unavailable value rather than zero.
7. User-visible LLM Deep Debug rendering is limited to Provider/Model, latency in milliseconds, and token counts.
8. Additional request IDs, finish reasons, cache metadata, error classes, and callback details remain logs-only.

### Success gate

- A mocked CrewAI tool loop that makes four provider calls produces four correlated provider records.
- Provider totals equal the sum of per-call usage where usage is available.
- No extra provider call is made for telemetry.
- Telemetry failure cannot corrupt a successful business response; mandatory startup hook-installation failure is explicit and tested.

## 12. Stage 7: Implement Commander-Only Live Deep Debug

### Objective

Deliver deterministic, localized, real-time trace messages to the originating commander in Telegram and CLI without storing rendered messages.

### File work

- `tools/observability.py`: expose a deterministic event-to-semantic-debug-entry mapping; do not place localized prose in logging call sites.
- `messages/en.py` and `messages/he.py`: add matching Deep Debug templates.
- `persistence/contracts.py` and `persistence/sqlite_store.py`: add cursor-based trace reads and long-poll wakeup after log commit.
- `api/routes.py`: add a commander-only trace polling endpoint with bounded `wait_seconds`, trace ID validation, and cursor ordering.
- `auth/permissions.py`: add or reuse a commander-only requested operation for viewing live trace internals. Do not add it to `ViewerAllowedAction`.
- `bot/transports.py`: add trace polling to the API client.
- `bot/app.py`: start trace polling concurrently only for an originating commander when Deep Debug is enabled.
- `tools/terminal_support.py` and commander CLI: consume the same trace feed and render the same message sequence.
- Viewer CLI: never poll or display the trace feed.
- Tests across permissions, API, persistence, bot, and CLI.

### Trace rendering rules

1. Render from structured event fields using catalog keys.
2. Never call an LLM for rendering.
3. Never render raw prompts, raw model responses, API keys, headers, or chain-of-thought.
4. Render only information already authorized for commanders.
5. Preserve committed log-entry order using the SQLite log-entry ID cursor.
6. Wake long polling only after the log record commits.
7. Bound waits and reconnect on transport failure using the existing client backoff behavior.
8. Stop polling when the synchronous request finishes, the async job reaches a terminal outcome, the client cancels, or the server returns an authorization/error response.

### Required event coverage

- request received by the API and Main Agent;
- intent classification;
- event extraction and missing fields;
- risk assessment;
- protocol selection;
- hold creation and resolution;
- specialist/agent routing;
- step start, retry, failure, waiting-for-data, and result;
- tool start/result/block;
- LLM Provider/Model, latency milliseconds, and token counts;
- insight, judgment, and final outcome;
- queue admission, wait, and execution where applicable.

### Success gate

- A commander sees ordered live entries in Telegram and CLI.
- A viewer receives authorization denial and no protected trace data.
- The stored record contains structured details but not rendered messages.
- Deep Debug off produces no live trace traffic and stores no raw model I/O.
- Deep Debug adds no LLM calls.

## 13. Stage 8: Add Required Invocation Configuration

### Objective

Enforce the user-approved iteration and provider-network limits without changing existing application retry behavior.

### File work

- `profiles/contracts.py`: add required `MAX_ITER` and `MODEL_TIMEOUT_SECONDS` fields.
- `profiles/loader.py`: validate `MAX_ITER` as a bounded positive integer and timeout as a positive finite value; use exact approved values in shipped profiles.
- `profiles/demo.py` and `profiles/template.py`: set `MAX_ITER = 8` and `MODEL_TIMEOUT_SECONDS = 30`.
- Every test profile fixture: declare both required fields explicitly.
- `profiles/contracts.py` `LoadedProfile`: carry values into runtime construction.
- `api/app.py` and agent assembly: pass the loaded limits into agent runtime configuration.
- `agents/runtime.py`: pass `max_iter=8` to CrewAI Agent and provider `timeout=30` to CrewAI LLM/provider options.
- Explicitly set provider retry behavior to disabled if the provider library otherwise retries implicitly and a supported option exists. Do not alter application repair/retry loops.
- `tests/test_profile_loader.py`, `tests/test_agent_runtime.py`, and executor tests.

### Execution steps

1. Add profile validation and update all repository profiles/fixtures.
2. Add runtime configuration with no hidden defaults.
3. Assert CrewAI Agent receives the exact `max_iter` value.
4. Assert the provider client receives the exact timeout value.
5. Assert provider automatic retry is disabled.
6. Assert protocol `RETRY_COUNT` behavior remains three total attempts in current profiles.
7. Test timeout classification and bounded user-facing error behavior.

### Success gate

- Missing fields fail profile startup.
- Every CrewAI agent has explicit `max_iter=8`.
- Every provider call has a 30-second network timeout.
- No provider-layer retry occurs.
- Existing eligible protocol-step retry behavior is unchanged.

## 14. Stage 9: Warm CrewAI and Verify Every Unique Model

### Objective

Move cold startup cost out of the first user request and fail fast if any configured model cannot serve requests.

### File work

- `agents/runtime.py`: add an explicit public runtime-initialization function that imports CrewAI, installs hooks, suppresses unwanted console output, builds safe reusable provider objects, and performs minimal verification calls.
- `agents/__init__.py`: export only the intended startup function.
- `api/app.py`: run initialization after profile and credentials are loaded but before the queue, scheduler, or HTTP server accepts work.
- `profiles/loader.py` or agent assembly: enumerate unique Provider/Model configurations without exposing API keys.
- `tests/test_agent_runtime.py` and `tests/test_api_app.py`: warmup deduplication, fail-fast, timeout, and startup-order tests.

### Warmup rules

1. Deduplicate by the full non-secret provider/model configuration required to distinguish endpoints and behavior.
2. Make one minimal, deterministic, tool-free request per unique combination.
3. Use a very small output budget and validate a simple expected response shape.
4. Use the 30-second provider timeout and no provider retry.
5. Record warmup telemetry under a startup trace and `warmup` stage.
6. Do not expose the API key in cache keys, logs, errors, or diagnostics.
7. Abort startup on any failure and close resources already opened.
8. Do not start queue workers or the summary scheduler before warmup succeeds.

### Success gate

- Cold CrewAI import no longer occurs during the first user request.
- Two agents using the same Provider/Model cause one warmup request.
- Invalid credentials, invalid models, unavailable providers, malformed responses, and timeout all fail startup.
- No server socket accepts traffic before successful warmup.

## 15. Stage 10: Add Safe LLM Reuse

### Objective

Avoid rebuilding expensive LLM objects only for provider implementations explicitly proven safe to share.

### File work

- `agents/contracts.py`: add an explicit `thread_safe` or equivalent LLM-reuse capability to `ProviderCapabilities`.
- `agents/runtime.py`: implement a bounded, readable cache and isolated non-cache path.
- Provider capability tests and multithreaded runtime tests.

### Cache rules

1. Reuse is opt-in. The default capability is false.
2. A provider is marked safe only after source/documentation review and concurrent tests.
3. Cache keys include every immutable option that can affect behavior: provider endpoint identity, model, output-token budget, reasoning effort, response-format mode, timeout, and other provider options.
4. Secrets may participate through a non-reversible internal identity if necessary but are never rendered or logged.
5. Request prompts, conversation turns, trace IDs, callbacks with request state, tool allowlists, and response objects are never cached.
6. Structured and unstructured invocations do not share an object when their provider configuration differs.
7. Cache access is lock-protected, but model calls are not globally serialized unless the provider contract requires it.
8. Cache lifetime is process lifetime; restart clears it.
9. A provider not declared safe uses the existing isolated construction path.

### Success gate

- Reused calls preserve response quality, prompts, schemas, and tool isolation.
- Concurrent calls with distinct trace IDs, conversations, response schemas, and tool allowlists do not leak state.
- Unsafe/unknown providers never hit the reuse cache.
- Warm call setup time improves measurably for an approved provider.

## 16. Stage 11: Documentation and Operator Experience

### Objective

Make startup, configuration, diagnostics, language behavior, and client behavior unambiguous for developers and operators.

### File work

- `README.md`: update required profile fields, environment variables, startup warmup behavior, fail-fast expectations, Telegram/CLI status lifecycle, and removal of streaming.
- `docs/api_spec.md`: remove `/Msg/Stream`; document stable error codes, localized messages, trace polling authorization, and ACK behavior.
- `docs/profile_authoring.md` or the applicable profile documentation: document `DEFAULT_LANGUAGE`, `MAX_ITER`, and `MODEL_TIMEOUT_SECONDS`.
- `docs/agent_authoring.md`: document provider reuse capability and the prohibition on cached request state.
- `docs/allowed_calls.md`: document the commander-only Deep Debug polling operation.
- `docs/file_catalog.md`: add the `messages` package, `SPEED.MD`, and `SPEED_PLAN.MD`; update the overwritten future-plan description.
- `docs/work_plan.md`: append a reference to the current and deferred plans.
- `docs/progress.md`: append one completion record after every stage, including deviations and evidence.
- `.env` documentation or example: add `DEEP_DEBUG=false` without committing secrets.

### Success gate

- A new operator can start the server with all required settings.
- Documentation clearly warns that startup makes one real call per unique model.
- Documentation clearly states that Deep Debug can persist raw prompts/responses indefinitely when enabled.
- No documentation advertises streaming.
- CLI is documented as the one-to-one Telegram simulator.

## 17. Stage 12: Update GitHub CI for Read-Only Validation

### Objective

Update the existing GitHub Actions CI workflow so every new contract and test is exercised automatically without granting or performing repository or external-system write operations.

### File work

- `.github/workflows/ci.yml`: add the new focused test modules to the appropriate mission groups, add a complete offline-suite coverage guard or final complete-suite job, and define every required `TEST_*` environment variable explicitly.
- Add checks for catalog parity, profile-required fields, removal of `/Msg/Stream`, Deep Debug authorization, provider instrumentation, warmup behavior through fakes, LLM cache isolation, Telegram/CLI parity, and documentation/file-catalog consistency.
- Keep live provider calls, paid evaluations, Telegram network calls, deployment, and mutation outside CI.

### Read-only safety rules

1. Set workflow or job permissions to `contents: read` and do not grant write scopes.
2. Do not use `pull_request_target`.
3. Do not commit, push, create or update pull requests, publish releases, deploy, upload to production services, or modify repository settings.
4. Do not use real provider, Telegram, or production credentials.
5. Use explicit fake `TEST_*` credentials and mocked network boundaries.
6. Do not run startup warmup against a live provider in CI; test warmup through deterministic fakes.
7. Do not write to the checked-out repository as part of validation. Temporary test data must use runner temporary directories.
8. Artifact upload is omitted unless the user separately approves it, because it creates external state in GitHub.

### Sequential execution

1. Inventory every `tests/test_*.py` file after implementation.
2. Ensure every test module is included exactly once in the mission grouping or in an explicitly documented final suite command.
3. Add all required test environment variables.
4. Add `git diff --check`, catalog validation, and file-catalog validation commands.
5. Declare read-only workflow permissions.
6. Validate the YAML locally without triggering or dispatching a GitHub workflow.
7. Run the same commands locally before the final verification stage.

### Success gate

- CI has only read permissions and contains no external mutation step.
- CI makes no paid or live network call.
- Every offline test module is covered.
- All required `TEST_*` variables are explicit.
- CI commands pass locally before the workflow file is considered complete.

## 18. Stage 13: Verification and Quality Gates

### Objective

Prove correctness, compatibility, security, and performance before declaring the current plan complete.

### Focused test groups

1. Message catalogs: language validation, key parity, placeholder parity, formatting, and missing-key startup failure.
2. API localization: stable `error_code`/HTTP status with localized message text.
3. Telegram presentation: initial status, one edit, ACK, error, long responses, edit failure, delete fallback, and later notifications.
4. CLI parity: identical semantic presentation sequences for viewer and commander scenarios.
5. Deep Debug authorization: commander allowed, viewer denied, disabled mode silent.
6. Deep Debug storage: structured details stored, rendered text absent, raw model I/O gated by environment.
7. Trace ordering and long polling: commit-before-wake, reconnect, cancellation, and terminal completion.
8. Provider telemetry: one record per actual provider request with provider/model, latency, and tokens.
9. Profile invocation settings: required fields, exact `max_iter`, exact timeout, no provider retry, unchanged application retry.
10. Warmup: unique-model deduplication, real-call adapter behavior, startup order, fail-fast, and cleanup.
11. LLM cache: capability opt-in, key isolation, multithreaded calls, conversation isolation, schema isolation, and tool-allowlist isolation.
12. Route regression: conversation, question, history, current state, report, request, approval, missing-event-data continuation, notification, and viewer disclosure.

### Full verification sequence

1. Run formatting/static checks already used by the repository.
2. Run focused tests after each stage.
3. Run `git diff --check`.
4. Run the complete offline test suite with explicit `TEST_*` variables.
5. Run the mocked latency benchmark and compare it with Stage 0.
6. Run concurrency tests for cached LLM objects and Deep Debug trace delivery.
7. Inspect `EXPLAIN QUERY PLAN` for cursor-based trace retrieval.
8. Manually inspect one English profile and one Hebrew profile in commander and viewer CLI.
9. Perform an operator-approved live smoke test because startup warmup necessarily calls configured providers; do not initiate paid live testing without the required credentials and authority.
10. Record all results in `docs/progress.md`.

### Final success criteria

- Focused tests pass.
- The complete offline suite passes with no authorization, disclosure, persistence, routing, history, or side-effect regression.
- First-request CrewAI import cost is removed.
- Warmup fails startup for an invalid or unavailable configured model.
- Approved thread-safe providers reuse LLM objects without state leakage.
- Provider calls are bounded by 30 seconds and CrewAI loops by `max_iter=8`.
- No provider automatic retry is added.
- A trace reconstructs every actual provider request and stage.
- Deep Debug is deterministic, commander-only, localized, real-time, and makes no LLM call.
- Raw model I/O is persisted only when Deep Debug is enabled.
- Telegram and CLI present equivalent status, edit, ACK, error, notification, and trace behavior.
- `/Msg/Stream` is absent.
- Every fixed user-facing string comes from the selected catalog.
- Deferred behavior remains disabled and documented in `docs/Next_Plan.md`.

## 19. Sequential Commit and Progress Discipline

Implement stages strictly in numeric order. After each stage:

1. Run its focused tests.
2. Review the diff for unrelated changes.
3. Append a dated entry to `docs/progress.md` containing status, files changed, deviations, tests, benchmark results, and remaining risks.
4. Do not mark the stage complete if a required test or success criterion is unresolved.
5. Do not begin a deferred optimization from `docs/Next_Plan.md`.

The implementation may be rolled back by reverting the current stage while retaining additive data. `DEEP_DEBUG=false` disables live trace delivery and raw model-I/O persistence. Existing legacy orchestration, serial queue behavior, and separate decision stages remain the behavioral rollback baseline throughout this plan.
