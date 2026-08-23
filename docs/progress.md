# Progress Log

Append-only running record of what was actually built for each subtask in
`docs/work_plan.md`, versus what the work plan originally specified. One
entry per subtask, added in completion order (per `instructions.md` §6).
Never edit or remove a prior entry — if a subtask is revisited later, add
a new entry describing what changed instead.

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
