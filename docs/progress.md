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
| 2 | Data Layer (2.1–2.12) | Done |
| 3 | Agent Framework (3.1–3.12) | Done |
| 4 | Protocol Engine (4.1–4.8) | Done |
| 5 | History System | Not started |
| 6 | Main Agent Orchestration | Not started |
| 7 | API Layer | Not started |
| 8 | Telegram Frontend | Not started |
| 9 | Integration and Hardening | Not started |

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
