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
| 4 | Protocol Engine | Not started |
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
