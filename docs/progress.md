# Progress Log

Append-only running record of what was actually built for each subtask in
`docs/work_plan.md`, versus what the work plan originally specified. One
entry per subtask, added in completion order (per `instructions.md` §6).
Never edit or remove a prior entry — if a subtask is revisited later, add
a new entry describing what changed instead.

## Mission status

| Mission | Section | Status |
|---|---|---|
| 1 | Foundations (1.1–1.10) | Done |
| 2 | Data Layer (2.1–2.13) | Done |
| 3 | Agent Framework (3.1–3.12) | Done |
| 4 | Protocol Engine (4.1–4.8) | Done |
| 5 | History System (5.1–5.10) | Done |
| 6 | Main Agent Orchestration (6.1–6.15) | Done |
| 7 | API Layer (7.1–7.12) | Done |
| 8 | Telegram Frontend (8.1–8.14, +8.15) | Done, end to end — `bot/` talks to a real running `api/*` process over real HTTP (`bot.http_api_client.HttpApiClient`), not a stub |
| 9 | Integration and Hardening (9.1–9.22) | Done |

Entry format:

```
### <subtask id> — <subtask name>
- **Status:** done | partially done | blocked (+ reason)
- **Deviations:** what differs from work_plan.md — implemented
  differently, deferred, stubbed, or simplified — and why. "None" if none.
```

---

### 1.1 — Set up the repository and module skeleton
- **Status:** done
- **Deviations:** The "automated check that fails the build" is a pytest
  test (`tests/test_architecture.py`) run via a new GitHub Actions
  workflow (`.github/workflows/ci.yml`) — an implementation choice
  confirmed with the user, not specified by work_plan.md itself. The
  skeleton also includes `auth/`, `fixtures/`, and `tests/` alongside the
  packages §1.1 names explicitly (`persistence`, `agents`, `protocols`,
  `history`, `orchestrator`, `api`, `bot`, `profiles`, `tools`) — `auth/`
  per the §1.9 decision below, `fixtures/`/`tests/` because they're on
  instructions.md's approved directory list and needed immediately for
  test fixtures.

### 1.2 — Define the domain vocabulary
- **Status:** done
- **Deviations:** None.

### 1.3 — Build the base configuration
- **Status:** done
- **Deviations:** Model identifiers (`main_agent_model`, etc.) are
  placeholder strings (`"gpt-4o"`, `"gpt-4o-mini"`) — no model client
  library is wired up yet, since model routing lands in §3.6. Will need
  revisiting once the Agent Framework exists.

### 1.8 — Establish structured logging and run tracing
- **Status:** partially done
- **Deviations:** Built early (right after 1.1, per the work plan's own
  note that it touches every module) but only the *infrastructure* exists
  — `tools/logging_config.py` (JSON formatter, profile-name stamping) and
  `tools/tracing.py` (trace-ID generation/propagation via contextvar). The
  specific named events §1.8 requires (intent decision, extraction result,
  risk assessment, protocol selection, hold kind, precedent lookup,
  per-step task/result, tool calls including blocked ones and retries,
  insight text and verdict) are not yet logged anywhere, because the
  subsystems that produce them (extraction, risk assessment, the
  executor, ...) don't exist yet. Those call sites land with §5/§6.

### 1.4 — Define the profile structure
- **Status:** done
- **Deviations:** The Agent Framework (§3) and Protocol Engine (§4) don't
  exist yet, so a profile's `AGENTS`/`PROTOCOLS` entries are specified and
  validated **structurally** (attribute presence via `profiles/spec.py`),
  not against the real `Agent`/`Protocol` classes — documented as a
  deliberate seam in `docs/profile_spec.md`. The real classes just need to
  satisfy this shape when §3/§4 land; nothing here should need to change.

### 1.5 — Implement profile loading and selection
- **Status:** partially done
- **Deviations:** Core-agent construction (Main Agent, History Agent,
  Insights Agent) is a documented no-op seam
  (`profiles/loader.py::_construct_core_agents`) returning an empty
  mapping, since the Agent Framework doesn't exist yet. This is a stub,
  not a completed requirement — it needs to be wired to real construction
  once §3 and §6 land. Everything else in §1.5 (module resolution, no
  default profile, freezing, env-var resolution at load time, db
  path/port sourced once) is implemented in full.

### 1.6 — Validate the profile at startup
- **Status:** done
- **Deviations:** Validates agent/protocol references structurally (same
  duck-typed approach as 1.4), not against real classes, for the same
  reason. Also validates that a profile does not itself declare
  `"human_activation"` as an event type — that check technically belongs
  to §2.1's scope but is pulled forward here since `docs/profile_spec.md`
  documents it as part of the loading contract and profiles.validate was
  the natural place to enforce it now.

### 1.7 — Build the runtime settings store
- **Status:** done
- **Deviations:** None.

### 1.9 — Implement the permission model
- **Status:** done
- **Deviations:** Implemented in a new top-level package, `auth/`, which
  was **not** on instructions.md's original approved directory list.
  Explicit user approval was obtained and instructions.md §5.1 was amended
  to add `auth/` before this was built. (work_plan.md's own branch-grouping
  table also names `auth/permissions`, so this matches the work plan; it
  only conflicted with instructions.md's list as originally written.)

### 1.10 — Build the user administration command
- **Status:** done
- **Deviations:** §1.10 requires §2.7 (the persistence interface), which
  is out of §1's scope entirely. Per explicit user decision: the **full**
  §2.7 operation set (events, summaries, held events, user CRUD) is
  declared now in `persistence/interface.py`, but only the user-CRUD
  operations have a real implementation — a SQLite-backed slice of §2.4
  (users table) and §2.9 (connection/WAL/schema-creation plumbing) in
  `persistence/sqlite_backend.py`. Every other operation raises
  `NotImplementedError`, naming the task that will implement it; matching
  structured placeholders (not DDL) live in `persistence/schema.py`. Also
  added `persistence.interface.open_persistence(db_path)`, a factory
  function not explicitly specified by work_plan.md — needed so
  `cli/user_admin.py` could get a concrete backend without importing
  `persistence.sqlite_backend` directly, which the import-graph test
  (§1.1) correctly flagged as a boundary violation on first run.

### 2.1 — Build the event-type registry
- **Status:** done
- **Deviations:** Implemented in a new top-level package, `registries/`,
  not on instructions.md's original approved directory list — matches
  work_plan.md's own branch table (B6); explicit user approval obtained
  and instructions.md §5.1 amended before this was built, same pattern as
  `auth/` in Mission 1. The human-activation append and duplicate-
  declaration rejection §2.1 describes were already implemented in
  `profiles/loader.py`/`profiles/validate.py` during Mission 1 (before
  `registries/` existed as a concept); `registries/event_types.py`
  deliberately wraps that already-correct data rather than
  re-implementing the same logic a second time.

### 2.2 — Build the area registry
- **Status:** done
- **Deviations:** Same package-location note as 2.1. Otherwise a direct,
  undeviating implementation — `registries/areas.py` wraps
  `LoadedProfile.areas` with an `is_valid` query method.

### 2.3 — Design the event schema
- **Status:** done
- **Deviations:** Per-step execution records use a separate `event_steps`
  table (composite PK `(event_id, step_index)`) rather than a JSON column
  on `events` — confirmed with the user; "one record per executed step"
  read literally as a relational row per step. `entities` and
  `precedent_matched_event_ids` are JSON-encoded TEXT columns, since
  SQLite has no native array type — decoded/encoded at the
  `persistence.sqlite_backend` boundary, never visible above it. DDL only
  in this entry — the operations that read/write it land under 2.9 below.

### 2.5 — Preserve raw text
- **Status:** done
- **Deviations:** None. `raw_text` is `NOT NULL`, written once by
  `append_event`; `update_event`'s column whitelist (2.9) deliberately
  excludes it so no code path can overwrite it later.

### 2.6 — Design the summary tables
- **Status:** done
- **Deviations:** Each of the three tables carries `UNIQUE(period_start,
  period_end)` — not explicitly required by 2.6's own wording, but needed
  so `write_summary` (2.9) can upsert per §5.5's "overwrite... rather than
  appending a second one," and it doubles as the §2.8 period-boundary
  index. Confirmed as part of the write_summary design decision.

### 2.8 — Define the indexing strategy
- **Status:** done
- **Deviations:** None. Occurrence-timestamp index and the composite
  `(classification, area)` index on `events`; each summary table's period
  index comes from its `UNIQUE(period_start, period_end)` constraint
  (2.6) rather than a separate `CREATE INDEX`, since a unique constraint
  already is one.

### 2.4 — Design the user table
- **Status:** done (verified, no change)
- **Deviations:** None. Already implemented in Mission 1 to back the
  user-admin CLI; re-checked against §2.4's exact wording now that the
  events table exists alongside it in the same file — one row per user,
  same database file as events, no global/shared user notion. No code
  change was needed.

### 2.7 — Define the persistence interface
- **Status:** done
- **Deviations:** The interface's operation set was already declared in
  full during Mission 1 (pulled forward for §1.10). This mission adds one
  operation beyond §2.7's literal bullet list: `update_event(event_id,
  updates)` — needed because §5.1/§6.11 require mutating an
  already-appended event and §2.7 as literally written has no operation
  for that. Confirmed with the user as completing 2.7's intent rather than
  leaving the gap for §5/§6 to discover. Everything else in §2.7
  (engine-specific statements confined to one module, no SQL/engine
  exceptions above the boundary, `db_path` as a construction argument) was
  already satisfied.

### 2.10 — Build schema migrations
- **Status:** done
- **Deviations:** Version tracking uses SQLite's built-in `PRAGMA
  user_version` rather than a dedicated migrations-log table — satisfies
  "record the applied version inside the database itself" with no extra
  table to keep in sync. Not explicitly specified by work_plan.md; a
  low-ambiguity implementation choice, not treated as needing a separate
  question.

### 2.9 — Implement the SQLite backend
- **Status:** done
- **Deviations:** While implementing `append_event`, found that the 2.3
  schema entry (already logged above) had `occurred_at NOT NULL`, which
  contradicts §6.11's explicit ordering — the event is written with its
  raw text *before* extraction runs, so a Telegram-sourced event has no
  occurrence timestamp yet at `append_event` time. Fixed by making
  `occurred_at` nullable (`occurred_at_is_fallback` stays `NOT NULL
  DEFAULT 0`) before any migration shipped it — not a re-opening of the
  2.3 entry above, noted here since it surfaced during 2.9's work. All of
  2.9's own requirements are implemented as specified: schema knowledge
  confined to this module, `db_path` taken from the profile with no
  default, WAL mode, a single serialized writer thread (stdlib `queue` +
  `concurrent.futures.Future` per job, confirmed with the user as a
  low-ambiguity design), concurrent short-lived read connections. Held-
  event operations still raise `NotImplementedError` — out of scope, owned
  by §6.2/§6.7.

### 2.11 — Write the backend-swap conformance suite
- **Status:** done
- **Deviations:** The held-event failure case 2.11 names ("resolving a
  hold that is not held") currently asserts `NotImplementedError` rather
  than a domain-specific error, since held-event storage isn't built yet
  (§6.2/§6.7). Noted inline in the test itself so it's easy to find and
  update once that lands, rather than silently left wrong.

### 2.12 — Build the seed dataset
- **Status:** done
- **Deviations:** "Text that no classification fits, so the clarification
  path can be driven from fixtures" is satisfied by a record with a final
  classification chosen through a *resolved* clarification hold
  (`clarification_held=True`, `clarification_chosen_classification` set),
  not a record with an empty final classification — since every record
  here is a completed historical record per 2.12's own framing, an
  unresolved/empty classification would contradict "completed." Otherwise
  every bullet implemented directly: partial reports, a contradictory
  pair, a late (`occurred_at < received_at`) report, both risk levels, a
  same-classification/area triple spanning inside and outside a 30-day
  lookback window, one never-resolved prior event, and two
  human-activation records (one approved, one declined at approval).
  Verified against a real backend in `tests/test_seed_dataset.py`, not
  just eyeballed.

### 3.2 — Define the agent descriptor
- **Status:** done
- **Deviations:** The live CrewAI instance is deliberately *not* held on
  the descriptor itself, unlike 3.2's literal "hold one CrewAI instance"
  wording — it lives on the `Agent` instance (§3.1), built lazily on first
  `process()` call rather than at construction. This matters because
  `crewai` isn't installed in this environment (confirmed with the user);
  eager construction in `__init__` would make constructing *any* agent —
  even one never invoked — crash immediately. Lazy construction on the
  instance achieves the same "built once, reused for the life of the run"
  outcome without that failure mode. `AgentDescriptor` itself stays a
  plain, frozen, declarative dataclass.

### 3.3 — Implement the tool-exposure function
- **Status:** done
- **Deviations:** None. `exposed_tools_for` introspects the class for
  `@tool`-decorated methods rather than reading a hand-maintained list, so
  a tool added to a class is exposed automatically.

### 3.4 — Classify tools by side effect
- **Status:** done
- **Deviations:** Beyond requiring `side_effecting` explicitly (§3.4's
  literal ask), the `@tool` decorator also rejects `idempotent` being
  *omitted* when `side_effecting=True` and rejects it being *given* when
  `side_effecting=False` — not just "no default," but "no ambiguous or
  meaningless value either." Confirmed as the natural reading of "meaningful,
  and required, only when side-effecting," not a separate question.

### 3.9 — Implement the unclear-task signal
- **Status:** done
- **Deviations:** §3.1 literally describes `process()` as returning
  "result_text" (implying a bare string), but §3.9 explicitly rules out
  both an exception and "an error string" as the unclear-task signal —
  logically leaving only a structured, tagged return value, since a bare
  string can't be distinct from a normal result without becoming exactly
  the disallowed "error string." `process()` therefore returns a small
  `AgentResult(status, text)`, read as completing §3.1's intent rather
  than contradicting it. The model signals unclear-task via a fixed
  sentinel line in its raw output (`UNCLEAR_TASK: ...`), parsed here —
  **this exact prompt convention is unverified against a live model**
  since crewai isn't installed; flagged for a manual smoke test once real
  API keys are available.

### 3.1 — Define the abstract agent class
- **Status:** done
- **Deviations:** `process()` returns `AgentResult`, not a bare string —
  see 3.9's entry above for why. Otherwise direct: one public entry point,
  model taken as a constructor argument and stored, `name`/`role`/
  `system_prompt` required as class-level attributes (checked at
  construction, failing loudly by naming what's missing), the base class
  holds everything common (descriptor, wrapped tools, invocation path).

### 3.5 — Implement the CrewAI adapter
- **Status:** done (as a seam — see Mission-level note below)
- **Deviations:** `crewai` is not installed in this environment
  (confirmed with the user before planning). The adapter is written
  against the real library's documented API — researched at
  docs.crewai.com on 2026-08-23, describing roughly the v1.13–1.15 series
  (exact PyPI version was ambiguous across sources) — not against a guess.
  Verified from that research: `Agent(role, goal, backstory, llm, tools,
  max_execution_time, ...)`, `Agent.kickoff(text) -> LiteAgentOutput`
  (`.raw` holds the text) runs one agent directly with no Task/Crew
  needed, and custom tools subclass `crewai.tools.BaseTool` with explicit
  `name`/`description`/`_run`. **Not verified against a live install**:
  whether dynamically subclassing `BaseTool` via `type(...)` (rather than
  a normal `class` statement) behaves correctly given `BaseTool` is
  pydantic-based — this is the specific risk flagged for a manual smoke
  test once crewai is actually installed. `_get_crewai()` is the one lazy
  import point; every other function is fully real and fully tested via a
  monkeypatched fake standing in for the module (`tests/test_agent_adapter.py`),
  and the framework-not-ready path is tested for real, since that's this
  environment's actual current state.

### 3.6 — Implement model routing
- **Status:** done (routing mechanics only — no core agents constructed, see below)
- **Deviations:** Model routing itself needed no new code for credential
  resolution: litellm reads provider environment variables straight from
  `os.environ`, and Mission 1's `profiles.loader` already put them there
  at load time. "Construct the three core agents with the models named in
  the base configuration" is **not done in this mission** — the Main
  Agent, History Agent, and Insights Agent classes don't exist yet
  (§5.3/§6.1/§6.9). `profiles/loader.py::_construct_core_agents` stays the
  documented `{}` seam from Mission 1; its comment was updated to point at
  those three future tasks instead of §3, since the Agent Framework now
  exists but has nothing of theirs to construct yet. What *is* verified:
  routing is per-call from `descriptor.model`, with no shared client and
  no global default (`tests/test_agent_adapter.py`'s two-agents-two-models
  test).

### 3.7 — Enforce tool permissions at call time
- **Status:** done
- **Deviations:** None. Enforcement happens per invocation via a
  contextvar `process()` sets for the current call, never bound at
  construction; a blocked attempt returns a refusal string and logs the
  agent, the tool, and the trace ID. "The step" from §3.7's wording isn't
  logged directly — `process()`'s signature (§3.1) doesn't carry a step
  identifier, only `text`/`allowed_tools` — the trace ID is what a caller
  correlates a blocked attempt back to a step with, once the executor
  (§4, later) exists to make that correlation meaningful.

### 3.10 — Handle timeouts and agent errors
- **Status:** done (translation logic; timeout mechanism unverified live)
- **Deviations:** Uses CrewAI's own `max_execution_time` constructor
  parameter as the timeout — confirmed as a real, documented parameter,
  not invented — rather than a separate thread-based watchdog. A generic
  `Exception` from `kickoff()` becomes `AgentModelError`; `TimeoutError`
  becomes `AgentTimeoutError`; output with no `.raw` attribute becomes
  `AgentOutputParseError`. **Not verified live**: whether CrewAI actually
  raises Python's built-in `TimeoutError` on an execution-time breach, or
  some other exception type that would currently fall through to
  `AgentModelError` instead — flagged alongside 3.5's other unverified
  specifics for a manual smoke test once crewai is installed.

### 3.8 — Build the agent registry
- **Status:** done
- **Deviations:** Rejects a duplicate agent name at build time
  (`DuplicateAgentNameError`) — not in §3.8's literal bullets, added as a
  direct integrity check since a silently-overwritten registration would
  be a confusing failure mode later. Otherwise direct: built from exactly
  the core-agents mapping and profile-agents list passed in, no
  self-registration; lookup by name and enumeration; `descriptor_for`
  returns role and tools together from the one descriptor rather than two
  separate calls that could drift.

### 3.11 — Build the reference agent
- **Status:** done
- **Deviations:** `check_status`'s tool name matches Mission 2's seed
  dataset and fixture profile, which already assumed a `"check_status"`
  tool would exist — not a coincidence, kept consistent deliberately.
  `record_action` genuinely appends to an instance list rather than
  returning a canned string, since a canned string can't distinguish one
  call from two — that distinction is exactly what §4.5's retry policy
  will later need to be testable against.

### 3.12 — Document the agent-authoring path
- **Status:** done
- **Deviations:** None. One page, points at `agents/reference.py` rather
  than reproducing its code, states the three failure modes (unmarked
  tool, missing class attribute, agent not constructed in a profile) and
  when each is caught.

### 3.10 — follow-up: tool-construction errors were bypassing translation
- **Status:** done (gap closed; append-only follow-up, not an edit of the entry above)
- **Deviations:** `agents/adapter.py::_build_crewai_tools` had no error
  handling of its own — a failure while dynamically subclassing
  `crewai.tools.BaseTool` via `type()` (the exact unverified-pending-
  real-crewai risk already named in the 3.10 entry above and in the
  Mission 3 re-summary) would have propagated raw out of `invoke()`,
  bypassing the `try/except` around `kickoff()` entirely — violating
  §3.10's "surface each as a distinct outcome" and §1.8's "log every
  retry with the cause that triggered it," since a raw, untyped exception
  carries no `agent_name`/`trace_id` and can't be matched by anything
  catching `AgentInvocationError`. Fixed: each tool's dynamic-class
  construction is now individually wrapped, re-raising as a new
  `AgentToolConstructionError(AgentInvocationError)` naming the specific
  tool that failed (not just "tool construction failed somewhere").
  Scoped to error handling only, as instructed — the `type()`-based
  construction mechanism itself is unchanged and still unverified against
  real pydantic. Two new tests in `tests/test_agent_adapter.py` cover it:
  one fake `BaseTool` that always raises in `__init__`, and one that
  raises only for a specific tool name among several, confirming the
  right tool is named in the error rather than just "something failed."

### 1.3 / 3.5 — follow-up: DEBUG_FLAG-backed raw AI interaction logging
- **Status:** done (gap closed; append-only follow-up)
- **Deviations:** `DEBUG_FLAG` remains a fixed base-configuration value,
  not a fourth live setting. `tools.logging_config.log_ai_interaction`
  prints the complete model payload and raw response only when enabled;
  both the CrewAI adapter and Mission 5 extraction path use it. Raw
  exchanges are never passed to persistence.

### 2.6 / 2.7 / 2.9 / 2.10 — follow-up: summary lookup index and half-open ranges
- **Status:** done (Mission 5 prerequisite completed)
- **Deviations:** Added migration 6 and current-schema `event_index` JSON
  columns for all three summary levels. Migration 6 checks the actual
  columns before altering so it works both after the frozen version-4 DDL
  and on a freshly-created schema that already contains the current
  column. Event queries now use `[start, end)` and summary overlap uses
  `period_start < end AND period_end > start`; the public interface and
  backend conformance coverage were updated together.

### 5.1 — Implement the history-write path
- **Status:** done
- **Deviations:** Implemented as the public `history.interface` service;
  the Mission 6 new-event flow remains responsible for calling the initial
  write before orchestration exists. The extraction write accepts source
  and received time only when a scheduler is supplied, since those values
  are required for the late-event hook and are deliberately not cached in
  memory. Intermediate state writes use a strict allowlist and cannot
  bypass the dedicated step/outcome writers.

### 5.2 — Build extraction
- **Status:** done
- **Deviations:** Model execution is injected through `model_invoker`, so
  the extraction contract, prompt, strict JSON parsing, registry checks,
  timestamp behavior, missing-field reporting, and typed execution error
  are complete without pulling Mission 6's Main Agent forward. Runtime
  invoker wiring remains at the future §6.11 integration point. An
  unresolvable Telegram occurrence time stays `None` and is not falsely
  marked as a fallback.

### 5.3 — Build the History Agent
- **Status:** done
- **Deviations:** The agent exposes zero tools; all database-derived
  context is supplied by history services. This is the strictest reading
  of "read-only tools only" and prevents the model from widening its own
  retrieval. It is constructed as `history_agent` on every profile load;
  Main and Insights remain deferred to their Mission 6 tasks.

### 5.4 — Build the summarization pipeline
- **Status:** done
- **Deviations:** Daily summaries are generated only for closed days that
  contain events; monthly/yearly summaries are generated only where the
  lower level contains records. Empty calendar periods are not materialized.
  Monthly and yearly indexes are deduplicated unions of their children,
  and no upper level re-reads raw events.

### 5.5 — Build the summary scheduler
- **Status:** done
- **Deviations:** Implemented as a reconciliation worker with injectable
  clock and lifecycle hooks. It derives missing/stale work from the
  database on every pass, processes levels bottom-up, and persists via
  idempotent summary upserts. Starting/stopping it from the application is
  intentionally deferred to Mission 7 lifecycle wiring.

### 5.6 — Handle late-arriving events
- **Status:** done
- **Deviations:** `notify_event_written` is a transient wake-up only; no
  correctness state is kept in memory. Daily staleness is derived from
  event receipt versus generation time and cascades through child/parent
  generation timestamps. Mission 6/7 ingestion will call the exposed hook
  when those flows land.

### 5.7 — Build the query interface
- **Status:** done
- **Deviations:** None. `HistoryQueryService` retrieves persisted material
  first, gives only that context to the History Agent, and returns exact
  source attribution alongside the answer with optional time,
  classification, and area filters.

### 5.8 — Implement precedent search
- **Status:** done
- **Deviations:** Uses the same hierarchical range planner as questions,
  so yearly/monthly/daily indexes identify candidate periods and raw reads
  cover only candidates and unsummarized edges. The window is anchored to
  the target occurrence time and reads the live lookback value on every
  call. Resolution is a deterministic outcome mapping, never a model
  judgment.

### 5.9 — Implement range-scoped retrieval
- **Status:** done
- **Deviations:** Uses deterministic greedy coverage in half-open UTC
  intervals, from yearly to monthly to daily to raw boundary spans.
  Sources never overlap, and matched event totals deduplicate by event ID.

### 5.10 — Verify summary fidelity
- **Status:** done
- **Deviations:** The automated three-level seed-dataset test uses a
  deterministic fake History Agent because CrewAI and live credentials
  are unavailable in this environment. It verifies both contradictory
  reports, handling/outcome material, and index identity survive through
  yearly compression. Live-model quality remains the documented manual
  smoke test; all 155 automated tests pass without API keys.
### 4.1 — Define the protocol model
- **Status:** done
- **Deviations:** Found and fixed a real bug while building this:
  `profiles/validate.py::_validate_protocol` compared a tool-name string
  against a set built from `agent.exposed_tools()` — correct against
  Mission 1's duck-typed `FakeAgent`/`_FixtureAgent` (which return plain
  strings) but silently broken against a real `agents.base.Agent`, whose
  `exposed_tools()` returns `tuple[ToolInfo, ...]` (Mission 3). No
  existing profile combined a real `Protocol` with a real `Agent` before
  now, so nothing caught it. Fixed with a shape-tolerant
  `getattr(t, "name", t)` so both shapes work; regression tests added in
  `tests/test_profile_validation.py` using a real `ReferenceAgent`. Also
  added `expected_success_output` to `profiles/spec.py`'s
  `PROTOCOL_REQUIRED_ATTRS` — §4.1 requires this field but Mission 1's
  structural contract never listed it; added a matching non-empty check
  to `_validate_protocol`. `Step` (§1.2/§4.4's contract) is also defined
  here, alongside `Protocol`, since both are core protocol-engine data
  shapes. `CriticalityLevel` is an ordered `IntEnum`, matching
  `auth.permissions.PermissionLevel`'s established pattern for "compare
  to pick a winner" fields.

### 4.2 — Load protocols from the profile
- **Status:** done
- **Deviations:** None beyond what 4.1's entry already covers.
  `profiles.loader` already instantiated every protocol correctly since
  Mission 1; this module is a thin, fixed-for-the-run read wrapper over
  that data (`ProtocolSet.all()`/`.get(name)`), matching the
  `registries.event_types` pattern from Mission 2. No checking of its own
  — depends entirely on §1.6's startup validation having already run.

### 4.3 — Implement profile protocol editing
- **Status:** done
- **Deviations:** A profile's `PROTOCOLS = [...]` is Python source, not
  data. Rather than element-level AST surgery (fragile comma/whitespace
  bookkeeping to preserve surrounding hand-formatting), a write
  regenerates the *entire* `PROTOCOLS` assignment from the currently-
  loaded `Protocol` objects plus the one being added/replaced/removed,
  and splices that in place of the original assignment's exact line span
  (`ast` locates the span; nothing outside it is touched). Confirmed with
  the user as the right tradeoff — far more robust at the cost of not
  preserving custom formatting inside that one block, which becomes
  machine-managed the moment editing exists. Relies on one invariant,
  documented in the module: the file being edited already validated
  successfully (§4.2's precondition), so it already imports `Protocol`/
  `CriticalityLevel` and the regenerated block can reference them
  unqualified. Validation reuses the exact startup checks via a new
  `profiles.loader.validate_single_protocol` wrapper — added so
  `protocols.editor` never has to import `profiles.validate` directly,
  which stays internal to `profiles/` per `docs/allowed_calls.md`.

### 4.4 — Build the protocol executor
- **Status:** done
- **Deviations:** Found and fixed a real bug while testing: `execute_steps`
  called `protocols.retry.execute_step_with_retry` without forwarding a
  `sleep_fn`, so it silently defaulted to the real `time.sleep` — any test
  exercising a multi-attempt failure *through the executor* (rather than
  calling `protocols.retry` directly) was sleeping for real seconds
  (`tests/test_protocol_executor.py` initially took 4.14s for 6 tests;
  0.16s after the fix). Fixed by adding `sleep_fn` as a parameter on
  `execute_steps` and threading it through. Separately, `Agent` is only
  ever used here as a type hint, and `agents.base` isn't an approved entry
  point (only `agents.registry`/`results`/`errors`/`reference` are) — the
  import-graph test correctly flagged this. Fixed with a `TYPE_CHECKING`
  guard, which then required fixing `tests/test_architecture.py` itself:
  its checker used a blind `ast.walk`, which doesn't distinguish a
  type-only, never-executed import from a real one. The checker now prunes
  `if TYPE_CHECKING:` blocks from its walk entirely — a correctness fix to
  the tool, not a loosening of the rule it enforces, since those imports
  create no real runtime coupling. `protocols.retry` also picked up the
  same `TYPE_CHECKING` treatment for the same reason.

### 4.5 — Implement the retry policy
- **Status:** done
- **Deviations:** "Never replay a step whose side-effecting, non-idempotent
  tool already acted" is read conservatively — confirmed with the user:
  since a failed `agent.process()` call gives no visibility into whether a
  non-idempotent tool actually fired before the failure (CrewAI's
  reasoning is opaque, and this can't be verified without a real installed
  crewai anyway per Mission 3's open items), a step naming *any*
  side-effecting, non-idempotent tool among its `allowed_tools` is never
  retried at all once its first attempt fails — treated as "may have
  acted." Backoff is a fixed interval (`backoff_seconds=1.0` default), not
  exponential — §4.5 only asks for "a backoff," not a growth curve, so the
  simplest implementation satisfying the literal requirement was used. The
  attempt limit is read from the settings store on *every attempt*, not
  once per step — the most literal reading of "not cached," and strictly
  more responsive than the spec requires, never less. An unclear-task
  signal with no `task_rewriter` available fails immediately after one
  attempt regardless of the attempt limit, rather than burning the whole
  budget resending text that would provoke the identical "unclear" response
  every time — resending unclear-task text unchanged is exactly the
  behavior §3.9/§4.5 distinguish it from (that's what an execution failure
  does), so falling back to it would blur the distinction the spec takes
  care to establish.

### 4.6 — Implement retry exhaustion handling
- **Status:** partially done — executor-level behavior only
- **Deviations:** Everything this mission can implement is implemented:
  `execute_steps` stops at the first permanently-failed step, names it and
  the failure cause, and preserves every already-succeeded `StepOutcome`
  rather than discarding them. Writing partial results onto the event
  record, notifying the event's originator, and moving on to the next
  event in the queue are explicitly out of scope for the Protocol Engine —
  they're the orchestrator's (§6.11), persistence's, and the bot's
  (§8.11) jobs respectively, none of which exist yet. This module's
  responsibility ends at producing the data (`ProtocolRunResult`) those
  future pieces will need.

### 4.8 — Leave a seam for task-based execution
- **Status:** done
- **Deviations:** None — no code was added for this beyond what 4.4
  already required. `execute_steps` is the one function boundary; nothing
  else in the codebase executes a step list directly. No field, flag, or
  branch exists for an alternative (dependency-graph) execution mode.

### 4.7 — Author the demonstration profile
- **Status:** done
- **Deviations:** Event types/areas reuse Mission 2's seed-dataset domain
  ("fire"/"medical", "north_sector"/"south_sector") rather than inventing
  a new one, for continuity across the fixture data, the reference agent's
  tool names, and this profile. This is the first profile to combine a
  real `Agent` with a real `Protocol`, and it validates cleanly end to end
  through `profiles.loader.load_profile` — the concrete confirmation that
  4.1's bug fix actually works, not just that its unit tests pass in
  isolation. All required properties present in one four-protocol set:
  read-only-only (`status_check`), side-effecting
  (`dispatch_response`, flagged), and a genuine tie pair
  (`minor_incident_review`/`routine_check` — identical approved tools and
  expected success output, overlapping descriptions, distinct criticality
  `MEDIUM`/`LOW`, one flagged and one not).

### 6.1 — Build the Main Agent's AI agent
- **Status:** done
- **Deviations:** Found and fixed a real architecture bug while designing
  this: Mission 1's `profiles.loader._construct_core_agents` docstring
  claimed it would become "the one place that changes to wire [core
  agents] in." That's wrong — constructing a `MainAgent` requires
  importing `orchestrator.main_agent`, and `profiles` is a low-level
  package that may never call upward into `orchestrator`
  (`docs/allowed_calls.md`'s own layering rule). Fixed by correcting
  `profiles/loader.py`'s docstring (no functional change — it still
  returns `{}`) and adding the real, correctly-layered replacement,
  `orchestrator.main_agent.construct_core_agents(base_config)`, to be
  called by whatever future startup code assembles the running system
  (§7/§9), not by profile loading. Separately: `agents.base` had to become
  a real `agents/` entry point (not just a `TYPE_CHECKING` import, unlike
  `protocols.retry`/`executor` in Mission 4) — `MainAgent` genuinely
  subclasses `agents.base.Agent` at runtime, and the work plan's own
  branch table places it in `orchestrator/`, not `agents/`, so any package
  defining a concrete agent needs real access to the base class. Updated
  `docs/allowed_calls.md` and the import-graph test accordingly. One
  design choice, confirmed as low-ambiguity and consistent with the
  project's decision pattern: risk/selection/formulation/judgment each get
  a pure prompt-builder and a pure response-parser, tested without any
  agent involved, plus a thin glue function — keeps almost all coverage
  off the crewai seam.

### 6.3 — Implement risk assessment
- **Status:** done
- **Deviations:** The assessed "risk level" is derived from a numeric
  score (`RISK_SCORE: 0.0`–`1.0`, the Main Agent's prompt response),
  compared against the live `risk_threshold` — `docs/vocabulary.md`
  types `risk_level` as `str`, and a bare string can't be meaningfully
  compared against a `float` threshold, so the numeric score is the
  actual comparison input and `"high"`/`"low"` is the derived label that
  gets stored, reconciling the two. `score >= risk_threshold` counts as
  high (at-or-above, not strictly above) — a documented convention, not
  stated explicitly either way in §6.3. **The `RISK_SCORE:`/`REASON:`
  response format is an unverified prompt convention**, same status as
  Mission 3's `UNCLEAR_TASK:` sentinel — flagged for a live-model smoke
  test once crewai is installed.

### 6.4 — Implement protocol selection
- **Status:** done
- **Deviations:** "Apply the same rule to a commander's own request" (§6.4's
  last bullet) is explicitly the caller's responsibility, not this
  function's — `select_protocol` only ever receives `risk_level`, never
  who originated the request, since that routing lives in message intent
  classification (§6.13, deferred). Noted in the module docstring rather
  than silently ignored. High-risk auto-resolution picks
  `max(candidates, key=criticality)` only among candidate names the model
  actually named *and* that exist in the loaded protocol set — a name the
  model hallucinated is silently excluded from the candidate pool rather
  than crashing, though this hasn't been exercised against a live model
  (unverified prompt convention, same status as 6.1/6.3).

### 6.7 — Implement approval holds
- **Status:** done
- **Deviations:** Closes a real gap Mission 2 deferred by name: the
  `held_events` table (`persistence/schema.py`) and the
  `store_held_event`/`list_held_events`/`resolve_held_event` operations
  (`persistence/sqlite_backend.py`) are now fully implemented — generic
  across both hold kinds via a `kind` column, not approval-specific, even
  though only the approval-hold *orchestration* is built this mission.
  Added migration 6 (`persistence/migrations.py`). This module explicitly
  does **not** resume execution on approval or write a declined outcome
  onto the event record — those belong to the new-event flow (§6.11,
  deferred). `answer_approval_hold` finds the target hold via
  `list_held_events` before resolving (the interface has no
  single-hold-lookup operation, and adding one wasn't warranted for this)
  — a resolve-vs-list race (resolved by someone else between the two
  calls) is still caught, since `resolve_held_event` itself raises
  `NotFoundError` on an already-resolved hold and that's caught too.

### 6.8 — Implement task formulation
- **Status:** done
- **Deviations:** `precedent_context` defaults to `()` — the §6.5 seam,
  confirmed with the user. `rewrite_task` matches
  `protocols.executor.execute_steps`'s `task_rewriter` callable signature
  exactly once `main_agent` is bound via `functools.partial`, verified
  with an integration test running it through the real Mission-4 executor
  (not just checked by inspection). **Both the multi-agent
  `AGENT:`/`TASK:` formulation format and the plain-text rewrite response
  are unverified prompt conventions**, same status as 6.1/6.3/6.4.

### 6.10 — Implement success judgment
- **Status:** done
- **Deviations:** `insight_text` defaults to `""` — the §6.9 seam,
  confirmed with the user. §6.10's "when the judgment call itself fails,
  rerun only the judgment, never the agents" is implemented as a
  documented *caller contract*, not code here: `judge_success` makes one
  call and either returns a verdict or raises
  `OrchestrationParseError` — it never retries itself, since retry policy
  is an orchestration decision §6.11 (deferred) will own, not something
  this function should decide unilaterally. **The `VERDICT:`/`REASONING:`
  response format is an unverified prompt convention**, same status as
  6.1/6.3/6.4/6.8.

### 6.15 — Enforce serial event processing
- **Status:** done
- **Deviations:** Generic over `process_fn` rather than coupled to the
  (deferred) new-event flow — confirmed with the user as the right way to
  build this without §6.11. Explicitly does not handle startup recovery
  (re-scanning persistence for in-flight/held events after a restart) —
  that's a future startup-sequence concern (§7/§9), not something a
  generic in-memory queue mechanism should own. Added `wait_until_idle()`
  and `stop()`, not named in §6.15's bullets, purely so tests have a
  deterministic point to check results against a background worker
  thread rather than sleeping and hoping.

### 6.2 — Implement clarification holds
- **Status:** not implemented this mission
- **Deviations:** Blocked on **5.2** (extraction) — clarification holds
  exist to catch events extraction couldn't classify, and extraction
  doesn't exist. Confirmed with the user before planning; no seam
  attempted. The storage this will use (`held_events`, generic across
  both hold kinds) is already complete, built as part of 6.7 — only the
  orchestration logic here is missing. Will be completed once §5.2 lands.

### 6.5 — Implement precedent lookup
- **Status:** not implemented this mission
- **Deviations:** Blocked on **5.8** (precedent search) — this task's own
  job is to call precedent search; there is nothing to call. Confirmed
  with the user before planning; no seam attempted. Will be completed
  once §5.8 lands.

### 6.6 — Implement closure on precedent
- **Status:** not implemented this mission
- **Deviations:** Blocked on **5.8**, via its own `Requires: 6.5` — 6.5
  is itself blocked on 5.8 (above). Confirmed with the user before
  planning; no seam attempted. Will be completed once §5.8 lands (and 6.5
  with it).

### 6.9 — Build the Insights Agent
- **Status:** not implemented this mission
- **Deviations:** Blocked on **5.7** (history query interface) — this
  agent's defining requirement is comparing the current run against
  comparable prior events from history; there is no history query
  interface to draw them from. Confirmed with the user before planning;
  no seam attempted. Will be completed once §5.7 lands.

### 6.12 — Implement the question flow
- **Status:** not implemented this mission
- **Deviations:** Blocked on **5.7** (history query interface) — questions
  about the past must be sent to the History Agent, which itself needs
  §5.7 and doesn't exist (§6.9, also deferred). Confirmed with the user
  before planning; no seam attempted. Will be completed once §5.7 lands.

### 6.14 — Hold the History reference
- **Status:** not implemented this mission
- **Deviations:** Blocked on **5.7** (history query interface) — this
  task is entirely "give the Main Agent a handle to history.query"; with
  no history query interface, there is nothing to hold. Confirmed with
  the user before planning; no seam attempted. Will be completed once
  §5.7 lands.

### 6.11 — Implement the new-event flow
- **Status:** not implemented this mission
- **Deviations:** Blocked transitively on multiple §5 prerequisites via
  its own `Requires:` line, which directly names 6.2, 6.5, 6.6, and 6.9 —
  all deferred above: 6.2 blocked on **5.2**, 6.5/6.6 blocked on **5.8**,
  6.9 blocked on **5.7**. This was not in the user's original named list
  of six but was identified and confirmed with them during planning as a
  necessary further deferral — a "new-event flow" stitching together
  mostly-nonexistent steps would not be a real flow. What §6.11 would
  stitch together *is* real and built: risk assessment (6.3), protocol
  selection (6.4), approval holds (6.7), task formulation (6.8), the
  protocol executor (Mission 4), and success judgment (6.10) all exist and
  are individually tested — only the orchestration gluing them into one
  flow, plus the three deferred steps interleaved among them, is missing.
  Will be completed once 5.2, 5.7, and 5.8 all land.

### 6.13 — Implement message intent classification
- **Status:** not implemented this mission
- **Deviations:** Blocked on its own `Requires: 6.11, 6.12` — both
  deferred above (6.11 transitively on 5.2/5.7/5.8; 6.12 directly on
  5.7). Not in the user's original named list of six; identified and
  confirmed with them during planning, same as 6.11. Will be completed
  once 6.11 and 6.12 are.

### Merge remediation — repairing damage from the Mission 5 / Mission 6 branch merge
- **Status:** done
- **Deviations:** Not a work_plan.md subtask — logged because it was real,
  necessary work discovered by re-reading the repo, not a hypothetical.
  This session's Mission 6 work and a separately-developed Mission 5
  branch (History System, 5.1–5.10, fully real: `history/interface.py`,
  `query.py`, `precedent.py`, `write.py`, `extraction.py`, `summarize.py`,
  `scheduler.py`, `retrieval.py`, `time_utils.py`, plus `agents/history.py`)
  were merged via GitHub PRs outside this conversation. The merge left
  concrete regressions, confirmed by running the suite (19 failing tests),
  not assumed:
  - `persistence/migrations.py` had **two migrations both numbered `6`**
    (Mission 5's "add event indexes to summaries" and this mission's
    "create held_events table"). `run_migrations` skips any entry whose
    version is `<= current_version`, so after applying the first `6` the
    second was silently never applied — `held_events` didn't exist,
    breaking every held-event test. Fixed by renumbering the held-events
    migration to `7` (confirmed it had never actually run against any
    real database, so renumbering was safe — never renumber a migration
    that might already be applied somewhere).
  - `tests/test_architecture.py`'s `ENTRY_POINTS` dict had **duplicate
    literal keys** (`"agents"` three times, `"history"`/`"protocols"`
    twice) — Python silently keeps only the last assignment, dropping
    `agents.history` and `history.interface`, which real code now
    imports. Fixed by collapsing to one entry per key, unioning what both
    branches actually needed.
  - `docs/allowed_calls.md` had the same duplication in table form (three
    `agents` rows, two each for `history`/`protocols`) — collapsed to one
    row per package, matching the corrected `ENTRY_POINTS` exactly.
  - `docs/progress.md`'s Mission-status table (this file) still said
    Missions 5 and 6 were "Not started" despite the append-only log below
    it — which merged cleanly, both branches' entries intact — showing
    otherwise. Corrected.
  - Core-agent construction was left split across two independently
    correct functions with nothing combining them:
    `profiles.loader._construct_core_agents` builds the History Agent
    (legitimate — `agents/history.py` isn't `orchestrator/`, no layering
    violation), `orchestrator.main_agent.construct_core_agents` builds the
    Main Agent (also legitimate — has to live outside `profiles`, per this
    mission's own earlier fix). Added
    `orchestrator.flows.assemble_core_agents(loaded_profile, base_config)`
    to merge both (and, from this point on, the Insights Agent too) — the
    one thing a future startup sequence (§7/§9) will call.
  - Full suite confirmed at 274/274 passing after these fixes, before any
    further work began.

### 6.2 — Implement clarification holds
- **Status:** done
- **Deviations:** Built as an extension of `orchestrator/holds.py`
  (Mission 6 Part 1's approval-hold module), sharing the same generic
  `held_events` storage with `kind="clarification"` — as originally
  planned when that storage was built. `determine_clarification_hold`
  relies on `history.extraction.extract_event` already nullifying an
  out-of-registry stated classification before this code ever sees it, so
  "classification is `None`" is the one unified signal covering both
  §6.2 bullets ("extraction couldn't resolve it" and "stated type outside
  the registry") — confirmed by reading `history/extraction.py` directly.
  The unresolved field is always `"classification"` (a constant, not
  derived) since that's the only field this hold type ever fires on, per
  §2.1/§2.2 (an empty area/description never holds an event). Resolution
  is validated against `registries.event_types.EventTypeRegistry.is_valid`,
  rejecting free text outright. This module still does not resume
  execution itself (resuming at risk assessment on answer, not
  extraction) — that wiring is §6.11's, built next.

### 6.5 — Implement precedent lookup
- **Status:** done
- **Deviations:** None beyond what's inherent in the API it wraps —
  `look_up_precedent` is a thin, read-only pass-through to
  `history.query.HistoryQueryService.search_precedents` (confirmed real
  by reading `history/query.py` and `history/precedent.py` directly, not
  assumed). Recording matches onto the event record and passing them to
  task formulation are both §6.11's job, since this function only reads.

### 6.6 — Implement closure on precedent
- **Status:** done
- **Deviations:** None. Three independent, explicit checks (risk level,
  match resolution, human-activation exclusion) rather than one combined
  condition, matching §6.6's four separate bullets one-to-one — makes
  each rule independently testable and each failure independently
  readable. Among several matches, the first `resolved=True` one is used,
  relying on `history.precedent.find_precedents` already returning
  matches most-recent-first (confirmed by reading its sort key directly).
  Recording the closure and notifying commanders are §6.11's job — this
  function only decides.

### 6.9 — Build the Insights Agent
- **Status:** done
- **Deviations:** `comparable_history` is designed to reuse §6.5's
  `orchestrator.precedent.look_up_precedent` output directly, not a
  second, separate history query — work_plan.md §9.20 names exactly this
  pair ("the two separate history reads per event — one in precedent
  lookup and one in the Insights Agent's comparison") as overlapping
  ground worth merging; built merged from the start instead of needing
  that fix later. Zero tools (no `@tool`-decorated methods at all), the
  strictest reading of "read-only tools only" — the same choice Mission
  5 made for the History Agent, kept consistent. `orchestrator.flows`'s
  core-agent seam (Mission 6 Part A) extended to include this agent too,
  as already promised in that module's docstring. **The insight prompt's
  framing is an unverified-against-a-live-model design choice**, though
  there's no structured response format to get wrong — free text is the
  whole point, and it's consumed as a plain string by `judge_success`
  (§6.10), unchanged.

### 6.12 — Implement the question flow
- **Status:** done
- **Deviations:** Reuses `orchestrator.formulation._parse_formulation_response`
  directly (an intra-package import, not a cross-package one — no
  entry-point concern) rather than reimplementing the identical
  `AGENT:`/`TASK:` parsing a second time, since the format is exactly the
  same. The History Agent needs no special-casing to be reachable for
  "about the past" questions, confirmed by a dedicated test — it's simply
  one more entry in the registry the routing prompt can choose, like any
  other agent. Composition is skipped (no second Main Agent call) when
  only one agent was chosen, both as a minor efficiency and because
  composing a single answer with itself has nothing to add. A sub-agent
  that fails or reports its task unclear doesn't crash the whole
  question — its slot in the composition becomes a visible
  "no usable answer" note rather than aborting every other agent's
  answer too. **The routing and composition prompt formats are
  unverified-against-a-live-model design choices**, same status as
  every other Main Agent decision this mission.

### 6.13 — Implement message intent classification
- **Status:** done
- **Deviations:** Deliberately classification-only — the routing bullets
  (question → question flow, report/request → the new-event flow at
  different entry points, the commander approval-flag bypass) are
  `orchestrator.flows`'s job, built next, not duplicated here. Built
  before `orchestrator/flows.py` in this mission's order specifically
  because flows' routing depends on it. **The `INTENT:`/`REASON:`
  response format is an unverified prompt convention**, same status as
  every other Main Agent decision this mission.

### 6.11 — Implement the new-event flow
- **Status:** done
- **Deviations:** §6.14 (hold the History reference) is folded into this
  entry rather than given its own module — `FlowDeps.history_query_service`
  *is* "one persistent handle to the history query interface, created at
  startup and used by every flow": precedent lookup (6.5), the Insights
  Agent's comparable history (6.9), and the question flow (6.12) all take
  it from this one bundle rather than each opening a second path into
  history, confirmed by reading each call site. Every event-record write
  goes through `history.interface`'s dedicated functions
  (`record_initial_event`/`record_extracted_fields`/`record_event_state`/
  `record_step_execution`/`record_event_outcome`) — never a raw
  `persistence.update_event` call from this module — matching Mission 5's
  write path exactly. Protocol selection always runs before the
  precedent-closure check (not skipped on a likely close) since risk
  assessment and protocol selection are independent of whether precedent
  ultimately closes the event — confirmed correct by a manual
  closed-on-precedent run, not assumed. Restartability (§6.11's own
  requirement) is structural: both `resume_after_clarification` and
  `resume_after_approval` take only a hold ID and an answer, re-reading
  the event's already-extracted fields and the hold's own payload from
  persistence rather than from anything held in memory — verified with a
  dedicated test that opens a second, independent `SQLitePersistence`
  against the same database file mid-hold and resumes through it
  successfully. `orchestrator.judgment.SuccessVerdict.verdict` speaks
  `"success"/"failure"/"uncertain"` (its own pre-existing sentinel
  vocabulary, unchanged) while `history.write.VALID_OUTCOMES` speaks
  `"succeeded"/"failed"/"uncertain"` — a real mismatch between two
  already-built modules that would have raised `ValueError` from
  `record_event_outcome` on every real success/failure verdict; found by
  writing this module's own integration tests, fixed with one small
  translation table (`_VERDICT_TO_OUTCOME`) at the single point the two
  vocabularies meet, rather than coupling either module to the other's
  wording. A failed task-formulation call and a failed success-judgment
  call are each retried exactly once before giving up and recording
  `"failed"` — no agent has acted yet when formulation fails, and only the
  judgment call itself (never the agents, which already acted on the
  world) is retried when judgment fails to parse, per §6.10's own retry
  boundary. Manually verified end to end (crewai seam mocked, per
  Mission 3's established technique): a report that closes on precedent,
  a report that holds for approval and resumes approved, and a
  commander's request that bypasses the approval flag — all three write
  the correct outcome via `history.interface`, confirmed by reading the
  stored event back after each run. Full suite green (335/335) and
  `tests/test_architecture.py` passes with the corrected `ENTRY_POINTS`.

### CI - per-mission test steps (Missions 1 through 6)
- **Status:** done
- **Deviations:** Not a work_plan.md subtask - requested directly by the
  user, logged for the same reason as the merge-remediation entry above:
  real work worth a record. `.github/workflows/ci.yml`'s single "Run
  tests" step (`pytest`, run since 1.1) is replaced with one step per
  mission, 1 through 6, each running that mission's own `test_*.py` files
  by explicit path, followed by one final "Full suite (drift check)" step
  that still runs plain `pytest` - a safety net catching a test file a
  future change forgets to add to any mission step, so coverage can never
  silently narrow the way `tests/test_architecture.py`'s `ENTRY_POINTS`
  dict silently narrowed in the Mission 5/6 merge. Every one of the 48
  `tests/test_*.py` files is assigned to exactly one mission step -
  verified mechanically (no file missing, none duplicated, none listed
  that doesn't exist) and by running each mission's exact file group
  locally; the six groups' pass counts (46, 60, 41, 44, 15, 129) sum to
  the full suite's 335. Assignment is by the mission that *introduced*
  each file, per `docs/progress.md`'s own entries, not by its package
  directory - confirmed individually for every file whose name alone
  didn't make this obvious: `test_history_logging.py` tests
  `tools.logging_config.log_ai_interaction`, a general §1.8 logging
  utility despite its name, so it's filed under Mission 1, not 5;
  `test_persistence_held_events.py` lives beside the Mission-2
  persistence tests but its own docstring says "§6.7, Mission 6", so it's
  filed there; `test_persistence_conformance.py` and
  `test_agent_results.py` stay under the mission that introduced them
  (2.11, 3.9) even though later missions added cases to those same files.
  Python version pinned in CI stayed at 3.11, unchanged - the repo runs
  fine under 3.11 (no 3.13-only syntax used) and revisiting the pin
  wasn't part of this request.

---

## Mission 8 — built against a Mission 7 that does not exist yet

Every §8 subtask requires either 7.2, 7.4, 7.6, 7.7, 7.8, or 7.9, and the
`api/` package is still just its Mission-1 skeleton (`api/__init__.py`
saying "not yet implemented"). The user directed this mission to proceed
anyway, explicitly: skip implementing any Mission-7 dependency, build
everything in `bot/` that does not require one, and — for anything that
does — add a minimal, clearly-marked placeholder rather than wait.

The seam is `bot/api_client.py`: `BotApiClient`, an abstract interface
covering every operation the rest of `bot/` needs from the API Layer,
with request/response shapes designed directly from `docs/vocabulary.md`
and each §8 subtask's exact wording. `UnimplementedApiClient` is its only
implementation today — every method raises `bot.errors
.ApiNotImplementedError`, naming the exact §7 subtask it is blocked on
(checked by `tests/test_bot_api_client.py`, which also asserts every
abstract method has a corresponding raise-and-name test, so the seam
cannot silently grow an unmarked gap). Every other `bot/` module is built
and tested against `BotApiClient`'s interface via dependency injection
(`tests/bot_fakes.py`'s `FakeBotApiClient`), never against
`UnimplementedApiClient` directly except `bot.app`, which wires the
default in. Closing Mission 7 means writing one new class implementing
`BotApiClient` with real HTTP calls to the profile's `api_port` and
changing one line in `bot.app.build_deps` — nothing else in `bot/` needs
to change, per the user's explicit "keep the structure ready" instruction.
`bot` still never imports the `api` package itself — "bot calls only api"
(`docs/allowed_calls.md`) is a network boundary (the profile's port),
not a Python import, so this seam lives entirely inside `bot/` and
touches nothing in `api/`.

A second, shared piece of infrastructure spans several subtasks:
`bot/notifications.py`'s `BotNotification` / `dispatch_notification` /
`run_notification_poll_loop`. Work_plan.md §7.2 leaves "how a finished
result reaches whoever submitted it" as one of the API's own open design
questions ("implement one path, not an unspecified mixture"); the same
question applies to §8.4/§8.5/§8.6's unprompted pushes. Rather than guess
which mechanism §7 will choose (a webhook into the bot vs. the bot
polling the API), every proactive push funnels through one shape and one
retrieval method, `poll_pending_notifications` — itself part of the same
stub seam. Introduced while building §8.4 (the first subtask needing a
push), reused by §8.5, §8.6, §8.9, and §8.11 rather than each
reimplementing routing.

`python-telegram-bot` 22.8 was added to `requirements.txt` as a real,
uncommented dependency and installed in this environment — unlike
`crewai` (§3.5), it needs no model API key to install or to exercise its
non-network code paths, so `bot/telegram_client.py`'s `PTBTelegramClient`
is written and tested against the real, installed library (its classes'
signatures introspected directly, not guessed), with only the
network-performing `Bot` methods (`get_me`, `send_message`,
`answer_callback_query`) replaced by mocks in tests
(`tests/test_bot_telegram_client.py`). It remains **unverified against a
live bot token or a real Telegram chat** — same "unverified against a
live integration" status this codebase already carries for the CrewAI
adapter (§3.5) and the Main Agent's prompt conventions (§6.x).

Every handler `bot.app` registers is wrapped (`bot.app._guarded`) so an
`ApiNotImplementedError` reaching it becomes a clear, honest chat reply
— "This isn't available yet: ... §7.X" — rather than a crash or a
silently dropped update. The bot is runnable today against a real
Telegram token (`python -m bot.app <profile_module>`); every capability
that depends on Mission 7 will simply say so, in the chat, naming the
exact subtask it is waiting on.

**A real gap discovered in already-built Mission 6 code, left
unmodified per this mission's explicit instruction not to touch prior
missions:** `orchestrator.holds.answer_approval_hold` only accepts
`Literal["approved", "rejected"]`, and `orchestrator.flows
.resume_after_approval` resumes with `hold["selected_protocol_name"]`,
which `orchestrator.selection.select_protocol` never sets for an
ambiguous-selection hold at low risk (it stays `None`). §8.5 requires
presenting an ambiguous-selection hold's candidates for a commander to
choose *among* — there is currently nowhere on the orchestrator side for
that choice to go. `BotApiClient.answer_approval_hold`'s `decision`
parameter is declared as a plain `str` (not the narrower
`Literal["approved", "rejected"]`) specifically so the bot side is ready
to send a candidate's name once this is fixed; its docstring records the
gap in full. Whoever builds §7 (or a follow-up fix to §6.7) needs to
close it — either by widening `answer_approval_hold`'s decision type, or
by having the API translate a candidate-name decision into a recorded
selection before resuming.

Full suite green (462/462) including `tests/test_architecture.py`, which
needed no changes — every new module lives inside `bot/`, and
`docs/allowed_calls.md` already declared `bot.app` as the package's only
entry point.

### 8.1 — Register and configure the bot
- **Status:** done
- **Deviations:** The bot token is read from `LoadedProfile
  .resolved_secrets`, already populated by `profiles.loader` at load
  time per §1.5 — `bot.app._resolve_bot_token` re-imports the profile
  module (a no-op after the first import; `importlib` caches it) only to
  read the *name* `BOT_TOKEN_ENV` points at, never the secret value a
  second time, and never touches `profiles/loader.py` or
  `profiles/spec.py` (Mission 1, done, left unmodified per this
  mission's instruction). "Rejected" is checked by actually calling
  Telegram's `getMe` (`bot.telegram_client.PTBTelegramClient
  .validate_token`) — a token that is merely *absent* already fails
  earlier and louder, inside `profiles.loader.load_profile` itself.
  "One bot per deployment" is `bot/singleton_lock.py`: an
  exclusive-create lock file beside the deployment's database (mirroring
  `config.settings_store`'s "beside the db" convention), acquired before
  the token is even validated. It catches the ordinary case — starting a
  second process while the first is healthy — by construction, but does
  not detect a lock left behind by a process that crashed without
  releasing it; documented as a deliberate limitation, not a Mission-7
  gap, in that module's own docstring. The profile's `api_port` is
  threaded into `LoadedProfile` and available on `BotDeps.loaded_profile`
  for whenever a real HTTP-backed `BotApiClient` needs it — not used by
  `UnimplementedApiClient`, since it makes no network calls at all.

### 8.2 — Resolve users against the user table
- **Status:** done for everything not behind the Mission-7 seam
- **Deviations:** The identity → permission-level *lookup*
  (`BotApiClient.resolve_user`) is the one piece genuinely blocked on
  §7.9 — the user table lives behind `persistence`, and `bot` has no
  path to it except through the API. Everything else is real: the
  permission *comparison* is `auth.permissions.is_permitted` (§1.9's
  shared function), imported directly in `bot/users.py` — `auth` is a
  low-level package callable by anything (`docs/allowed_calls.md`), and
  §1.9 itself names the bot as one of the function's two callers, so
  this is not a Mission-7 dependency despite §8.2's own header line
  listing 7.9. Both required refusal messages are implemented and
  tested word-for-word: "not a registered user" for an unknown identity,
  and a message naming the specific refused action for a registered
  user acting above their level. §8.2's explicit prohibition — no
  command that adds, changes, removes, or lists users — is enforced two
  ways: nothing in `bot/users.py` exposes such an operation, and
  `bot.app.REGISTERED_COMMANDS` (the exhaustive, asserted-against set of
  slash-commands the bot registers) contains only `profile` and
  `settings`; both are checked by `tests/test_bot_users.py`.

### 8.3 — Implement the single message entry point
- **Status:** done
- **Deviations:** Structurally complete and independently testable
  (`tests/test_bot_entrypoint.py`) — the one thing it cannot do for real
  is submit a message, since `BotApiClient.submit_message` is the
  §7.4 stub. `bot.app` routes only non-command text
  (`filters.TEXT & ~filters.COMMAND`) to `bot.entrypoint
  .handle_incoming_message`, reserving `/profile` and `/settings` as
  the only slash-commands, matching §8.3's "reserve slash-commands ...
  so they never collide with free text."

### 8.4 — Implement clarification prompts
- **Status:** done for everything not behind the Mission-7 seam
- **Deviations:** Buttons carry `hold_id` and the chosen classification
  directly in their callback data (`clarify:<hold_id>:<classification>`)
  rather than any server-side session state, so answering needs nothing
  but the button press itself. The available classifications are
  expected to arrive as part of `HeldClarificationNotice` (via the
  poll-notification seam) rather than the bot re-deriving "the loaded
  event types" from its own copy of the profile — the API process is
  the authority on what is actually running (relevant once §7.7's
  running/on-disk distinction exists), so the choices always come from
  wherever the hold was created. The race between two commanders is
  handled on the bot side by rendering whatever `HoldAnswerOutcome
  .resolved_by`/`.message` the API returns for a `not_found` status —
  `orchestrator.holds.answer_clarification_hold`'s current `not_found`
  message does not itself name who resolved it first; `resolved_by` is
  declared on the DTO ready to receive that once Mission 7 (or a
  refinement of §6.2) supplies it, and is exercised in tests via the
  fake client, not the real orchestrator function.

### 8.5 — Implement approval prompts
- **Status:** done for everything not behind the Mission-7 seam,
  **except** relaying an ambiguous-selection choice all the way through
  to a resumed run, which cannot work until the orchestrator-side gap
  described above (Mission-8 preamble) is closed
- **Deviations:** `bot/approval.py` presents the two hold reasons with
  genuinely different text and buttons — yes/no for a flagged protocol,
  one button per candidate for an ambiguous selection — and
  `notify_uncertain_verdict` is deliberately phrased with no question
  mark and no buttons, tested directly (`test_uncertain_verdict_is_not
  _phrased_as_a_question_and_has_no_buttons`). The race-condition
  handling mirrors §8.4's.

### 8.6 — Implement precedent-closure notifications
- **Status:** done for everything not behind the Mission-7 seam
- **Deviations:** None beyond the shared seam. Pushed individually (one
  `send_text` call per commander, never batched) and phrased with no
  question mark, per §8.6's "clearly informational" requirement.

### 8.7 — Implement profile commands
- **Status:** done for everything not behind the Mission-7 seam
- **Deviations:** Reads (`/profile view`, `/profile diff`) require only
  that the caller be a registered user — there is no dedicated action
  key for "view the profile" in `auth.permissions.ACTION_REQUIREMENTS`
  (§1.9's table lists exactly six actions, none of them a read), so
  gating a read on a nonexistent action key was not an option; §8.7's
  own "allow viewers to read" confirms this is the intended behavior,
  not a gap. Writes require `"edit_profile"`, the existing key. The
  `approval_flag`-must-be-explicit requirement is enforced only for
  `add`/`edit` — `remove` identifies a protocol by name alone and has no
  flag to give. `bot.app._parse_protocol_write_command` accepts a
  pipe-delimited command (`/profile add name | description | agents,... |
  tools,... | expected_output | criticality | true|false`), chosen over
  space-delimited so every free-text field can itself contain spaces or
  commas without ambiguity; tested directly, independent of the network
  seam.

### 8.8 — Implement settings commands
- **Status:** done for everything not behind the Mission-7 seam
- **Deviations:** Same read-is-open, write-requires-`"change_settings"`
  reasoning as §8.7. Every value is validated in `bot/settings_commands
  .py` *before* it would ever reach the API — non-negative integer for
  retry count, `0.0`–`1.0` for the risk threshold, a positive integer for
  the lookback window — independent of whatever validation §7.8
  eventually adds server-side, per §8.8's own "validating the value
  before sending it." The confirmation wording ("took effect
  immediately... unlike a profile edit, no restart is needed") is
  deliberately the mirror image of §8.7's "nothing changed... applies
  from the next start", per §8.8's own explicit "worth stating so a
  commander using both will otherwise not know which is which."

### 8.9 — Deliver asynchronous results
- **Status:** split — the acknowledgment half is done for real; delivery
  is done for everything not behind the Mission-7 seam
- **Deviations:** "Acknowledge every submission immediately" is not a
  separate mechanism — it is `bot.entrypoint.handle_incoming_message`
  replying in the same turn `submit_message` returns a job ID, before
  any later poll ever runs, so there genuinely is no silent wait.
  Delivery (`bot/results.py`) is one branch of the shared
  `bot.notifications.dispatch_notification`, referencing the original
  message via `TelegramClient.send_reply`'s `reply_to_message_id`.

### 8.10 — Format output for chat
- **Status:** done
- **Deviations:** None — the one §8 subtask with zero Mission-7
  dependency, fully real. `bot/formatting.py`'s `split_message` breaks
  at paragraph, then sentence, then plain-newline boundaries before
  falling back to a hard character cut, and is exercised with text
  engineered to hit each fallback tier
  (`tests/test_bot_formatting.py`). Seven distinct headers exist, not
  the four §8.10 names (clarification, approval, closure, result) —
  `uncertain_verdict`, `failed`, and `declined` were added too, since
  §8.5 and §8.11 separately require those to be visually distinct from
  each other and from a plain result.

### 8.11 — Deliver failure notifications
- **Status:** done for everything not behind the Mission-7 seam
- **Deviations:** `bot/failures.py` reuses `bot.telegram_client
  .send_reply` and the same `BotNotification` delivery path as §8.9's
  results, distinguished only by `kind` (`"job_failed"` vs.
  `"job_finished"`) and by `bot.formatting`'s separate `"failed"` header
  — the same infrastructure, not a parallel one, since both are "an
  asynchronous outcome reaching whoever is waiting on it."
### 2.13 - Add held-event lookup by event ID
- **Status:** done
- **Deviations:** Not in work_plan.md's original text - added as a new
  subtask via a user-approved addendum while planning Mission 7 (`docs/
  work_plan.md` itself was edited first to add this subtask, in an earlier
  pass; this entry logs the implementation). `fetch_held_event(kind,
  event_id) -> dict | None` added to `persistence/interface.py` and
  `persistence/sqlite_backend.py`, same "completing §2.7's intent"
  reasoning as `fetch_event`/`update_event` before it. Looks up by
  `(kind, event_id)` rather than the orchestrator's internal `hold_id`,
  ordered most-recent-first as a defensive tie-break (an event carries at
  most one hold of a given kind at a time by design - docs/vocabulary.md -
  but the table itself has no constraint enforcing that). Reuses the
  existing `_decode_held_event_row` helper unchanged, so a resolved hold's
  `resolved_by`/`resolved_at`/`resolution` decode identically to how
  `list_held_events` already decodes a pending one - the only difference
  is this method doesn't filter `WHERE resolved = 0`. Extended the
  backend-swap conformance suite (§2.11) with four new cases: pending,
  resolved (reporting resolver and timestamp), unknown event ID, and
  kind-scoping. This exists to serve §7.11, not built yet.


### 6.7 — Implement approval holds (amended: candidate-protocol selection for the ambiguous-selection case)
- **Status:** done
- **Deviations:** A real gap found while designing §7.11 (POST /Approve),
  fixed here per explicit user decision rather than translated at the API
  boundary: "a teammate reading `answer_approval_hold`'s signature alone,
  without reading its body, should see that a candidate-protocol
  selection is a real, first-class outcome of answering an approval
  hold." `answer_approval_hold` now accepts a third `decision` shape
  alongside `"approved"`/`"rejected"` — a candidate protocol name, for a
  hold whose `reason` is `"ambiguous_selection"` (§6.4's no-clear-fit-at-
  low-risk case), which previously had no working answer path at all
  (`selected_protocol_name` stayed `None`, and nothing downstream could
  do anything with that). Purely additive: branches strictly on
  `held["reason"]`, never on `decision`'s shape, so a `"flagged_protocol"`
  hold's existing approve/reject handling is reached exactly as before —
  confirmed by a dedicated regression test, not just by the original
  approve/reject tests still passing unchanged. A candidate is validated
  against exactly `held["candidate_protocol_names"]` (already captured at
  hold-creation time — no new dependency); an invalid one returns a new
  `HoldAnswerResult` status, `"invalid_candidate"`, naming the real
  candidates. A valid selection overrides the returned hold's
  `selected_protocol_name` with the chosen candidate and reports
  `"approved"` — the same status a flagged-protocol approval already
  used — so every existing caller of that status needs no new branch.
  `orchestrator/flows.py`'s `resolve_approval` additionally writes
  `selected_protocol` onto the event record (harmless re-write for the
  flagged-protocol case, real for a chosen candidate), and a new
  integration test (`tests/test_orchestrator_flows.py`) confirms an
  ambiguous-selection hold resolves and resumes to a genuine, completed
  protocol run — the previous tests covered only the `None` case by
  omission, never by assertion.

### 7.1 — Specify payloads
- **Status:** done
- **Deviations:** `docs/api_spec.md` — a document, no code, same pattern
  as §1.2's vocabulary file. Defines the acknowledgment shape once (the
  job ID *is* the event ID — no second identifier), how a held/closed
  event appears in a `GET /Job/<event_id>` response (a `status` field
  distinguishing "still running" from "waiting on a commander" from
  "closed without running," always `200 OK` — a run's own outcome is
  data, never a transport-level failure), and the one error shape every
  endpoint shares. Every subsequent §7.x subtask below was built against
  this document and it was kept in sync as each landed — the candidate-
  protocol decision shape (§7.11) and the `"rejected"` correction (below)
  are both reflected in it, not left stale.

### 7.2 — Build the async job mechanism
- **Status:** done
- **Deviations:** No separate "jobs" table — the job ID *is* the event
  ID (`history.interface.record_initial_event` already returns one
  synchronously), and every state §7.2 lists is derived from
  already-persisted event/hold state, except one genuinely transient bit:
  "queued" vs. "running," which has no meaningful persisted distinction
  and doesn't need one. `orchestrator/queue.py`'s `SerialEventQueue`
  gained two small accessors for this: `currently_processing()` (the raw
  item mid-flight, or `None` — this class stays fully generic over what
  an "item" is, per its own §6.15 design; `api/app.py`'s convention of
  submitting `(event_id, work_fn)` pairs is api/-only, not baked into the
  queue) and `qsize()` (items not yet picked up). "Result retrieval by
  job ID" is `api/jobs.py`'s `GET /Job/<event_id>`. "How a finished
  result reaches whoever submitted it" is deliberately **not** decided
  here — §7.2's own wording defers that to "the bot pushes it... an
  external system polls," and Mission 8's `bot/` already committed to
  polling via its `BotApiClient.get_job_result`/`poll_pending_notifications`
  seam; this mission implements the polling side those depend on and
  takes no position on a push/webhook alternative.

### 7.3 — Implement `POST /Event`
- **Status:** done
- **Deviations:** None beyond the split every §7.2-queued entry point
  shares: `begin_report` (synchronous — write the raw text, return the
  event ID, no model call) and `run_report_extraction` (the queued
  continuation). Sets nothing sensor-specific beyond `source="sensor"`
  and the receipt timestamp — `history.extraction.extract_event`'s own
  `source == "sensor"` branch (Mission 5) already sets `occurred_at`
  equal to `received_at` and never asks the model to extract one; §7.3
  triggers that, it doesn't reimplement it.

### 7.4 — Implement `POST /Msg`
- **Status:** done
- **Deviations:** Composes `classify_intent` + the split primitives
  itself rather than calling `orchestrator.flows.process_message`, which
  runs a report/request synchronously start to finish — exactly what
  §7.2 exists to avoid blocking a request on. A question is answered
  synchronously and directly, per §7.4's own rule ("a question has no
  job to track"). `classify_intent`/`answer_question` raising
  `OrchestrationParseError` here — with no job yet created to report a
  status against — is exactly `run_failure`'s (§7.10) reason to exist;
  tested directly, not left theoretical.

### 7.5 — Unify ingestion
- **Status:** done
- **Deviations:** None — verified with `tests/test_api_unified_ingestion.py`
  rather than by inspection, per §7.5's own explicit requirement. One
  test asserts every extracted/decided field converges given identical
  text submitted through both endpoints, differing only in `source` and
  `occurred_at`-derivation; a second asserts the *same* `orchestrator
  .flows.begin_report` call happens from both route handlers (monkeypatch-
  observed), not merely a same-shaped parallel implementation.

### 7.6 — Implement `CRUD /Protocol`
- **Status:** done
- **Deviations:** A thin wrapper over the already-complete
  `protocols.editor` (§4.3) — reads serve `ctx.deps.protocol_set.all()`
  directly (nothing to fetch), writes go through
  `add_protocol`/`replace_protocol`/`remove_protocol` unchanged. Every
  write response is the one fixed message §7.6 specifies, unconditionally
  — never a body resembling a successful state change. Tested against a
  disposable temp profile module on disk (the same technique
  `tests/test_protocol_editor.py` already established), never the shared
  fixture profile, since a write genuinely edits the file.

### 7.7 — Implement `GET /SYSTEM`
- **Status:** done
- **Deviations:** Two small additive changes to already-"done" modules,
  both confirmed with a dedicated regression test rather than assumed
  safe: `history/scheduler.py`'s `SummaryScheduler` gained
  `last_run_status()` (`last_run_at`/`last_run_ok`/`last_run_error`, all
  `None` until the *background* thread completes a pass — a manual
  `reconcile()` call, as most existing tests make, does not update it);
  `profiles/loader.py` gained `profile_file_hash` (captured once at load
  time) and the `hash_profile_file(module_path)` function both
  `LoadedProfile` construction and `GET /SYSTEM`'s live recompute call —
  the one function both moments use, so the two hashes can never be
  computed two different ways. "How many events are queued" is
  `SerialEventQueue.qsize()` (§7.2); "how many are held in each state" is
  `len(persistence.list_held_events(kind))` per kind.

### 7.8 — Implement `PUT /SYSTEM`
- **Status:** done
- **Deviations:** Accepts a partial body — only the keys present are
  changed, matching §1.7's own settings-store semantics. Rejects an
  unknown field by name (`invalid_input`, naming the field) rather than
  ignoring it. Validation ranges: `risk_threshold` in `[0.0, 1.0]`
  (matching `orchestrator.main_agent`'s own risk-score range, confirmed
  by reading it, not guessed); `retry_count` rejects only negative values
  — zero ("try once, never retry") is a legitimate operator choice, per
  §7.8's own wording ("a negative retry count... is a configuration
  error"); `lookback_window_days` rejects zero or negative, per the same
  bullet's "zero-length lookback window" wording. Each accepted value is
  written to the settings store before the response is sent.

### 7.9 — Enforce authentication and authorization
- **Status:** done
- **Deviations:** One `authenticate` function (`api/auth.py`) every route
  calls first, reading the caller's identity from an `X-Identity` header
  and rejecting an unregistered one outright — never defaulted to viewer.
  One `require(level, action)` wrapping `auth.permissions.is_permitted`
  — never an inline level comparison anywhere in `api/`. The sensor path
  authenticates through this same function, as a pre-registered identity
  (provisioned via `cli/user_admin`, same as any other), never a bypass.
  Profile edits, hold resolution, approval, and settings changes map
  one-to-one onto `auth.permissions.ACTION_REQUIREMENTS`'s existing
  `edit_profile`/`resolve_hold`/`approve_run`/`change_settings` actions —
  no new action names were needed. No endpoint creates, changes, or
  removes a user — `api/` has no such route, confirmed by its own
  contents rather than a negative test (there is nothing to test the
  absence of beyond "the route does not exist").

### 7.10 — Define the error contract
- **Status:** done
- **Deviations:** `api/errors.py`'s `ApiError` and its subclasses
  (`InvalidInputError` 400, `NotFoundError` 404, `ConflictError` 409,
  `AuthenticationError` 401, `AuthorizationError` 403, `RunFailureError`
  422, `InternalError` 500) are the only sanctioned way a route raises an
  HTTP-visible failure — nothing in `api/` builds an error response by
  hand. `NotFoundError`/`ConflictError`/`AuthenticationError`/
  `AuthorizationError` all report `error_class: "invalid_input"` per
  §7.10's three-class list — only their HTTP status differs; §7.10 names
  three classes, not five, and this keeps the JSON body's vocabulary
  matching that literally. A Flask/Werkzeug `HTTPException` (an unmapped
  route, a wrong method) is also translated into the same shape, so a
  caller never sees Werkzeug's own HTML error page. The bare-`Exception`
  handler's message is a fixed, generic string, confirmed by a dedicated
  test to never leak the real exception's text. A protocol run that
  exhausts its retries is `200 OK` via `GET /Job/<event_id>` reporting
  `status: "failed"` — not this error contract at all, per §7.10's own
  explicit rule, restated in `docs/api_spec.md`.

### 7.11 — Implement `POST /Approve/<event_id>` and `POST /Clarify/<event_id>`
- **Status:** done
- **Deviations:** Added a new persistence subtask, §2.13
  (`fetch_held_event(kind, event_id) -> dict | None`), via a user-approved
  addendum to `docs/work_plan.md`, since neither endpoint can address a
  hold by the event ID a caller actually has, or report an already-
  resolved hold's resolver and timestamp, without it — `list_held_events`
  only ever returns unresolved holds. `api/holds.py` is a thin wrapper:
  `decision` (`"approved"` / `"rejected"` / a candidate protocol name,
  per §6.7's amendment above) is passed straight through to
  `orchestrator.flows.resolve_approval` with no branching beyond routing
  to the right parameter — §6.7 is the one place that knows what a
  candidate name means. `POST /Clarify` and the approved (or candidate-
  selected) branch of `POST /Approve` both queue a continuation, since
  resuming is itself a full run (§7.2); the rejected branch stays fully
  synchronous — declining is genuinely final, nothing to queue. Found and
  fixed one accidental terminology inconsistency while building this:
  early drafts of `api/holds.py` and `docs/api_spec.md` spelled the deny
  outcome `"denied"`, while every other file in the system — Mission 6's
  `orchestrator/holds.py`/`orchestrator/flows.py` and, importantly,
  Mission 8's `bot/api_client.py`/`bot/approval.py` (the API's actual
  future caller) — uses `"rejected"` throughout. Confirmed via a full-
  codebase search that this was never a documented boundary translation,
  just an independent word choice made before Mission 8's vocabulary
  existed to check against; corrected to `"rejected"` end-to-end in both
  files rather than adding a translation layer.

## Mission 7 — Merge verification (Mission 8 landed mid-mission)

Between building the five small additive changes/`docs/api_spec.md` and
writing the `api/` test suite, Mission 8 (Telegram Frontend) was merged
into this branch by a separate collaborator. Verified before continuing:
full suite (481 tests at that point) showed one failure,
`tests/test_bot_telegram_client.py::test_run_polling_registers_handlers_then_polls`
— a pre-existing Mission-8 bug (`python-telegram-bot` 22.8's
`Application.run_polling` is a read-only attribute, so the test's own
`monkeypatch.setattr` fails), unrelated to Mission 7 or the merge, and
still present at the end of this mission — not fixed here, out of scope
(Mission 8's own test). `tests/test_architecture.py` and every shared
registration point (`ENTRY_POINTS`, `docs/allowed_calls.md`, CI) checked
clean — no duplicated or dropped entries, matching the same pattern the
Mission 5/6 merge review used. `git show --stat` on every relevant commit
confirmed no Mission 7 file was reverted, overwritten, or altered by the
merge. `.github/workflows/ci.yml` was missing a "Mission 7" step entirely
at merge time (not merge damage — simply not yet reached in this
mission's own build order); added as this mission's own step 6, above.

## Test-suite coverage audit — remediation (persistence concurrency, history/time_utils, bot wrong-layer tests, notification poll-loop exception path)

Not a new `work_plan.md` subtask — a full test-suite coverage audit
(Missions 1 through 8) identified several already-"done" subtasks whose
production code was real but whose own test coverage had gaps or a
wrong-layer pattern. Four fixes, against Missions 2, 5, and 8:

- **§2.9 concurrency (`persistence/sqlite_backend.py`)** — added
  `tests/test_persistence_sqlite_backend.py`, exercising the serialized-
  writer-thread design under real multi-threaded contention for the first
  time (every other persistence test drives it single-threaded): 25
  concurrent appends, 20 concurrent updates to distinct events, and a
  combined 15-writer/10-reader run against a real temp SQLite file (not
  `:memory:`). No bug found — confirmed clean across 5 repeated runs; the
  architecture's own safety claim (one writer, WAL-mode concurrent
  readers) held under contention.
- **`history/time_utils.py`** — previously only exercised indirectly
  through `history/scheduler.py`'s/`history/summarize.py`'s own tests.
  Added `tests/test_history_time_utils.py` (25 tests) covering
  `parse_timestamp`, `storage_timestamp`, `day_bounds`, `month_bounds`,
  `year_bounds`, `add_month`, `iter_days`, `iter_months`, `iter_years`
  directly: month/year rollover both directions, malformed-timestamp
  input (confirmed to raise plain `ValueError`, matching
  `history/extraction.py`'s existing `except (TypeError, ValueError)`
  catch — no new contract invented), a non-UTC-offset timestamp crossing
  a day boundary on conversion, `day_bounds`' documented behavior of
  using its input's own calendar date rather than re-normalizing to UTC
  itself, and leap-year correctness via `iter_days` across a leap and a
  non-leap February.
- **Bot wrong-layer tests (§8.4/§8.5)** — `bot/app.py::_on_callback_query`
  previously had exactly one handler-level test (the clarification
  happy path); every other case (approval dispatch, permission denial,
  the second-commander-conflict race, unrecognized/malformed
  `callback_data`) was only exercised at the
  `handle_clarification_answer`/`handle_approval_answer` level directly
  — the same bug class the §8.2 profile/settings fix already caught once.
  Added 8 tests to `tests/test_bot_app.py` going through the real handler
  (and, for malformed `callback_data`, through the real
  `_guarded(_on_callback_query)` composition `register_handlers` actually
  wires up) for all of these. Re-scanned the rest of `tests/test_bot_*.py`
  for the same pattern: `bot/entrypoint.py` is called through
  `_on_text_message`, a pure pass-through with no branching logic of its
  own, so it isn't at risk the same way; `bot/failures.py`/
  `bot/results.py`/`bot/precedent_notify.py` are only ever reached
  through `bot/notifications.py::dispatch_notification`, which already
  has its own full-coverage dispatch tests. Left
  `tests/test_bot_profile_commands.py`/`tests/test_bot_settings_commands.py`'s
  isolated-function tests in place rather than consolidating — they still
  cover input parsing/validation logic worth testing standalone, and
  handler-level coverage for the permission-check concern already exists
  in `tests/test_bot_app.py` from the §8.2 fix.
- **§8.4/§8.5/§8.6/§8.9/§8.11 notification poll loop** — read
  `bot/notifications.py::run_notification_poll_loop` in full: a non-
  `ApiNotImplementedError` exception from a poll iteration is logged and
  the loop continues to the next iteration, never re-raised. Only the
  `ApiNotImplementedError` branch had a test. Added
  `test_poll_loop_logs_and_continues_past_a_non_api_not_implemented_error`
  to `tests/test_bot_notifications.py`, using a fake client that raises
  `RuntimeError` on every poll, asserting the loop actually completed all
  3 configured iterations (the real resulting behavior) rather than just
  "no exception escaped the test."

Full suite: 634 passed, 0 failed. `tests/test_architecture.py` passes.

### 8.14 — Implement `resolve_user`
- **Status:** done
- **Deviations:** Lives in `api/users.py`, alongside 8.13 — both are
  `api/*` code, numbered under §8 per the user's own decision to fold
  Mission 8's dependency on these three endpoints back into Mission 8
  itself rather than a separate Mission 9. `GET /User/<identity>`,
  VIEWER-level (`view_history`) — the lowest-privilege read in the
  system, matching this subtask's own low-sensitivity reasoning. Response
  is `200 OK` even for an unregistered identity (`{"registered": false,
  "permission_level": null}`) — asking "is this registered" is the whole
  point, not an error case. `bot.api_client.UnimplementedApiClient
  .resolve_user` remains as the null-object fallback (see 8.15's entry
  below); `bot.http_api_client.HttpApiClient.resolve_user` is the real
  implementation, using `bot.api_client.BOT_SERVICE_IDENTITY` as
  `X-Identity` — this call has no specific Telegram user's identity to
  forward, unlike the hold-answer/message-submission calls.

### 8.13 — Implement the commander roster
- **Status:** done
- **Deviations:** `GET /Commanders` in `api/users.py`. Diverges from the
  original draft's open question ("whether a commander's Telegram
  identity alone is sufficient chat-routing information") — resolved: yes,
  by reading `bot/telegram_client.py`, whose `send_text`/`send_with_buttons`
  address a chat purely by `chat_id`, and this system's private-chat-only
  design means a commander's own Telegram identity already equals the
  chat_id to reach them at (no group chats anywhere in `bot/*`). No new
  storage needed beyond `list_users()` filtered to `permission_level ==
  "commander"`. New action name added to `auth.permissions
  .ACTION_REQUIREMENTS`: `view_commander_roster`, COMMANDER level — unlike
  most reads in this system, this one is commander-level, since it returns
  the full roster (comparable sensitivity to the "no user-list command"
  rule §8.2 already enforces on the bot side, here enforced server-side
  for the one real caller, the bot's own service identity).

### 8.12 — Implement the notification feed the bot polls
- **Status:** done
- **Deviations:** `GET /Notifications` in new `api/notifications.py`.
  Required a new persistence primitive, confirmed with the user before
  building: a `notification_log` table (migration 8 —
  `sequence_id INTEGER PRIMARY KEY AUTOINCREMENT, kind, event_id,
  created_at`) and one new read method on `PersistenceInterface`,
  `fetch_notifications_since(since: int) -> list[dict]`. No new abstract
  *write* method was added — `store_held_event` and `update_event`
  (`persistence/sqlite_backend.py`) both insert a `notification_log` row
  inside their own existing `_do`/commit, satisfying "same transaction as
  the state change" literally without widening the interface's write
  surface. Outcome-to-notification-kind mapping (a real design decision,
  not specified in advance): `succeeded`/`declined` → `job_finished`;
  `failed` → `job_failed`; `uncertain` → **both** `job_finished` and
  `uncertain_verdict` (two rows, two audiences — the original submitter
  and every commander); `closed_on_precedent` → **both** `job_finished`
  and `precedent_closure`, same reasoning. Endpoint is COMMANDER-level
  (`poll_notifications`) — narrower than the original draft text's
  per-notification-kind viewer/commander split, corrected after
  confirming there is exactly one real caller (the bot's own service
  identity, `docs/allowed_calls.md`: "bot calls only api"), which needs
  every kind to do its own fan-out. Cursor is caller-supplied and
  stateless server-side (`since` query param, `next_cursor` in the
  response) — no per-identity server state. `target_chat_ids` is
  populated per response entry (from the event's own `sender_identity`
  for `job_finished`/`job_failed`; empty for the four commander-facing
  kinds, which a caller resolves via `GET /Commanders` instead) — caught
  as a real gap while documenting the mapping (`JobResult` itself carries
  no identity field) and fixed before it reached a test. `reply_to_message_id`
  is always `None` — no column anywhere stores a Telegram message ID; a
  pre-existing gap, not introduced or closed here, documented in
  `docs/api_spec.md` rather than left as a hidden assumption.

### 8.15 — Build a real `HttpApiClient` and resolve the service-identity gap
*Not a `docs/work_plan.md`-numbered subtask — real work needed to close
Mission 8's own dependency on Mission 7 for good, done in the same round
as 8.12–8.14 per the user's explicit decision to build it now rather than
defer it.*
- **Status:** done
- **Deviations:** Service identity: `bot.api_client.BOT_SERVICE_IDENTITY
  = "bot-service"`, provisioned per deployment via `cli/user_admin` at
  COMMANDER level — the same bootstrap path as the first human commander,
  no code change to `cli/user_admin` needed. Calls with no specific
  Telegram user's identity in their own signature use this constant as
  `X-Identity`; calls that already carry one (`answer_clarification_hold`,
  `answer_approval_hold`, `submit_message`) use that real identity instead
  — required, not stylistic, so the API's own §7.9 check keeps checking
  the real acting person rather than always passing on the bot's
  commander-level access. One pre-existing structural gap this surfaced
  and documented rather than silently redesigned:
  `get_profile_view`/`get_profile_diff_status`/`get_settings_view`
  /`get_job_result`/`write_protocol`/`write_setting` carry no per-call
  identity parameter at all in `BotApiClient`'s Mission-8-era abstract
  signatures, so for these the bot's own client-side permission check
  (already in place, already tested) is the only genuine gate — the API's
  own check necessarily passes on the service identity's blanket access.
  Documented in `docs/api_spec.md`'s new "Service identity" section.

  `bot/http_api_client.py::HttpApiClient` implements all thirteen
  `BotApiClient` methods with real HTTP calls, built on `urllib.request`
  (wrapped in `asyncio.to_thread`) rather than adding `requests`/`httpx`
  as a new runtime dependency — neither is on this session's approved
  package list, and nothing about a JSON-over-HTTP client this size needs
  them. Built and tested one method at a time against a real running
  `api/*` server on a genuine OS-assigned TCP port
  (`tests/api_fakes.py::RunningApiServer`, a `werkzeug` dev server on a
  background thread — `app.test_client()` never opens a real socket, so a
  new helper was needed to actually prove `HttpApiClient`'s own `urllib`
  calls work end to end). Two real bugs this incremental testing caught
  and fixed before they reached a committed test: `submit_message` had
  initially forwarded the API's own `status` field into
  `MessageSubmissionResult.awaiting_approval`, which is never knowable at
  acknowledgment time by §8.3's own design (fixed to always `False`); and
  `answer_approval_hold`'s success path had initially forwarded the raw
  HTTP body's `"queued"`/`"declined"` text directly into
  `HoldAnswerOutcome.status`, which expects `"approved"`/`"rejected"`/
  `"resolved"` (fixed to translate explicitly by status code + body).

  `bot.api_client.UnimplementedApiClient` was **not** retired — kept
  deliberately: `tests/test_bot_notifications.py`'s own
  `test_poll_loop_survives_an_unimplemented_api_and_stops_after_max_iterations`
  genuinely needs a null-object double whose every method raises, to
  confirm the poll loop's `ApiNotImplementedError` branch degrades
  gracefully — a real, current, legitimate use, not leftover scaffolding.
  `bot/app.py::build_deps` now constructs `HttpApiClient` as the real
  default instead.

  New tests: `tests/test_api_notifications.py` (10),
  `tests/test_api_users.py` (7), `tests/test_bot_http_api_client.py` (15,
  every `HttpApiClient` method against `RunningApiServer`). Also fixed,
  while auditing `.github/workflows/ci.yml`'s explicit per-mission file
  list (not glob-based, contrary to an earlier assumption — checked
  rather than assumed, per instruction): three files from *earlier* in
  this session (`test_persistence_sqlite_backend.py`,
  `test_history_time_utils.py`, `test_api_app.py`) were also missing from
  their mission steps, silently covered only by the trailing "Full suite
  (drift check)" step. Added all six missing files to their correct
  mission steps, restoring the file's own stated invariant.

**Mission 8 — now genuinely complete, end to end.** Every bot-side
behavior was already correct and fully tested against the `BotApiClient`
interface before this round; what closed today is the interface's real
implementation. A bot built from this codebase today, pointed at a real
Telegram token and a deployment whose `bot-service` identity has been
provisioned via `cli/user_admin`, makes genuine HTTP calls to a genuine
running `api/*` process for every one of its thirteen operations — not a
mock, not a fake, not `UnimplementedApiClient`. Full suite: 676 passed, 0
failed. `tests/test_architecture.py` passes.

### Follow-up — closing the two known limitations flagged at the end of §8.15

*Not `docs/work_plan.md`-numbered subtasks — both were explicitly flagged
as known, real gaps at the end of the §8.15 round and approved for a real
fix (not documentation) in this follow-up pass.*

**Problem 1 — server-side permission enforcement for five `BotApiClient`
methods.**
- **Status:** done
- **Deviations:** `get_profile_view`, `get_settings_view`, `get_job_result`,
  `write_protocol`, and `write_setting` were defined in Mission 8's
  original `BotApiClient` interface with no parameter carrying the
  specific Telegram identity asking — `HttpApiClient` therefore had
  nothing to put in `X-Identity` for these five except
  `BOT_SERVICE_IDENTITY`, so the API's own already-correct §7.9
  `authenticate`/`require` checks in `api/protocols.py`/`api/system.py`
  /`api/jobs.py` (confirmed by re-reading all three in full — no `api/*`
  code changes were needed) were checking the bot's blanket
  commander-level access rather than the real caller's, for these five
  calls only. Confirmed the real identity was already available at every
  call site — `bot.users.resolve_caller` already resolves it — it just
  wasn't threaded through; two call sites (`bot/app.py`'s profile/settings
  `view` branches) were discarding the resolved `CallerContext` entirely
  after checking only whether it was `None`. Fixed by adding a
  `caller_identity` parameter to all five abstract methods (`bot/api_client.py`,
  `UnimplementedApiClient`), threading it through `bot/http_api_client.py`
  (into `X-Identity`, replacing `BOT_SERVICE_IDENTITY`),
  `bot/profile_commands.py`/`bot/settings_commands.py` (which already took
  a `caller` for their own client-side check on the two write functions,
  but never forwarded it), and `bot/app.py`'s two `view` branches (now
  capturing the `CallerContext` instead of discarding it).
  `get_profile_diff_status` was deliberately left unchanged — not one of
  the five named, carries no permission-sensitive content, no dedicated
  action key. Twelve new tests: per-method identity-forwarding assertions
  in `tests/test_bot_profile_commands.py`/`tests/test_bot_settings_commands.py`,
  four handler-level forwarding tests in `tests/test_bot_app.py` (mirroring
  how the original §8.2 audit fix was itself verified — through the real
  command handler, not an isolated function), and five new
  server-side-refusal tests in `tests/test_bot_http_api_client.py`
  confirming the real running API now genuinely returns `403` for a
  viewer identity on `write_protocol`/`write_setting` — not just that the
  bot's own client-side check would have caught it — plus confirmation the
  three reads still correctly succeed for a viewer.

**Problem 2 — `reply_to_message_id` had no defined source.**
- **Status:** done
- **Deviations:** The originating Telegram message's own ID was never
  captured anywhere — `bot/app.py::_on_text_message` read `update.message
  .text` but never `update.message.message_id`, so it was lost before
  `bot/entrypoint.py::handle_incoming_message` or `BotApiClient
  .submit_message` ever saw it, long before any later asynchronous
  job-result/failure reply (§8.9/§8.11, delivered via `TelegramClient
  .send_reply`) could reference it. Required a new persistence field to
  survive the gap between submission and a possibly much-later queued
  reply: `events.source_message_id` (nullable TEXT — null for a
  sensor-sourced event, which has no Telegram message to reference),
  added via migration 9 (idempotent, matching migration 6's own pattern,
  since `persistence/schema.py`'s `EVENTS_TABLE_DDL` was also updated
  directly for a fresh database, the same choice already made once for
  the summary tables' `event_index` column before migration 6 needed the
  same idempotency check). Threaded end to end: `bot/app.py` →
  `bot/entrypoint.py::handle_incoming_message` (new `message_id` parameter)
  → `BotApiClient.submit_message` (new `source_message_id` parameter) →
  `bot/http_api_client.py` (into the `POST /Msg` body) → `api/messages.py`
  (reads `body.get("source_message_id")`, optional) →
  `orchestrator.flows.begin_report`/`begin_request` (new optional
  parameter, default `None` — `api/events.py`'s sensor path is unchanged)
  → `history.write.InitialEventEnvelope` (new field) →
  `persistence.sqlite_backend` (`_EVENT_COLUMNS`/`_EVENT_IMMUTABLE_COLUMNS`).
  Read back in `api/notifications.py`: each `GET /Notifications` response
  entry now carries its own `reply_to_message_id` (alongside the existing
  `target_chat_ids`), populated from the originating event's
  `source_message_id` only for `job_finished`/`job_failed` — the two kinds
  ever delivered via `send_reply` — `null` for the other four kinds and
  for a sensor-sourced event. `bot/http_api_client.py::poll_pending_notifications`
  reads it off the response entry instead of hardcoding `None`.
  `orchestrator.flows.process_report`/`process_request`/`process_message`
  were deliberately left unchanged — they're the fully-synchronous
  all-in-one path `api/messages.py` explicitly avoids using (its own
  docstring: "exactly what §7.2 exists to avoid blocking a request on"),
  never reachable from the real bot-driven HTTP path this fix closes, and
  changing them would have rippled through 15+ existing
  `tests/test_orchestrator_flows.py` call sites for no functional benefit.
  Five new tests, including two built specifically to prove the fix
  (`tests/test_api_notifications.py::test_job_finished_and_job_failed_carry_the_real_originating_message_id`,
  `tests/test_bot_http_api_client.py::test_reply_to_message_id_survives_the_full_real_path_from_submit_message_to_the_notification`)
  using two deliberately distinct, distinctive fixture message IDs and
  asserting the exact value each notification carries, not just that some
  reply was sent — confirmed by a deliberate mutation (forcing the source
  to always return `None`) that both tests correctly caught before being
  reverted.

Full suite: 693 passed, 0 failed. `tests/test_architecture.py` passes.

## Mission 9 — Integration and Hardening

### 9.1 — Build the sensor simulator
- **Status:** done
- **Deviations:** `tools/simulator.py`, a standalone `urllib`-only CLI
  (`python -m tools.simulator --port ... --identity ...`) — no new
  runtime dependency, matching `bot/http_api_client.py`'s own choice.
  Not declared in `docs/allowed_calls.md`: nothing inside the system
  imports it, and it imports nothing from the system beyond the standard
  library — it's a plain HTTP client of `POST /Event`, authenticating as
  any other caller would with a pre-registered sensor identity. Real
  variety in generated text (5 templates each for fire/medical/
  unclassifiable, randomized location/severity) rather than one fixed
  string, so extraction is genuinely exercised — not natural-language
  generation, but enough to satisfy this subtask's own "reads like a real
  sensor report rather than a template" bullet short of a real model
  writing the reports. `--repeat-rate` reuses a small pool of
  recently-emitted classification/area pairs for precedent lookup
  matches; `--unclassifiable-rate` drives the clarification path;
  `--burst-size` sends N events with no inter-event delay before
  switching to `--rate`, for §9.19's later use. 11 tests in
  `tests/test_tools_simulator.py`, including three against a real running
  API (`tests/api_fakes.py::RunningApiServer`, extended with a public
  `.port` attribute for this) confirming a real run actually creates the
  right events, a burst sends with no delay, and an unregistered identity
  fails cleanly. Added a new CI step, "Mission 9 — Integration and
  Hardening".

### 9.2 — Run the end-to-end flow test
- **Status:** done
- **Deviations:** Found and fixed a real, previously-unwired gap before
  this subtask's own "confirm the trace ID connects every log record"
  bullet could honestly pass: `tools/tracing.py`'s `trace_context()`/
  `new_trace_id()` were fully built (§1.8) but never called anywhere in
  the real production code — `api/events.py`, `api/messages.py`,
  `api/holds.py`, `orchestrator/*` all only ever *read* `get_trace_id()`.
  Every real log record's `trace_id` was genuinely `""` before this. Fixed
  by generating one trace ID at each real ingestion/resumption point
  (`api/events.py::post_event`, `api/messages.py::post_msg`,
  `api/holds.py::post_clarify`/`post_approve`) and entering
  `trace_context(trace_id)` around both the synchronous prefix and the
  queued continuation's own closure — threaded through explicitly as
  data, not relying on contextvar propagation across the queue's
  background worker thread, which cannot inherit it automatically and
  which drains many different items over its lifetime (a copied context
  would be wrong for every item after the first). Confirmed with the user
  before fixing, since it touches real production code beyond what the
  subtask itself implied. A hold resumption gets its own fresh trace ID
  rather than continuing the original event's — that ID isn't persisted
  anywhere a much-later resumption could read it back from; extending
  trace continuity across a hold's pause would need a new persistence
  field and is out of scope here.

  `tests/test_integration_end_to_end_flow.py` drives one event through
  `POST /Event` against a real running API, confirms every stage's field
  is present on the event record (classification, risk level/reason,
  selected protocol/reason, precedent columns, every step's task/result,
  insight text, verdict), and captures real stdout JSON log output
  (`tools.logging_config.configure_logging`) to confirm every record
  produced during that one event's handling carries the same single,
  non-empty trace ID — including a real `step_start` record from
  `protocols/executor.py`. Verified the test actually catches the bug: a
  deliberate revert of the `api/events.py` fix made it fail exactly on
  the trace-ID assertion, confirmed, then restored.

### 9.3 — Test profile loading and validation
- **Status:** done
- **Deviations:** Most of this subtask's bullets were already thoroughly
  covered at the unit level (`tests/test_profile_loading.py`,
  `tests/test_profile_validation.py`, both pre-existing) — confirmed by
  reading both files in full rather than assuming, to avoid writing
  redundant coverage. `tests/test_integration_profile_loading.py` adds
  exactly the two bullets that weren't covered anywhere: an exhaustive
  "loads exactly its agents/protocols/event types/areas and nothing else"
  check (by name/set, not just a subset of fields), and the
  two-profiles-differing-only-in-model routing check — a genuinely
  different, profile-level concern from `tests/test_agent_adapter.py`'s
  existing adapter-level model-routing test (two real profile modules on
  disk via `tests.helpers.write_profile_module`, each constructing a real
  `ReferenceAgent` with its own model string, confirming each one's
  captured `llm` kwarg at the (mocked) crewai boundary matches its own
  profile, not the other's).

### 9.4 — Test profile isolation
- **Status:** done
- **Deviations:** `tests/test_integration_profile_isolation.py` (5 tests):
  two real `RunningApiServer` instances at once confirm genuinely separate
  ports and database file paths; events written under one never appear in
  the other's `fetch_events_range` or `search_precedents` (a real,
  no-model-call `HistoryQueryService.search_precedents`, not mocked);
  adding a user to one leaves the other's `read_user` returning `None`;
  two real `config.settings_store.SettingsStore` instances (not
  `tests/api_fakes.py`'s `FakeSettings` — the real production class, for
  a genuine settings-persistence isolation check) confirm changing one's
  risk threshold leaves the other's unchanged; and a full round trip
  through both real APIs at once (`POST /Event` via
  `tools.simulator._post_event`, reused rather than reimplemented)
  confirms each server's own event lands only in its own database.

### 9.5 — Test user administration
- **Status:** done
- **Deviations:** `tests/test_integration_user_administration.py` (2
  tests). The end-to-end "add the first commander, confirm they can
  approve a run" test required matching `cli.user_admin.main()`'s own
  independently-reloaded profile (it calls `load_profile(args.profile)`
  and opens its own persistence against *that module's* `DB_PATH`) to the
  exact same file `tests.api_fakes.build_context`'s `ApiContext` reads
  from — otherwise the two would silently write to two different
  database files that happen not to conflict, not a real end-to-end
  proof. Fixed by writing a disposable profile module (`tests.helpers
  .write_profile_module`) whose `DB_PATH` is set to exactly
  `build_context`'s own hardcoded path, matching
  `tests/test_api_protocols.py`'s `writable_profile_module` pattern. The
  "search the API and bot surfaces" bullet is now a real structural test
  over `app.url_map.iter_rules()` (not by inspection): every route whose
  path contains "user" or "commander" — including the two read-only
  lookups added after this subtask was first drafted — must expose only
  `GET`. The bot-surface half was already covered by
  `tests/test_bot_users.py::test_no_user_management_command_is_registered_by_the_bot`.

### 9.6 — Test ingestion parity
- **Status:** done
- **Deviations:** `tests/test_integration_ingestion_parity.py` — as
  refined, drives the sensor side through a real `POST /Event` and the
  "through Telegram" side through the real bot code path
  (`bot.entrypoint.handle_incoming_message` calling a real
  `bot.http_api_client.HttpApiClient` against a second real running API),
  not a second direct `POST /Msg`, since `tests/test_api_unified_ingestion.py`
  already proves that half. Confirms the same convergence fields §7.5
  requires, plus one the API-only test can't reach: the bot path's real
  `source_message_id` (Problem 2's fix) actually lands on the event
  record, while the sensor path's stays `None`.

### 9.7 — Test the clarification path
### 9.8 — Test protocol selection
### 9.9 — Test the approval flag
### 9.10 — Test message intent and human activation
### 9.11 — Test precedent lookup and closure
### 9.12 — Test task formulation and reformulation
### 9.13 — Run the protocol execution regression suite
- **Status:** done
- **Deviations:** Checked each subtask's bullets individually against the
  real, pre-existing test suite before writing anything new, to avoid
  redundant coverage. Every single bullet across these seven subtasks is
  already satisfied, bullet for bullet, by pre-existing tests:
  `tests/test_orchestrator_holds.py` (flagged/unflagged/ambiguous
  selection, commander bypass, viewer refusal, second-answer conflict,
  clarification/approval independence — §9.7/§9.9), `tests/test_orchestrator_precedent.py`
  (risk threshold, resolved/unresolved match, human-activation exemption,
  most-recent-match selection — §9.11), `tests/test_orchestrator_formulation.py`
  (per-agent tasks, tool filtering, precedent context, unclear-task
  rewrite, attempt-limit sharing — §9.12), `tests/test_orchestrator_judgment.py`
  and `tests/test_protocol_executor.py`/`tests/test_protocol_retry.py`
  (all three verdicts, tool-approval enforcement, judgment failure
  isolation — §9.13), `tests/test_orchestrator_intent.py`/`test_api_messages.py`
  (question/report/request routing, commander bypass scope, sender told
  which — §9.10), `tests/test_orchestrator_selection.py`/`test_api_holds.py`
  (clear match, high-risk tie-break, low-risk hold, commander-still-held
  ambiguity — §9.8). `tests/test_api_notifications.py`'s own
  `test_closed_on_precedent_produces_both_a_job_finished_and_a_precedent_closure_entry`
  (from the earlier §8.12-closure round) already satisfies §9.11's
  refined "confirm via a real `GET /Notifications` poll" bullet too.

  The one genuinely uncovered gap across all seven: restart-survival
  proven through the real `api/*` HTTP routes specifically — the
  pre-existing `tests/test_orchestrator_flows.py::test_a_held_event_resumes_correctly_after_a_simulated_restart`
  proves it at the orchestrator level (calling `resume_after_approval`
  directly) for an approval hold only. `tests/test_integration_hold_restart_and_flow.py`
  adds the missing piece: a real restart (a fresh `ApiContext`/
  `SQLitePersistence` against the same file, the old one's queue stopped
  and persistence closed first) resolved through the real
  `POST /Clarify`/`POST /Approve` routes, for both hold kinds — plus
  §9.7's own "events behind a held event continued processing while it
  waited" bullet, confirmed by submitting a holding event and a
  clean-match event back to back through the real API and showing the
  second completes while the first is still pending, not blocked and not
  abandoned.

### 9.14 — Test retry and idempotency
- **Status:** done
- **Deviations:** The first three bullets (idempotency blocking retry
  after a side-effecting tool acted, read-only retry to the limit, the
  limit read live) were already directly covered against
  `protocols.retry.execute_step_with_retry` in
  `tests/test_protocol_retry.py`; "keeps the successful steps' results"
  already covered at the rendering level in `tests/test_api_jobs.py`.
  `tests/test_integration_retry_exhaustion.py` adds the real,
  wired-together path: a crewai mock that always raises forces a genuine
  retry-exhaustion through the real executor (not simulated by writing an
  outcome directly), confirmed to produce a real `job_failed`
  `GET /Notifications` entry naming the right agent and reaching the
  right recipient, and confirmed the queue is not stuck — a second event
  submitted right after still reaches a real terminal outcome rather than
  staying queued forever.

### 9.15 — Test the question flow
- **Status:** done
- **Deviations:** Already fully covered — `tests/test_orchestrator_question_flow.py`
  covers every bullet at the orchestrator level (side-effecting tool
  never passed regardless of wording, multi-agent composition, a failing
  sub-agent not crashing the whole answer, the real History Agent routed
  through the query service). "Restriction holds for a commander too" is
  structurally guaranteed, not merely tested: `answer_question`'s real
  signature carries no caller-identity parameter at all, so there is no
  branch that could special-case a commander even if something tried.
  "Nothing was written to the event record" is already confirmed through
  the real API in `tests/test_api_messages.py::test_a_question_is_answered_directly_with_no_job`
  (`fetch_events_range(...) == []`). No new test needed.

### 9.16 — Test history accuracy over time
- **Status:** done
- **Deviations:** Multi-month fidelity and cross-level query assembly
  already covered by `tests/test_history_fidelity.py`/`tests/test_history_query.py`;
  downtime-gap backfill already covered by `tests/test_history_scheduler.py
  ::test_reconciliation_builds_bottom_up_and_is_idempotent`. Found one real
  gap: the existing `test_late_telegram_notification_wakes_only_for_existing_stale_day`
  only asserts the scheduler's wake event gets set — it never actually
  runs a reconciliation pass afterward to confirm real regeneration.
  `tests/test_integration_history_accuracy.py` adds that: seeds all three
  summary levels, appends a late-arriving Telegram event into the
  already-summarized day (with an advancing injected clock — a fixed one
  would make the regenerated daily summary's `generated_at` look no newer
  than the still-current monthly/yearly ones, so the upward staleness
  propagation this bullet exists to prove would never actually fire), and
  confirms `reconcile()` regenerates all three levels, with the daily
  summary's own `event_index` genuinely including the new event
  afterward — not just a re-stamped timestamp.

### 9.17 — Test profile editing and settings persistence
- **Status:** done
- **Deviations:** Four of six bullets already covered: "running system
  unchanged" (`tests/test_api_protocols.py`), "`GET /SYSTEM` reports a
  pending change" and "reject a profile-owned field"
  (`tests/test_api_system.py`), "changed threshold survives a restart"
  (`tests/test_settings_store.py::test_later_run_prefers_the_settings_file_over_profile_starting_values`).
  `tests/test_integration_profile_editing_and_settings.py` adds the
  remaining two: a protocol added through the real `POST /Protocol` is
  confirmed present, correctly shaped, and referencing a real constructed
  agent after a genuine profile reload (`profiles.loader.load_profile`
  called fresh, not simulated); and a risk-threshold change through the
  real `PUT /SYSTEM` is confirmed to change the very next event's own
  outcome (a risk score placed deliberately between the old and new
  threshold, so only the new value being in effect explains the result).
  Building the first test surfaced a real fixture mismatch worth noting:
  `tests.api_fakes.build_context`'s `ApiContext.deps.protocol_set` is
  always its own hardcoded fixture protocols, independent of whatever
  profile module `module_path` names — a disposable profile module used
  for a real protocol-write-then-reload test must still construct a real
  agent matching what that hardcoded protocol set's own participating
  agents expect, or reloading it fails validation for unrelated reasons.
  Not a product bug — `build_context` is a test fixture, working as
  designed — but a sharp edge worth documenting for whoever writes the
  next test combining a disposable profile module with `build_context`.

### 9.18 — Test permission enforcement
- **Status:** done
- **Deviations:** Every cell of the viewer/commander/unregistered ×
  resolve-hold/approve-run/edit-profile/change-settings matrix was
  already individually proven correct, scattered across
  `tests/test_api_holds.py`, `tests/test_api_protocols.py`,
  `tests/test_api_system.py`, `tests/test_bot_clarification.py`,
  `tests/test_bot_approval.py`, `tests/test_bot_profile_commands.py`,
  `tests/test_bot_settings_commands.py`, plus — per this subtask's own
  refined text — `tests/test_bot_http_api_client.py`'s real-`HttpApiClient`
  tests added this round for the five methods Problem 1 fixed. Checked
  each cell rather than assuming; found exactly one missing:
  `answer_approval_hold`'s viewer-refusal, never exercised through a real
  `HttpApiClient` (only through the fake in `tests/test_bot_approval.py`).
  Added it, plus one test confirming an *unregistered* identity is
  refused distinctly from a *registered-but-insufficient* one across two
  representative methods — surfacing a real, deliberate asymmetry worth
  making explicit: `answer_approval_hold`'s 401 becomes
  `HoldAnswerOutcome(status="unauthorized")` (no raise —
  `HoldAnswerStatus` already has a slot for it, per `docs/api_spec.md`'s
  own mapping table), while `get_profile_view`'s 401 raises
  `ApiRequestError` (no DTO slot exists there). My first draft of this
  test assumed both raised — the run caught it immediately.

### 9.19 — Test serial processing under load
- **Status:** done
- **Deviations:** `tests/test_integration_serial_processing_under_load.py`
  drives a real 25-event burst through the real simulator (§9.1) against
  a real running API, with a real (started) `SummaryScheduler` alongside,
  and a wrapper around the scripted main agent that detects any two
  overlapping `.process()` calls directly (a non-blocking lock
  acquire/release around each call) — the actual proof of one-at-a-time
  processing, not an inference from timing. Caught and fixed a wrong
  assumption while building it: `happy_path_agent()`'s extraction
  response is a single fixed canned value, so every event in the burst
  shares one classification/area — after the first genuinely runs, the
  rest correctly close on precedent instead of re-running the protocol,
  which is real, correct system behavior, not a lock error or lost
  write. The original assertion ("every event succeeds") was wrong for
  that reason and was fixed to accept either terminal outcome, while
  still asserting none is `"failed"`, the event count matches what was
  emitted exactly, and no overlap was ever detected.

### Two real bugs found and fixed during §9.19/§9.20's integration testing

*Not `docs/work_plan.md`-numbered subtasks — genuine, previously-unknown
correctness bugs, found while building §9.19's burst test and §9.20's
cost/latency review, confirmed with the user before fixing per this
session's standing rule (a real bug found while testing is reported, not
silently worked around).*

**Bug 1 — inconsistent timestamp formatting silently broke same-second
range comparisons.** `api/events.py`'s and `api/messages.py`'s own
`_now()` wrote `received_at`/`occurred_at` via a raw
`datetime.now(timezone.utc).isoformat()` — microsecond precision, a
`+00:00` suffix. Every range-query bound built through
`history.time_utils.storage_timestamp` (used throughout
`history/retrieval.py`) is whole-second precision with no suffix.
`occurred_at` is a plain SQLite TEXT column, compared lexicographically —
two different formats in the same column meant an event genuinely
earlier in the same second as a query's upper bound could sort *after*
the truncated bound string and be silently excluded from a match.
Reproduced directly: two sensor events landing in the same wall-clock
second (a real, likely outcome of exactly the burst load §9.19 tests)
failed to find each other as precedents in roughly 80–90% of repeated
runs. Fixed by making both `_now()` functions go through
`storage_timestamp` too, so every timestamp in the table shares one
comparable format. Initially imported it from `history.time_utils`
directly, which `tests/test_architecture.py` correctly caught as a
layering violation (`history.time_utils` is internal — only
`history.interface`/`history.query` are declared `history` entry
points); fixed by re-exporting `storage_timestamp` from
`history.interface` instead and importing it from there, matching that
module's own existing role as the package's write-path facade. Full
suite re-run confirmed clean after this correction —
`tests/test_architecture.py` passes.

**Bug 2 — same-second events still couldn't match after Bug 1's fix.**
Once every timestamp shared one whole-second format, two events
genuinely milliseconds apart could round to an *identical* truncated
string — and `history.precedent.find_precedents`'s window used a strict,
exclusive upper bound at the target event's own timestamp, so equal
still meant excluded. Confirmed with the user before fixing (a second,
distinct architectural question from Bug 1, not assumed to be covered by
the same fix). Fixed by widening `find_precedents`'s own window by
exactly one second — scoped to precedent search alone, not
`retrieve_range`'s general contract, which `history/query.py`'s plain
history queries also depend on and which was never shown to share this
problem (`history/retrieval.py`'s own day/month/year boundary chunking
uses `retrieve_range` directly and was not touched). Confirmed the
widening can never pull in a genuinely later event: anything a full
second past the target's own timestamp still falls outside the widened
bound, verified by a dedicated test.

Both fixes verified empirically, not just by re-reading the code: the
same 15-repetition real-time diagnostic that reproduced Bug 1 (12–13
failures per 15 runs) showed 0 failures in 15 runs after Bug 2's fix.
`tests/test_history_retrieval.py` (4 tests) is the deterministic version
of that same check — a same-second precedent is found, a genuinely later
event is not pulled in, `retrieve_range`'s own general contract is
unaffected, and both `_now()` helpers produce `storage_timestamp`-
compatible output.

### 9.20 — Review cost and latency
- **Status:** done
- **Deviations:** This subtask is a review, not a pass/fail correctness
  test, per its own bullets. `tests/test_integration_cost_and_latency_review.py`
  is the real instrumentation (a wrapper counting main-agent calls by
  matching each prompt's own distinguishing text, plus a separate
  insights-agent call counter) behind the actual measured findings
  written up in `docs/cost_latency_review.md`: 6 main-agent-shaped calls
  for a full run (extraction, risk, selection, formulation, execution,
  judgment) plus 1 insights-agent call, versus 3 for a precedent closure
  (a 40%+ reduction, skipping formulation/execution/insights/judgment
  entirely); wall-clock latency is structural only, given every model
  call is mocked; the clearest remaining merge opportunity is precedent
  lookup and the Insights Agent's own comparison both reading comparable
  history independently for the same event, confirmed by reading both
  call sites directly. Building this review's own instrumentation is
  what surfaced Bugs 1 and 2 above — a genuinely different, valuable
  outcome from what this subtask's own bullets asked for, not something
  this pass went looking for.

### 9.21 — Set up deployment
- **Status:** done
- **Deviations:** `api/app.py` had no runnable launch entry point at
  all — confirmed by checking for one before assuming it existed. Added
  `api.app.main(argv)` (`python -m api.app <profile_module>`), matching
  `bot/app.py`'s own `main()`/`if __name__ == "__main__"` pattern
  exactly: every host, port, and path comes from the named profile,
  never a flag, except `--host` itself (a deployment concern the profile
  has no opinion on, defaulting to `127.0.0.1`). Runs Flask's own
  `app.run()` — deliberately not a production WSGI server, matching
  "package... to run on localhost for the demonstration" precisely and
  leaving everything `docs/PRODUCTION_READY.md` covers out of scope.
  `tests/test_integration_deployment.py` (2 tests) uses the *real*
  `api.app.build_context`/`build_app` — not `tests/api_fakes.py`'s test
  double — to prove the actual startup wiring: a genuinely empty
  directory, `cli.user_admin` triggering migrations and creating the
  database from nothing, the system then serving a real request; and two
  full deployments (separate ports, separate database files, separate
  profile modules) running side by side from the same build, each
  correctly refusing the other's commander identity.

### 9.22 — Write operator documentation
- **Status:** done
- **Deviations:** `docs/operator_guide.md` — written to cross-reference,
  not duplicate, the existing docs that already cover part of this
  ground in full (`docs/profile_spec.md` for every name the loader
  expects, `docs/agent_authoring.md` for adding an agent,
  `docs/api_spec.md`'s "Service identity" section for the bot-service
  provisioning step). Covers what wasn't consolidated anywhere yet: the
  approval flag's exact meaning and the commander-bypass rule, `cli
  .user_admin`'s exact CLI syntax, the three unprompted message types by
  their real header text (read directly from `bot/formatting.py`'s
  `_HEADERS`, not guessed), the three live settings' immediate-effect
  behavior versus a profile edit's restart-required one, and reading logs
  by trace ID — including the accurate detail that a hold's resumption
  gets its own fresh trace ID, not a continuation of the original event's
  (Bug 1/2's own fix work, §9.19/§9.20, made this fully honest to state).
  The bot-service identity section is deliberately the most prominent —
  it's the one step a deployment can silently skip and appear to have
  started correctly regardless. `docs/work_plan.md`'s own Branch Grouping
  table pointed at a `docs/operations` target that was never the actual
  filename chosen; corrected to `docs/operator_guide.md`.

## Mission 9 — complete

All 22 subtasks (§9.1–§9.22) done. Two real, previously-unknown
correctness bugs found and fixed along the way (see the dedicated entry
above), one real architectural layering violation caught by
`tests/test_architecture.py` and corrected immediately, and one real,
concrete production-readiness gap closed that had no prior test coverage
at all (`api/app.py` had no runnable launch entry point before §9.21).
Every new integration test drives real components — a real running API
server (`tests/api_fakes.py::RunningApiServer`), the real simulator, the
real bot code path, or the real `cli.user_admin`/`api.app` entry
points — not fakes standing in for them, matching this mission's own
purpose.

---

## docs/server_report.md audit — remediation (2026-08-25)

A full verification audit (`docs/server_report.md`) was run against the
whole codebase and this log. It confirmed the large majority of this
log's own claims hold, and surfaced three findings acted on below —
append-only entries, per this file's own rule; none of the entries they
touch were edited or removed.

### 1.5 — superseded: core-agent construction is no longer a stub
- **Status:** superseded, see below
- **Deviations:** The original 1.5 entry (above) correctly described the
  state at the time it was written — `profiles/loader.py
  ::_construct_core_agents` was a documented no-op seam returning `{}`,
  since the Agent Framework didn't exist yet. That stopped being true
  during Mission 6: `orchestrator.flows.assemble_core_agents`
  (`orchestrator/flows.py`, added in the "Merge remediation" entry above)
  genuinely merges the Main, History, and Insights agents, and
  `api/app.py::build_context` calls it as part of real startup wiring —
  confirmed by direct reading during the audit, not assumed. The original
  1.5 entry is left unedited per this log's append-only rule; this entry
  is the pointer for a reader who lands on it and wonders whether the
  limitation is still current. It is not.

### 4.6 — superseded: retry exhaustion handling is now fully wired
- **Status:** superseded, see below
- **Deviations:** The original 4.6 entry (above) says "partially done —
  executor-level behavior only," explicitly deferring three things to
  missions that didn't exist yet: writing partial results onto the event
  record, notifying the event's originator, and moving on to the next
  event. All three are real today, confirmed during the audit: 
  `orchestrator/flows.py`'s `_run_protocol` calls `record_step_execution`
  inside the step loop (partial results land incrementally, not only at
  the end) and `record_event_outcome(..., "failed", ...)` on exhaustion
  (§6.11); `bot/failures.py` plus the `job_failed` notification kind
  (`api/notifications.py`, §8.11/§8.12) deliver the originator
  notification; the serial queue (§6.15) already guarantees the next
  event proceeds. `tests/test_integration_retry_exhaustion.py` (§9.14)
  exercises this exact path end to end. The original 4.6 entry is left
  unedited per this log's append-only rule; this entry is the pointer.

### 1.8 — completed: the specific named events are now logged
- **Status:** done
- **Deviations:** The original 1.8 entry (above) — "partially done...
  those call sites land with §5/§6" — was never actually followed up:
  the audit found, by direct grep, zero `logger.*` calls in `orchestrator/`
  and zero in `history/` as of 2026-08-25, six missions after that
  promise was made. This entry closes that gap for real. Added, all
  through the existing `tools/logging_config.py`/`tools/tracing.py`
  machinery (no second logging mechanism, no new dependency):
  - `intent_classified` — `api/messages.py::post_msg`, the real
    production call site (not `orchestrator.flows.process_message`,
    which is never called in production — see the 8.15-adjacent
    dead-code entry below).
  - `extraction_result` (naming which fields came back empty),
    `risk_assessed`, `protocol_selection`, `hold_created` (clarification
    or approval, with which), `precedent_closure`, `insight_generated`,
    `final_verdict`, and a new umbrella `event_outcome` fired on every
    terminal branch (closed on precedent, declined, failed, succeeded,
    uncertain) — all in `orchestrator/flows.py`, at the point each
    decision is made, since that module has both the event ID and the
    full decision context together.
  - `precedent_lookup` (the window searched, the target/classification/
    area, and which prior events matched) — `history/precedent.py
    ::find_precedents`, the function that actually computes the window.
  - `tool_call` for a *successful* tool invocation — `agents/base.py`.
    `tool_blocked` already existed and needed no change; the gap was
    specifically that only the blocked branch was ever logged.
  - `step_start`/`step_result` (task text and result respectively) and
    `step_failed`/`step_retry`/`step_unclear` — confirmed already present
    and correct in `protocols/executor.py`/`protocols/retry.py`; nothing
    added, no duplication.
  - Level convention: every new call site is INFO, matching every
    pre-existing one in this codebase (`step_start`, `tool_blocked`,
    `step_retry`, etc. are all already INFO) — kept consistent with the
    established convention rather than introduce a new DEBUG/WARNING
    split that would leave old and new call sites inconsistent with each
    other, a deliberate choice confirmed rather than assumed.
  - Six new tests (`tests/test_agent_permission_enforcement.py`,
    `tests/test_history_precedent.py`, `tests/test_orchestrator_flows.py`,
    `tests/test_integration_end_to_end_flow.py`) drive a real event or
    message through the real flow, capture actual JSON log output, and
    assert the specific values a run produced (which risk level, which
    protocol, which fields were empty) appear under that run's own trace
    ID — not just that the formatter works, which `tests/test_logging.py`
    already covered and continues to cover unchanged.
  - `docs/operator_guide.md`'s "Reading the run logs" section corrected:
    it previously implied every relevant record (including step/tool
    records) carries `event_id`; that's true for the decision-level
    events above but not for `step_start`/`step_result`/`tool_call`,
    which carry the agent/step/tool and the shared `trace_id` instead —
    the guide now lists every event name that actually fires and states
    the real correlation path (find any `event_id`-bearing record, read
    its `trace_id`, filter the whole stream to that value) accurately.

  **The Mission-status table at the top of this file and this entry now
  agree**: Mission 1 is genuinely "Done," including 1.8 — confirmed by
  re-running the full suite and `tests/test_architecture.py` after this
  fix, not merely asserted.

### Dead code — `orchestrator.flows.process_report`/`process_request`/`process_message` reviewed, kept intentionally
- **Status:** done (documentation only, no functional change)
- **Deviations:** The audit found these three functions fully built and
  exercised by ~20 test call sites in `tests/test_orchestrator_flows.py`,
  but never called from any production entry point — `api/events.py` and
  `api/messages.py` use the split primitives (`begin_report`/
  `run_report_extraction`, `begin_request`/`continue_from_risk_assessment`)
  instead, required by §7.2's async-job design. Reviewed and a decision
  made explicitly (not assumed): **keep them**, since they are the
  orchestrator package's own deliberate synchronous test-facing
  composition — letting `tests/test_orchestrator_flows.py` exercise
  full-flow logic in one call, independent of `api/`'s queueing concerns
  — not leftover scaffolding, and deleting them would mean rewriting
  ~20 passing tests onto the split-primitive pattern for no functional
  benefit. Each of the three functions' own docstrings now states this
  plainly (not used in production, names exactly what `api/*` uses
  instead and why), so a reader who finds one by searching the codebase,
  not just one who reads the module's opening docstring, sees the same
  answer. No test was changed; `tests/test_orchestrator_flows.py`'s 22
  tests pass unchanged.

Full suite after all three remediations: 736 passed, 0 failed (was 732;
+4 new test functions, 2 existing tests extended in place with additional
assertions). `tests/test_architecture.py` passes.
`.github/workflows/ci.yml`'s per-mission file-coverage invariant checked
mechanically and still holds — no new test files were created this pass,
only existing ones extended, so no CI change was needed.

---

## Follow-up to the §1.8 remediation above — debug-gated model I/O logging, noisy internals moved off INFO

*Not a `docs/work_plan.md`-numbered subtask — real work, requested directly,
building on the §1.8 remediation logged above: that pass added the eleven
always-on decision events; this pass adds the layer beneath them (the exact
prompt sent and raw response received) and moves genuinely noisy internal
detail off INFO, gated behind the same mechanism.*
- **Status:** done
- **Deviations:**
  - **One flag, not two** (an explicit choice, not a default): a single
    `DEBUG_VERBOSE_LOGGING` environment variable gates both the model I/O
    logging and the internal-detail demotion below, rather than one flag
    per concern. Chosen because an operator diagnosing a live-model
    problem — the scenario this exists for — almost always wants both at
    once (the internal detail is frequently what explains *why* a given
    prompt/response pair produced the outcome it did); two flags would add
    configuration surface for a case where splitting them apart has no
    real use found.
  - Read from the environment directly (`os.environ.get("DEBUG_VERBOSE_LOGGING")`,
    `config/base.py`, module-level, once at import time) — matching
    `profiles/loader.py`'s own existing mechanism for the environment
    variables a profile names, since no `.env`-file loader exists anywhere
    in this codebase to instead plug into. Parsed strictly
    (`config.base._parse_debug_flag`): only `"1"`/`"true"` (any case,
    optionally padded) mean on; unset, empty, `"false"`, `"0"`, or any
    other text mean off — never a bare "any non-empty string is truthy"
    check. Confirmed no conflict with §1.6's profile validation: that
    validation only ever inspects a `LoadedProfile`'s own declared
    environment variables (`BOT_TOKEN_ENV`, `MODEL_CREDENTIAL_ENVS`);
    `DEBUG_VERBOSE_LOGGING` is never profile-declared and never a
    *required* variable, so its absence is never a validation failure —
    confirmed by reading `profiles/validate.py` in full, which contains no
    environment-variable handling of any kind. No `.env.example` or
    equivalent exists anywhere in this repository to document it in
    (checked, not assumed) — noted here rather than created, since that's
    `docs/PRODUCTION_READY.md`'s own separate scope, not this task's.
  - `agents/adapter.py::invoke` confirmed as the one real choke point
    every agent call passes through (`agents.base.Agent.process` →
    here) — reused, not duplicated: `history/extraction.py` used to make
    its own separate `log_ai_interaction` call for the extraction model
    call, which (in production, via `orchestrator.flows._model_invoker_for`)
    always already routed through this same choke point a second time
    under a different tag — a real, pre-existing duplication, not
    introduced by this pass. Removed the separate call; `extract_event`
    now wraps its `model_invoker(prompt)` call in
    `tools.tracing.stage_context("extraction")` instead, so the one real
    logged interaction carries the right stage tag without a second log
    call.
  - **Stage tagging**: `agents.base.Agent.process(text, allowed_tools)`
    (§3.1) is deliberately the only public entry point every caller
    reaches an agent through — adding a "stage" parameter to it would mean
    touching that contract and every one of its ~15 call sites for a
    logging concern alone. Used a contextvar instead
    (`tools.tracing.stage_context`/`get_current_stage`, the same shape as
    `trace_context`, and the same reasoning `agents/base.py`'s existing
    `_current_allowed_tools` contextvar already applies to `allowed_tools`).
    Each orchestrator-level call site wraps its own `agent.process(...)`
    call: `intent_classification`, `risk_assessment`, `protocol_selection`,
    `task_formulation`, `task_rewrite`, `success_judgment`,
    `insight_generation`, `extraction`, `step_execution`
    (`protocols/retry.py`), and the question flow's three internal calls
    (`question_routing`, `question_subagent`, `question_history_query`,
    `question_composition`). `agents/adapter.py::invoke` reads it back via
    `get_current_stage()` when building a debug-gated log record — no
    signature threaded through the agent framework itself.
  - **Payload construction, not just emission, is gated**: `interaction_payload`
    (the full CrewAI invocation envelope — role, goal, backstory, model,
    tools, and the full task text) is only ever built inside
    `if verbose_logging_enabled():` in `agents/adapter.py::invoke` — never
    built and then discarded. `tools.logging_config.log_ai_interaction`
    itself keeps its own internal `DEBUG_FLAG` check too, as
    defense-in-depth for any other caller, but the expensive part (the
    `json.dumps` call) never runs when the flag is off regardless.
  - `log_ai_interaction` itself was rewritten from `print()` to a
    structured `logger.debug(...)` call through the same JSON formatter
    and trace-ID mechanism as every other log record (§1.8's own "not a
    second logging mechanism" rule) — a real behavior change from the
    Mission 1 `DEBUG_FLAG`-backed version (`docs/progress.md`'s "1.3 / 3.5
    — follow-up" entry, above), required by this task's own explicit
    instruction to use the existing structured logging setup rather than
    leave the print-based one in place. `tests/test_history_logging.py`
    rewritten to match (was asserting on `capsys` stdout text; now asserts
    on `caplog` structured records) — the old assertions no longer apply
    to the new, intentionally different implementation.
  - `configure_logging`'s `level` parameter now defaults to `None`,
    meaning "compute from `config.base.DEBUG_FLAG`" (`DEBUG` when on,
    `INFO` otherwise) rather than always `INFO` — read once, at the single
    point logging is configured, not per log call; an explicit `level`
    still overrides. This is what actually makes the flag take effect: the
    DEBUG-level records this pass adds are otherwise silently dropped by
    Python's own logger-level check before ever reaching the JSON
    formatter, regardless of any per-call guard.
  - **A real, pre-existing gap found and fixed while wiring this in**:
    `api/app.py::build_context` — the function every real `python -m
    api.app` process runs — never called `configure_logging` at all,
    confirmed by a repo-wide search before assuming otherwise; only
    `bot/app.py` did. Without this fix, `DEBUG_VERBOSE_LOGGING` (and,
    more importantly, every one of §1.8's own INFO-level events) would
    have had no real effect on the API process specifically, which is
    where nearly every one of those events is actually produced
    (extraction, risk assessment, selection, holds, precedent, insight,
    judgment all run inside API-triggered flows, not inside the bot
    process). Fixed by adding the same `configure_logging(loaded_profile.module_path)`
    call `bot/app.py` already makes, right after profile loading in
    `build_context` — `bot/app.py`'s own existing call needed no change,
    since it already omits an explicit `level` and picks up the new
    default automatically.
  - **Noisy internals moved to DEBUG** (only these two; nothing was moved
    off the always-on INFO list, per this task's own explicit
    instruction): (1) `agents/base.py`'s successful, allowed tool-call log
    (`tool_call`) — `tool_blocked` is unchanged, still INFO. (2)
    `history/precedent.py::find_precedents`'s own `precedent_lookup`
    record (the exact window boundaries and raw candidate list) — the
    outcome an operator actually needs ("did anything match, did it close
    the event") is `orchestrator.flows.continue_from_risk_assessment`'s
    `precedent_closure` record, at INFO, unchanged, added in the §1.8
    remediation above. No other candidate from this task's own list
    (extraction per-field detail, queue/scheduler state transitions)
    turned out to have any existing logging to move — confirmed by reading
    `orchestrator/queue.py` and `history/scheduler.py` directly: their
    only `logger` calls are `logger.exception(...)` on an actual failure,
    which stay as they are (errors, not routine internal detail), and
    extraction's own per-field detail was never logged separately from the
    already-INFO `extraction_result` summary the §1.8 remediation added,
    so there was nothing further to demote there.
  - Ten new/rewritten tests across `tests/test_base_config.py` (strict
    flag parsing, including the exact `"false"`/`"0"` case requested, and
    a real read-from-environment round trip via `importlib.reload`),
    `tests/test_history_logging.py` (rewritten for the structured-logging
    behavior, plus a new stage-context-default test),
    `tests/test_agent_permission_enforcement.py` and
    `tests/test_history_precedent.py` (updated to assert DEBUG, not INFO,
    for the two demoted events), and two new integration tests in
    `tests/test_integration_end_to_end_flow.py` driving a real event
    through a real running API with the flag explicitly off and explicitly
    on: off confirms no `model_io` record appears and every §1.8 decision
    event is still present and unaffected; on confirms `model_io` appears
    for every real model call (using real `MainAgent`/`InsightsAgent`
    instances with a keyword-dispatching fake CrewAI kickoff, since the
    fixture used by every other test in that file,
    `tests.api_fakes.happy_path_agent`, is itself a fake that never
    reaches `agents/adapter.py::invoke` and so can't exercise this),
    tagged with the right stage per call, carrying the exact same
    non-empty `trace_id` as every INFO record from the same run.
  - `docs/operator_guide.md`'s "Reading the run logs" section rewritten:
    split into the always-on INFO tier and the `DEBUG_VERBOSE_LOGGING`
    tier (naming `model_io` and its stage vocabulary explicitly, and the
    two records moved off INFO), with the exact environment variable name,
    how to set it, and an explicit sensitivity warning that its output can
    contain the full original event/message text and should not be left
    on in normal operation.

Full suite: 741 passed, 0 failed (was 736; +5 net new test functions).
`tests/test_architecture.py` passes. `.github/workflows/ci.yml`'s
per-mission file-coverage invariant checked mechanically and still holds —
no new test files were created this pass, only existing ones extended, so
no CI change was needed.
