# AgentsHub — System Investigation & Improvement Findings

Investigation pass performed against `investigation_and_improvement_plan.md`
(handed to Claude Code as a read-only work order). This file is the sole
deliverable of that pass — every other file in the repository was left
unmodified; see the confirmation at the very end of this document.

**Investigation date:** 2026-09-04

**Method note:** every item below is grounded in either (a) reading the real,
current source file(s) named, or (b) a live reproduction run against an
isolated copy of `profiles.demo` (same protocols/agents/areas/event types,
scratch SQLite DB, port 8977, real OpenRouter model calls, `DEEP_DEBUG=true`)
— never against the deployment's actual `data/demo_profile.db`, which was
confirmed untouched throughout (its file modification time predates this
investigation and is unchanged after it). Where a live trace was captured,
its trace ID and the exact evidence are quoted below.

---

## Executive Summary

Ranked by priority. Items not listed as "confirmed cause" or "contributing
factor" are summarized only; see their own section for the full entry.

| Priority | Item | Verdict |
|---|---|---|
| **Critical (safety)** | **A.7** — situation-picture false-safe | **Confirmed cause**, reproduced live with full trace. "Is everything ok" routes to a live specialist tool check (`reference_agent.check_status`) and never consults event history at all — the routing model's own stated reason was *"No historical lookup is needed as no past events are referenced."* An active, unresolved fire report was answered as "✅ Nominal... green across the board." |
| **Critical** | **Bonus finding (filed under C.1)** — report/request intent classification hard-fails ~consistently | **Confirmed cause**, reproduced live twice in a row with full trace. The legacy intent-classification prompt's JSON example hardcodes the evidence key `"question"`; the model copies that literal key even when the real intent is `report`/`request`, and the parser's own validation (`primary_intent not in evidence`) then rejects it — turning ordinary field reports into a hard `422 run_failure` visible to the end user. |
| High | **A.2** — timestamp semantics for "most recent" | **Confirmed cause.** `answer_most_recent_event` orders/filters on `occurred_at` (not receipt time) by default; for human/Telegram reports `occurred_at` is model-extracted and can resolve to `None`, and `search_events` unconditionally excludes any event with a `NULL` value in the ordering column — so an event whose timestamp the model couldn't parse becomes permanently invisible to every recency/history query, with no hold and no operator-visible signal. |
| High | **A.1** — recency vs. reference-resolution routing | **Not confirmed as a cause.** Read both the direct-lookup classifier and the full question-routing prompt: both already explicitly instruct the model to treat "last/latest/most recent" as a fresh query, distinct from a reference like "that event." The plan's "prime suspect" turned out, on inspection, not to be the mechanism — A.2 is. |
| High | **D.3** — LLM/client cache coverage | **Confirmed: the cache is currently inert for the deployed configuration.** `thread_safe_client` is `False` by dataclass default and is never set `True` for any provider except an explicit `False` for `"openai"` — so for the actually-configured `openrouter` provider (and every other provider), Stage 10's reuse cache never activates. **Reviewed 2026-09-04: stays inert, deliberately — `openrouter` shares CrewAI's `OpenAICompletion` class with `openai`, which has a genuine unsynchronized shared-mutable-state race (`_last_response_id`/`_last_reasoning_items`); see the entry's addendum.** |
| Medium-High | **D.1/D.2** — model-tier assignment and sequential calls | No misassignment found. One full report takes **7 sequential model calls, ~39.4s of pure model latency** in a live-measured run (`extraction 3.0s → risk_assessment 3.0s → protocol_selection 3.4s → task_formulation 3.9s → step_execution 11.2s → insight_generation 9.1s → success_judgment 5.7s`). The already-built `operational_decision_mode="merged"` would collapse two of these into one call; it is not the active default. |
| Medium | A.3 (rolling-summary leakage) | Not observed in the live run; routing logic appears to keep it separate — see entry for the specific check performed. |
| Medium | A.4 (conversation-memory re-read guard) | Confirmed present at the code level for the path actually exercised — no caching layer between the guard and the DB. |
| Low-Medium | A.5 (caching/connection staleness) | The write path is fully synchronous/blocking before any read in the same causal chain — no staleness risk found there. The queue-timing sub-question (asking "what's the last event" while it is still queued) is a real, expected async-timing effect, not a bug. |
| Needs more investigation | A.6, B.1–B.3, C.2–C.4, D.4, D.5 | See entries — grounded in code reading, not independently reproduced live in this pass. |

**Recommended order to act on this document:** A.7 first (safety), then the
report/request-classification hard-fail (files under C.1 below, but is
arguably as urgent as A.7 since it can break the system's core reporting
function), then A.2, then D.3, then the rest per the plan's own Part E
ordering.

---

## Part A

### A.0 — Reproduce and capture evidence first

- **File(s)/function(s) examined:** `api/app.py` (`build_context`, `build_app`,
  `main`), `profiles/demo.py`, `cli/user_admin.py`.
- **What the code actually does:** Ran a real, isolated instance of the demo
  profile's configuration (same `PROTOCOLS`/`AGENTS`/`EVENT_TYPES`/`AREAS`,
  scratch DB, port 8977) with `DEEP_DEBUG=true`, `python -m api.app
  investigation_repro`. Registered `commander-repro` (commander) and
  `viewer-repro` (viewer) via `cli.user_admin`. Submitted two `POST /Event`
  sensor reports four seconds apart, then asked "what is the last event?"
  via `POST /Msg`.
- **Verdict:** confirmed cause (deliverable produced; findings below).
- **Evidence:** Two events persisted with `occurred_at` = `received_at`
  (`2026-09-03T23:41:43` and `...:47`, sensor source). The question correctly
  identified event `9c9bee55b1cd4231b47b987e153d824f` (the true most recent)
  — this specific two-sensor-event case did **not** reproduce a stale
  answer, which is itself useful evidence: it shows the bug is not in the
  basic "newest wins" comparison, narrowing the search toward A.2's
  timestamp-resolution mechanism (below), which only applies to
  Telegram/human-sourced reports, not sensor events.
- **Proposed fix:** N/A — reproduction step only.
- **Suggested regression test:** N/A.
- **Priority:** N/A (infrastructure step).

### A.1 — Check how "recency" questions are classified

- **File(s)/function(s) examined:** `orchestrator/reasoning.py` —
  `_build_direct_lookup_prompt`, `_is_direct_most_recent_lookup`,
  `answer_question` (lines ~1086–1103, ~1495–1520); `_build_agent_selection_prompt`
  (captured live via `DEEP_DEBUG` model_io for trace
  `bf52e547f9ed437d9dc84ce2e12a30c5`).
- **What the code actually does:** `answer_question` (the live path for
  `planner_mode="legacy"`, which is what `profiles.demo`'s unmodified
  `OptimizationPolicy()` uses) first runs a dedicated classification call
  (`_build_direct_lookup_prompt`) whose entire job is to separate "most
  recent event" lookups from references to something already discussed. Its
  prompt text explicitly states: *"questions like 'what is the last event',
  'what just happened'... as opposed to... A question referring back to a
  specific event already discussed earlier in this conversation ('that
  event', 'the first one', 'what happened after that?') is not a
  most-recent-event lookup even if it sounds similar — route it normally
  instead."* The full question-routing prompt (used when this isn't a direct
  lookup) carries the same instruction: conversation context is to be used
  *only* to resolve which stable Event ID a demonstrative reference means,
  never to answer a fresh recency question.
- **Verdict:** not an issue (the plan's "prime suspect" is not confirmed).
- **Evidence:** Both prompts, read verbatim from the current source, already
  and explicitly draw the distinction the plan worried was missing. The live
  reproduction's recency question was also answered from a fresh query
  (not a remembered turn) with no conversation history present to even
  trigger the reference path.
- **Proposed fix:** None needed for this item specifically. If future work
  wants to harden this further, add the bilingual regression-test set B.1
  describes to *prove* the distinction holds under adversarial phrasing
  rather than trusting the prompt text alone.
- **Suggested regression test:** See B.1.
- **Priority:** low (already correct on inspection; worth a regression test
  to keep it that way).

### A.2 — Check the actual SQLite query construction for history questions

- **File(s)/function(s) examined:** `history/query.py`
  (`HistoryQueryService.answer_most_recent_event`, lines 543–583),
  `persistence/contracts.py` (`EventSearchCriteria`, lines 17–33),
  `persistence/sqlite_store.py` (`_search_where`, lines 215–243;
  `search_events`, lines 470–488), `history/event_pipeline.py`
  (`extract_event`, lines 12–141), `orchestrator/flows.py`
  (`run_report_extraction`, lines 199–243), `orchestrator/holds.py`
  (`determine_clarification_hold`, lines 129–132).
- **What the code actually does:**
  1. `answer_most_recent_event` builds `EventSearchCriteria(time_end=storage_timestamp(now),
     sender_identity=..., order="newest", limit=1)` **without** setting
     `time_basis`, so it uses the dataclass default, `"occurred_at"` — never
     `received_at`/ingestion time.
  2. `_search_where` turns this into SQL: `occurred_at IS NOT NULL AND
     occurred_at < ?` (strict less-than, confirmed by reading the code — not
     `<=`) `ORDER BY occurred_at DESC, event_id DESC LIMIT 1`.
  3. `extract_event`'s timestamp rule (`history/event_pipeline.py:109`) is
     `occurred_at = received_at if source == "sensor" else model_occurred_at`
     — for a **sensor** event, `occurred_at` is always the true receipt
     time (this is why A.0's sensor-only reproduction didn't show the bug).
     For a **Telegram/human** report, `occurred_at` is instead whatever the
     extraction model resolved from the message text ("resolve occurred_at
     relative to received_at=...; use null if it cannot be resolved" is the
     literal instruction given to the model). If the model cannot resolve a
     time reference, `occurred_at` is persisted as `None`/`NULL`.
  4. `determine_clarification_hold` (the gate that decides whether an event
     must wait for a commander before proceeding) checks **only**
     `extraction_result.classification is None` — it does **not** check
     `occurred_at`. A report with an unresolved `occurred_at` is not held;
     it proceeds through the rest of the pipeline with `occurred_at = NULL`
     permanently on its row.
  5. Because of step 2's `occurred_at IS NOT NULL` clause, that event is
     then **excluded from every future `search_events` call that uses the
     default `time_basis`** — including `answer_most_recent_event` and any
     `query_spec` operation that doesn't explicitly ask for `received_at`
     instead. The system will keep answering "last event" with whatever
     older event still has a resolvable `occurred_at`, with no error, no
     hold, and no log line calling this out as the reason.
- **Verdict:** confirmed cause.
- **Evidence:** Direct code reading of the four files above; the causal
  chain (extraction → optional NULL → exclusion from search) is
  unconditional and requires no timing race. I attempted a live
  reproduction of the specific "unresolved timestamp" case via `POST /Msg`
  and hit the C.1 bonus bug (report/request intent classification
  hard-failing) before I could get a report all the way to persistence —
  which is itself informative: **the two bugs compound**. A human report
  first has to survive intent classification (frequently fails per the C.1
  bonus finding) before it can even reach extraction and hit this
  timestamp issue. Given the mechanism itself (steps 1–5) is unconditional
  and grounded in the literal current source, it is reported here as
  confirmed even though the live end-to-end chain was interrupted by the
  separate C.1 bug.
- **Proposed fix:** Two independent, complementary changes:
  1. In `history/event_pipeline.py`'s `extract_event`, when `source ==
     "telegram"` and the model returns `occurred_at = null`, fall back to
     `received_at` and set `occurred_at_is_fallback=True` (the field
     already exists in `ExtractionResult` and is already logged — it's just
     hardcoded to `False` today at line 139, so it currently carries no
     information for this source). This guarantees `occurred_at` is never
     `NULL` for a report that otherwise has enough to proceed, so it can
     never silently vanish from recency queries. The `occurred_at_is_fallback`
     flag lets any future UI/answer explain "the report time as stated
     couldn't be parsed; using receipt time" if desired.
  2. In `history/query.py`'s `answer_most_recent_event`, either default to
     `time_basis="received_at"` (arguably closer to "what actually just
     happened, from the system's perspective") or make the choice a
     documented, deliberate decision recorded in `docs/vocabulary.md`, and
     apply that same choice consistently everywhere recency is computed
     (this file, `query_spec`'s `"latest"` operation, and B.3's precedent
     lookback). The plan's own A.2 explicitly asks to decide and confirm
     this with the user before implementing — flagging that decision back
     rather than picking unilaterally here.
- **Suggested regression test:** Submit a Telegram-style report whose text
  has no resolvable time reference (e.g., "smoke at gate 3" with no "just
  now"/"X minutes ago" wording), confirm via a direct DB read (or a new
  test seam) that `occurred_at` is not `NULL` after extraction, then ask
  "what's the last event" and assert the answer names that event's ID —
  not an older one.
- **Priority:** high.

### A.3 — Check the History Agent's summarization layer isn't the one being asked

- **File(s)/function(s) examined:** `history/query.py` (`_exact_summary`,
  lines 91–108; `query_spec`, lines 366–465; `answer_most_recent_event`,
  lines 543–583); `history/summaries.py` (not fully audited — see below).
- **What the code actually does:** Rolling summaries are only consulted
  inside `query_spec`'s general retrieval path (`retrieve_range`, used for
  `operation` values other than `count`/`aggregate`/`compare`/`latest`),
  and only when the constructed `HistoryQuerySpec` names a period-level
  request the summary tables can satisfy exactly (`_exact_summary` requires
  a precise `period_start`/`period_end` match). `answer_most_recent_event`
  (the direct-lookup fast path) and `query_spec`'s own `operation ==
  "latest"` branch both call `self._persistence.search_events(...)`
  directly — never a summary table — so a recency question routed through
  either of those two paths cannot be answered from a stale rollup by
  construction.
- **Verdict:** not an issue, for the two paths that actually handle "what's
  the last event." I did not exhaustively audit whether some other phrasing
  could cause the model to emit a period-style `HistoryQuerySpec` for what a
  person means as a recency question (e.g., "what happened today") and land
  in the summary-backed branch instead — that narrower question is left as
  "needs more investigation" rather than asserted either way.
- **Evidence:** Code reading of the three functions named; live reproduction
  did not exercise a period-summary-eligible query.
- **Proposed fix:** N/A — no fix needed for the confirmed part. If the
  narrower open question above turns out to matter, the fix would be
  making the question-routing prompt explicit that "today"/"this shift"-style
  windows still resolve through a live table scan when they overlap the
  in-progress period (summaries only exist for *completed* periods).
- **Suggested regression test:** Ask "what happened today" shortly after
  submitting an event, and assert the answer includes that event even
  though today's daily summary (if any exists) would not yet include it.
- **Priority:** medium.

### A.4 — Check conversation memory's own staleness guard

- **File(s)/function(s) examined:** `orchestrator/reasoning.py`
  (`answer_question`, lines 1495–1608), `history/query.py` (`query_spec`,
  `answer_most_recent_event`).
- **What the code actually does:** When the routing model resolves a
  reference to a stable Event ID (via the `"history"` route with a
  `HistoryQuerySpec` naming that `event_id`), `answer_question` calls
  `history_query_service.query_spec(...)`, which always performs a fresh
  `self._persistence.search_events(...)`/`count_events(...)` call against
  the live `events` table — there is no cache, no reuse of a prior turn's
  remembered answer text, and no code path that returns the earlier
  assistant reply verbatim. The "remembered" data (`conversation_messages`)
  is only ever fed into the *prompt* asking the model which Event ID a
  demonstrative refers to — never substituted for the DB read itself.
- **Verdict:** not an issue — the guard described in README is real and
  enforced in the code, for the path exercised (the legacy `answer_question`
  path, which is what `profiles.demo`'s default `planner_mode` uses). I did
  not separately verify the `answer_question_from_plan` ("merged") path's
  equivalent behavior in this pass — flagged as an open, lower-priority
  question since it isn't the active default.
- **Evidence:** Code reading; no caching layer exists between
  `history_query_service.query_spec` and `persistence.search_events`.
- **Proposed fix:** N/A for the active path. If pursued, verify
  `answer_question_from_plan` (`orchestrator/reasoning.py`, imported but
  used only under `planner_mode in {"merged"}`) the same way.
- **Suggested regression test:** In one conversation, ask about a specific
  event, get an answer; change that event's state directly in the DB (e.g.
  via a test seam), then ask "what's the status of that event now?" in the
  same conversation and assert the new state is reflected, not the first
  answer's wording.
- **Priority:** low (already correct on inspection for the active path).

### A.5 — Check for caching or connection-level staleness

- **File(s)/function(s) examined:** `persistence/sqlite_store.py`
  (`_submit_write`, lines 291–304; `_read_connection`, lines 315–323;
  `SQLitePersistence.__init__`, lines 246–261), `orchestrator/flows.py`
  (`begin_report`, lines 167–196).
- **What the code actually does:** All writes go through a single dedicated
  writer thread via `_submit_write`, which blocks the calling thread
  (`future.result()`) until the write's transaction has actually committed
  (WAL mode) before returning control to the caller. Reads use separate,
  per-thread connections (`_read_local`) with no additional caching layer.
  Because the call chain for "submit an event, then (later, in the same or
  a new request) ask a question about it" always passes through this
  blocking write before any subsequent read can be issued, there is no
  code-level staleness window for causally-ordered read-after-write.
  Separately: `begin_report` persists the *initial* event row (raw text,
  source, `received_at`, sender) synchronously and returns immediately,
  before extraction (which sets `occurred_at`, `classification`, etc.) has
  run — extraction itself happens on the async queue. So a recency question
  asked in the same instant as submission, before the queue has processed
  that item, will legitimately not see it yet.
- **Verdict:** not an issue, for the connection/caching question. The
  queue-timing question is a **contributing factor** in the narrow sense
  that it's real, expected async behavior — not a defect, but worth
  surfacing to a user as "still processing" rather than silence.
- **Evidence:** Code reading of the three functions named.
- **Proposed fix:** N/A for the connection-staleness question (no fix
  needed). For the queue-timing effect: if a recency question is asked
  about an event still `queued`/`running`, consider having the answer
  mention that a submission is still being processed rather than silently
  answering as if it doesn't exist yet — a UX improvement, not a
  correctness fix.
- **Suggested regression test:** Submit an event and, without waiting for
  processing, immediately ask "what's the last event" in a way that
  deterministically races the queue (e.g. via a test double that holds the
  queue open); assert the response is either correct or explicitly
  communicates "still processing," never a stale wrong answer presented as
  final.
- **Priority:** low.

### A.6 — Write the root-cause report before patching

- **Deliverable:** A.2 is the confirmed mechanism: for Telegram/human
  reports specifically, an unresolved `occurred_at` silently excludes the
  event from every future recency/history answer, forever, with no hold and
  no operator-visible error. A.1 (the plan's prime suspect) is not
  confirmed — the routing prompts already correctly separate recency from
  reference language. A.3–A.5 are not confirmed as independent causes for
  the specific "what's the last event" symptom, though A.5's queue-timing
  point and A.3's narrower open question are worth keeping in mind.
  Critically, a **separate, unplanned bug** (filed under C.1 below) means
  human reports frequently fail intent classification outright before ever
  reaching the A.2 mechanism — both should be fixed together, since fixing
  A.2 alone would leave many real reports never reaching extraction at all.
- **Priority:** N/A (synthesis entry).

### A.7 — SEPARATE, HIGHER-PRIORITY BUG: "situation picture" shows false-safe status

- **File(s)/function(s) examined:** `orchestrator/reasoning.py`
  (`answer_question`, `_build_agent_selection_prompt` — the live prompt text
  was captured directly from `DEEP_DEBUG` `model_io` records, not just read
  from source, to see exactly what the model was told and what it decided).
- **What the code actually does:** "Situation picture"-style questions
  ("give me the current situation picture, is everything ok?") go through
  the same general `answer_question` → question-routing path as any other
  question. There is **no dedicated "situation picture" feature or code
  path** anywhere in the codebase (confirmed by a repository-wide search —
  the term appears nowhere in `orchestrator/`, `history/`, `api/`). The
  question-routing prompt instructs the model: *"Route questions about
  stored past events to history and current-state questions to suitable
  specialist agents."* For this phrasing, the model chose the
  `reference_agent` specialist (its only read-only tool being
  `check_status`, a live/fake per-location check unrelated to the events
  table) for both known areas, and explicitly declined to consult history.
- **Verdict:** confirmed cause — reproduced live.
- **Evidence:** Live run against the reproduction server, trace ID
  `bf52e547f9ed437d9dc84ce2e12a30c5`. At the time of the question, event
  `e4ef8e9152c94fb183f937659c4e64d8` (a fire report, `classification=fire`,
  `area=north_sector`) had status `"uncertain"`, with its own insight text
  explicitly stating the fire had **not** been formally recorded due to a
  tool-permission failure and calling this "a safety-critical classification
  unlogged." Asked "give me the current situation picture, is everything
  ok?", the system answered:
  > "North Sector: ✅ Nominal — None detected... All sectors are operating
  > normally... green across the board — no immediate action required."

  The routing model's own captured JSON response for this question:
  ```json
  {"route":"agents","tasks":[{"agent_name":"reference_agent","task":"Check the current status of all available locations (north_sector and south_sector) and provide a current situation picture."}],"reason":"The question asks for the current situation picture and whether everything is ok — this is a current-state question best answered by the reference_agent using its check_status tool across both known areas. No historical lookup is needed as no past events are referenced."}
  ```
  This is the exact false-safe failure mode the plan describes, with the
  model's own stated reasoning confirming *why*: it categorically decided a
  "how are things" question needs no historical lookup, when in fact
  exactly this kind of question is the one place an open/unresolved
  incident most needs to surface.
- **Which hypothesis:** Closest to Hypothesis 4 (scope mismatch) but more
  fundamental than a filter — this is a **routing-prompt design defect**:
  the binary "past events → history, current-state → specialist" framing
  in `_build_agent_selection_prompt` has no category at all for "is there
  anything currently unresolved," so a genuinely urgent open incident is
  invisible to this class of question by construction, not by an
  accidental filter mismatch. Hypotheses 1 (stale rollup) and 2 (closure
  semantics as literally described) were not the mechanism here — the
  system never queried the events table at all for this question, so
  rollup-staleness/closure-definition are moot for this exact case; the raw
  query result question ("(a) lost in generation vs (b) never in the raw
  result") resolves to a third answer the plan didn't list as a numbered
  option: **no query was ever issued.**
- **Proposed fix:** Change `_build_agent_selection_prompt`'s routing
  instruction so "current state"/"situation"/"is everything ok"-style
  questions are explicitly told to *also* check for unresolved/open events
  (via a history route filtered to non-terminal `outcomes`, e.g. `queued`,
  `running`, `held_for_approval`, `held_for_clarification`, `uncertain`)
  before or alongside any live specialist check — not one or the other.
  Concretely: add a rule to the prompt that any question implying overall
  status ("situation", "is everything ok", "anything to report") must
  route (at least in part) to `history` with an outcome filter for
  non-terminal states, and that a specialist's live check never substitutes
  for that history check. This is a prompt-text change plus (if needed) a
  small addition to the routing schema to allow combining a `history` task
  with `agents` tasks in one response, if the current schema doesn't
  already allow both (verify this before implementing).
- **Suggested regression test:** Exactly as the plan specifies: submit a
  high-criticality event, immediately ask for the situation picture, assert
  the event appears and is not characterized as "all clear." Run for a
  freshly-submitted event and for one already inside a hold state, in both
  English and Hebrew.
- **Priority:** critical (safety).

---

## Part B — Recommended fix plan (document only)

### B.1 — Separate "recency" intent from "reference" intent

- Per A.1, the code already does this correctly for the active
  (`legacy`-planner) path. The recommended work here is **not** a fix but a
  **regression test** to lock in the behavior: a labeled bilingual phrasing
  set ("what's the latest", "anything new", "last report", "האירוע האחרון",
  "מה קרה עכשיו", "מה חדש") run against `_is_direct_most_recent_lookup` and
  the full question-routing path, each asserting the answer reflects the
  true most-recent event and not a remembered reference. This test would
  have caught nothing today (the mechanism is correct) but guards against
  a future prompt edit silently reintroducing exactly the failure mode the
  plan worried about.
- **Priority:** medium (protective test, not a fix for a live bug).

### B.2 — Make scoping visible in the answer

- Not evaluated as a live bug in this pass (A.2 was the confirmed cause,
  not viewer-scoping) — but the underlying mechanism the plan describes is
  real: `history/query.py`'s `sender_identity_filter` (threaded from
  `api/routes.py`'s `caller_sender_identity_filter = None if level is
  PermissionLevel.COMMANDER else caller_identity`) does restrict a viewer's
  recency/history answers to their own submissions, silently. Recommended:
  when `sender_identity_filter` is not `None` and it measurably changes the
  result set (i.e., a broader "any commander" query would have returned a
  different top event), have the History Agent's answer say so explicitly,
  e.g. "the most recent event **you reported** was X." This requires
  passing a signal into the History Agent's prompt (or a post-hoc check
  comparing scoped vs. unscoped `total_events_matched`) — implementable,
  not yet implemented.
- **Priority:** low-medium (UX clarity, not a correctness bug).

### B.3 — Confirm precedent lookback isn't affected by the same bug

- **File(s) examined:** `orchestrator/reasoning.py` (`look_up_precedent`,
  line 1062), `history/precedent.py` (not fully audited in this pass — name
  inferred from `find_precedents` import in `history/query.py`).
- Given A.2's confirmed mechanism (a report's `occurred_at` can be `NULL`
  for Telegram sources), and given `look_up_precedent` is only called with
  `event["occurred_at"]` after `orchestrator/flows.py` line 472 already
  guards `if event["occurred_at"] is None ...: return None` (skips
  precedent lookup entirely rather than searching with a bad value) — this
  specific failure mode (a `NULL` timestamp corrupting a search window)
  appears **already guarded against** for precedent lookup specifically,
  unlike the recency-query path. This is a partial, not exhaustive, check —
  `history/precedent.py`'s own window-computation logic was not read in
  this pass.
- **Verdict:** needs more investigation (the one guard found is reassuring
  but the precedent module itself wasn't opened).
- **Priority:** medium.

---

## Part C — General conversation-quality improvements

### C.1 — Audit intent classification for "getting stuck" more broadly ✅ COMPLETED (bonus finding only)

> **Status: the bonus finding's fix is implemented** — `_build_intent_prompt`
> no longer hardcodes `"question"` as the evidence key, and
> `_parse_structured_intent_response` now requires only non-empty evidence
> rather than a key matching `primary_intent` (fix (b), the more robust
> option this entry itself recommended). See `docs/progress.md` (2026-09-04,
> "CRITICAL_FIXES_PLAN item 2: intent-classification prompt no longer
> hardcodes an evidence key"). The incidental `process_message` dead-code
> observation noted in "File(s)/function(s) examined" below was **not**
> addressed — it was flagged as optional, separate cleanup, not part of
> this authorized fix.

- **File(s)/function(s) examined:** `orchestrator/reasoning.py`
  (`classify_intent`, `_parse_structured_intent_response`,
  `_build_intent_prompt`, lines ~300–470), `orchestrator/flows.py`
  (`process_message` — found to be **dead code**, not called from
  `api/routes.py` at all; the live `/Msg` handler calls
  `answer_question_from_plan`/`answer_question` directly with
  `conversation_messages` and ownership scoping correctly threaded through,
  so `process_message`'s own incomplete-looking call is not reachable in
  production and not a live bug — worth removing or fixing for its own
  sake as dead-code hygiene, separately from this investigation).
- **What the code actually does — the planned check:** `waiting_for_event_data`
  routing (README: "Messages that do not answer the data request continue
  through normal intent routing") was not independently re-verified in this
  pass (it was investigated and confirmed working correctly in a prior,
  separate session — see this repo's own conversation history if available;
  not re-derived here to avoid duplicating already-verified work under this
  investigation's own "ground everything in what was actually read" rule,
  since it wasn't re-read this pass).
- **Bonus finding, not in the original plan — CONFIRMED CAUSE, reproduced
  live twice in a row:** `_build_intent_prompt`'s JSON schema example (line
  322) is:
  ```
  '"is_followup_without_context":false,"evidence":{"question":"exact quote from message"},'
  ```
  This is a **generic, hardcoded example** using the literal key
  `"question"` — it is not adjusted per the actual `primary_intent` the
  model determines. `_parse_structured_intent_response`'s validation (line
  ~445) requires `analysis.primary_intent in analysis.evidence` — i.e., the
  determined intent name must itself be a key in the `evidence` object. In
  live testing, real "report"-classified messages consistently came back
  with `"evidence":{"question":"<exact quote>"}` — the model copied the
  example's literal `"question"` key rather than using `"report"` — and
  every one of these calls was rejected by the parser (twice in a row, the
  same mistake on both the original attempt and the one-shot repair retry),
  surfacing to the caller as `{"error_class":"run_failure",...,"message":"primary
  intent 'report' requires exact evidence"}`, a hard `422`. This reproduced
  on **every** report-style message tried (3 different phrasings, all
  failed the same way); it did not reproduce for `POST /Event` (which
  bypasses intent classification entirely) or, in the trace captured, for
  a genuine question. Compare with the newer `_MESSAGE_PLAN_SCHEMA` (used
  only by the "merged" planner, not the active default), whose `evidence`
  schema requires **all three** keys (`question`, `report`, `request`)
  simultaneously via strict JSON-schema enforcement — a design that doesn't
  depend on the model picking the "right" key at all, and does not have
  this failure mode.
- **Verdict:** confirmed cause (critical).
- **Evidence:** Live model_io records, trace `43dd01018ee443b3a0785eb4b0c53d84`,
  both attempts' raw responses quoted verbatim above/in the investigation
  log; `orchestrator/reasoning.py` lines 322, ~392–420, ~444–446 for the
  prompt text and the validation that rejects it.
- **Proposed fix:** In `_build_intent_prompt`, replace the single generic
  evidence example with either (a) three parallel examples, one per intent
  ("...if primary_intent is 'question', evidence should be
  `{\"question\": ...}`; if 'report', `{\"report\": ...}`; if 'request',
  `{\"request\": ...}`"), or (b) — the more robust fix, matching what the
  newer merged-planner schema already does — change
  `_parse_structured_intent_response`'s expectation to always require all
  three possible keys (with the non-applicable ones empty/null) rather than
  a key matching whatever `primary_intent` turned out to be. Given the
  merged-planner schema already solved this exact problem, migrating the
  legacy path's evidence contract to match it (even without fully migrating
  to the merged planner) is the more durable fix.
- **Suggested regression test:** Submit several distinctly-worded report and
  request messages (not just questions) through `classify_intent` directly,
  asserting `status == "success"` and no `OrchestrationParseError` — this
  would have caught the bug immediately, since it reproduced on 3/3
  attempts with real messages in this pass.
- **Priority:** critical.

### C.2 — Improve grounding for "what can you do" / capability questions

- **File(s)/function(s) examined:** `orchestrator/capabilities.py`
  (`visible_capabilities`, `build_role_aware_system_context`, lines 135+).
- **What the code actually does:** `visible_capabilities` is a pure,
  role-filtered function over a static/registry-derived catalog (not
  model-generated), and `build_role_aware_system_context` assembles the
  system prompt context from it. The factuality risk the plan describes
  (model paraphrasing loosely, inventing or omitting a capability) lives
  downstream of this — in whatever free-text answer the Main Agent
  generates from this context — which was not independently exercised live
  in this pass.
- **Verdict:** needs more investigation. The grounding data itself is
  correctly role-filtered by code, not by the model; whether the model's
  *prose* about it stays faithful was not tested.
- **Evidence:** Code reading only.
- **Proposed fix:** Not proposed — insufficient evidence yet.
- **Suggested regression test:** The plan's own suggestion is sound as-is:
  a regression test that diffs the model's claimed capabilities (parsed
  from its answer) against `visible_capabilities(level)`'s actual filtered
  catalog, for both roles.
- **Priority:** medium.

### C.3 — Improve ambiguous-reference handling

- **File(s)/function(s) examined:** `orchestrator/reasoning.py`
  (`_MESSAGE_PLAN_SCHEMA`'s `ambiguity_reason`/`is_followup_without_context`
  fields; the question-routing prompt's own reference-resolution
  instructions, captured live in trace `bf52e547f9ed437d9dc84ce2e12a30c5`:
  *"use the conversation context only to identify which stable Event ID(s)
  the reference means (a prior assistant answer that discussed one event,
  or a numbered list of several, in the order given)"*).
- **What the code actually does:** The mechanism for flagging an ambiguous
  reference exists at the intent-classification level
  (`ambiguity_reason`/`is_followup_without_context` → routes to
  `needs_clarification`). Whether the *specific* "more than one plausible
  prior event" case (as opposed to "missing context" generally) is detected
  with the right breadth (not too narrow, not too broad, per the plan's own
  framing) was not independently tested with a live multi-event
  conversation in this pass.
- **Verdict:** needs more investigation.
- **Evidence:** Code/prompt reading only; no live multi-reference
  conversation was run.
- **Proposed fix:** Not proposed — insufficient evidence yet.
- **Suggested regression test:** A conversation that discusses two
  different events, then asks an ambiguous follow-up ("what's the status
  now?") — assert a clarification question naming both candidates, not a
  guess. A second test with a genuinely unambiguous follow-up (only one
  event discussed) — assert no needless clarification is asked.
- **Priority:** medium.

### C.4 — Bilingual consistency

- **File(s)/function(s) examined:** `messages/en.py`, `messages/he.py`
  (existence and general structure confirmed via earlier work in this
  session; not line-by-line re-audited here), `profiles/demo.py`
  (`DEFAULT_LANGUAGE = "en"`).
- **What the code actually does:** The demo profile (and this
  investigation's reproduction) runs with `DEFAULT_LANGUAGE = "en"`; no
  Hebrew-language run was performed in this pass, so bilingual parity for
  the recency bug (A.2), the situation-picture bug (A.7), or the
  intent-classification bug (C.1's bonus finding) was **not** verified in
  Hebrew.
- **Verdict:** needs more investigation.
- **Evidence:** None gathered this pass beyond confirming the profile's
  language setting.
- **Proposed fix:** Not proposed — insufficient evidence yet.
- **Suggested regression test:** Re-run this investigation's three live
  reproductions (A.2's unresolved-timestamp report, A.7's situation
  picture, C.1's report-classification failure) against a profile with
  `DEFAULT_LANGUAGE = "he"`, in Hebrew phrasing, and compare outcomes.
- **Priority:** low (until the English-language fixes above land — no
  point verifying Hebrew parity for bugs not yet fixed in either language).

---

## Part D — General speed improvements

### D.1 — Confirm warmup and model-tier assignment are still correctly matched

- **File(s)/function(s) examined:** `profiles/demo.py` (`AGENTS`, using
  `tier="sub"` for `ReferenceAgent`), `orchestrator/reasoning.py`
  (`MainAgent`/`InsightsAgent` construction via `base_config.core_model`,
  `_construct_core_agents`), live warmup log from the reproduction run.
- **What the code actually does:** The live warmup log showed exactly one
  real provider call at startup (`anthropic/claude-sonnet-4.6` via
  `openrouter`) — `profiles.demo` only declares one non-core-tier agent
  (`ReferenceAgent`, tier `"sub"`), and `CORE_MODEL_NAME`/`SUB_MODEL_NAME`
  are configured identically in this deployment's `.env`
  (`anthropic/claude-sonnet-4.6` for both), so warmup deduplication (Stage
  9's "one request per unique Provider/Model") correctly collapsed to one
  call. The Main Agent and Insights Agent are constructed from
  `base_config.core_model` (core tier); `ReferenceAgent`/`HistoryAgent` from
  whichever tier their `AgentSpec` declares. No misassignment found — the
  Main Agent (judgment-heavy) stays on core, `ReferenceAgent` (the
  demo's cheap/high-volume specialist) stays on sub.
- **Verdict:** not an issue.
- **Evidence:** Live warmup log (`model warmup started` → one
  `chat/completions` call → `model warmup finished`) plus the source files
  named.
- **Proposed fix:** N/A.
- **Suggested regression test:** N/A (existing warmup-dedup tests per
  `docs/progress.md`'s Stage 9 already cover this).
- **Priority:** low.

### D.2 — Check for unnecessary sequential model calls

- **File(s)/function(s) examined:** Live `DEEP_DEBUG` telemetry for trace
  `e0c13b475b1644928392e326c4626ca3` (one full sensor report, start to
  finish); `docs/progress.md`'s IMPROVE-C/D/E entries; `profiles/contracts.py`
  (`OptimizationPolicy`, `operational_decision_mode`).
- **What the code actually does:** One full report, in the currently-active
  (`operational_decision_mode="separate"`) configuration, makes **7
  sequential model calls totaling ~39.4 seconds of pure model latency**:
  `extraction` (3.0s) → `risk_assessment` (3.0s) → `protocol_selection`
  (3.4s) → `task_formulation` (3.9s) → `step_execution` (11.2s) →
  `insight_generation` (9.1s) → `success_judgment` (5.7s). Each stage's
  input genuinely depends on the previous stage's output in this pipeline
  shape, so there is no "obviously independent, currently-serialized" pair
  within this trace to parallelize as-is. However,
  `docs/progress.md` records that `operational_decision_mode="merged"`
  (combining `risk_assessment` + `protocol_selection` into one call) is
  already built and tested, but **not the active default** — activating it
  would remove one full sequential round-trip (~3–3.4s) without
  restructuring anything. Likewise, IMPROVE-E's DAG step parallelism
  wasn't exercised by this trace because the selected protocol
  (`dispatch_response`) has only one participating agent/step; a
  multi-step protocol would be where that already-built parallelism
  matters.
- **Verdict:** contributing factor (latency, not correctness) — confirmed
  the call count and timing; the "already built, not activated" pattern
  repeats from D.3.
- **Evidence:** Live telemetry (`model_invocation_finished` records) quoted
  above, from a real run.
- **Proposed fix:** Evaluate activating `operational_decision_mode="merged"`
  for the profile(s) where the plan's own experiment gates
  (`docs/progress.md`'s "live rollout gates pending") have since been
  satisfied — this is a config/profile change, not new code. Precedent
  lookup was confirmed (A.5/B.3 area) to be a plain DB search, not a model
  call, so it isn't a parallelization candidate against risk assessment as
  the plan speculated — that specific pairing doesn't apply here.
- **Suggested regression test:** A benchmark test (matching the style of
  `docs/progress.md`'s "175.8ms mocked benchmark") re-run with
  `operational_decision_mode="merged"` active, asserting both correctness
  parity and a measurable reduction in sequential model-call count.
- **Priority:** medium.

### D.3 — Verify the LLM/client cache (SPEED_PLAN Stage 10) covers the actual hot path

> **Status: reviewed and resolved, 2026-09-04 — `thread_safe_client` intentionally
> left `False` for `openrouter`, not merely unset.** The bounded provider-
> capability review this entry's own "proposed fix (a)" called for was done
> — see the addendum after "Priority" below for the concrete evidence found
> and why option (a) (enabling the cache) was rejected in favor of option
> (b) (document plainly, per this entry). `docs/progress.md` 2026-09-04.

- **File(s)/function(s) examined:** `agents/contracts.py`
  (`ProviderCapabilities`, `provider_capabilities`, lines ~15–34),
  `agents/runtime.py` (line 246, the sole call site gating the reuse
  cache), `.env` (`CORE_MODEL_PROVIDER=openrouter`,
  `SUB_MODEL_PROVIDER=openrouter`).
- **What the code actually does:** `ProviderCapabilities.thread_safe_client`
  defaults to `False`. `provider_capabilities(model)` explicitly sets it
  `False` for `"openai"` and returns the all-`False` default
  `ProviderCapabilities()` for every other provider string — there is no
  branch anywhere in the function, or found elsewhere in the codebase, that
  ever sets `thread_safe_client=True`. `agents/runtime.py:246` is the only
  place this flag is read, and it gates the entire reuse-cache path. The
  actually-configured provider for this deployment, `openrouter`, therefore
  always takes the "existing isolated construction path" (Stage 10's own
  fallback for an unmarked provider) — it never benefits from the cache.
- **Verdict:** confirmed cause (of zero cache benefit for the current
  deployment, not a correctness bug).
- **Evidence:** `grep -n "thread_safe_client" agents/*.py` returns exactly
  three lines: the dataclass default, the explicit-`False` for `"openai"`,
  and the one gating read-site — confirming there is no path to `True`
  anywhere in the current source.
- **Proposed fix:** Either (a) do the source/documentation review and
  concurrency testing Stage 10's own rule #2 requires for `openrouter`
  specifically (it proxies to various upstream providers, so this needs
  care — the review can't just assume OpenAI-compatible thread-safety
  transfers) and mark it `thread_safe_client=True` if it holds up, or (b)
  if that review can't be completed confidently, document plainly (e.g. in
  `docs/SPEED.MD` or `docs/progress.md`) that the cache currently provides
  no benefit for any shipped profile, so it isn't mistaken for active
  infrastructure in future latency investigations.
- **Suggested regression test:** A concurrency test already exists per
  Stage 10's own file work (`multithreaded runtime tests`) — extend it with
  a case for `openrouter` specifically if (a) above is pursued, asserting
  no state leaks across concurrent calls with distinct trace IDs/schemas/
  tool allowlists, per the cache rules already documented in
  `SPEED_PLAN.MD` Stage 10.
- **Priority:** medium-high (real, currently-wasted infrastructure —
  fixing it could measurably help warm-call latency, which D.2's trace
  shows is a real cost).

- **Addendum, 2026-09-04 — the bounded review, done:** Read CrewAI
  1.15.17's actual construction path for `openrouter`, not just its flag
  value. `LLM.__new__` routes `openrouter` to
  `crewai.llms.providers.openai_compatible.completion.OpenAICompatibleCompletion`
  — a **native provider class**, not the LiteLLM fallback. That class is a
  thin subclass of `crewai.llms.providers.openai.completion.OpenAICompletion`:
  it overrides only provider-config resolution (`base_url`/`api_key`/
  headers) and delegates `supports_function_calling` to the parent — it
  does **not** override client construction or any call-execution method.
  Concretely, this means `openrouter`'s thread-safety is byte-for-byte
  identical to `openai`'s, since they share the exact same request-handling
  code.
  - **Found genuine shared mutable state, not just an unreviewed default.**
    `OpenAICompletion` stores `self._last_response_id` and
    `self._last_reasoning_items` on the instance (constructor: `None`;
    written in `_handle_responses`/`_handle_streaming_responses`; read via
    the `last_response_id` property; explicitly resettable via
    `reset_chain()`). This exists to chain a multi-turn conversation
    through OpenAI's Responses API (`previous_response_id`). If a cached,
    shared `LLM` instance served two **concurrent, unrelated** calls, both
    would race on these two attributes — one request's response ID could
    leak into another's chaining state, corrupting an unrelated
    conversation with no error raised. There is no lock around these
    writes.
  - **This path is reachable, not purely theoretical, even for a
    non-`custom_openai` provider like `openrouter`:** `_effective_api()`
    upgrades a model from `"completions"` to `"responses"` automatically
    once that exact model name has been recorded (process-wide, via
    `_remember_responses_only_model()`) as 404-ing on chat completions —
    a condition that depends on runtime behavior, not something this
    review can rule out for every current or future `openrouter`-routed
    model name.
  - **The plain chat-completions handler (`_handle_completion`) itself
    writes no comparable per-instance state** — the risk is specifically
    the Responses-API chaining path described above, not the ordinary
    request path AgentsHub uses today. That distinction doesn't remove the
    risk, since the upgrade to that path is automatic and outside this
    codebase's control.
  - **`thread_safe_client=False`'s prior meaning corrected:** `docs/progress.md`'s
    original SPEED_PLAN Stage 10 entry recorded `openai` as deliberately
    left isolated pending exactly this kind of review — it was **not**
    previously backed by a concrete unsafety finding for either `openai`
    or `openrouter`. It is now: the review this entry called for is done,
    and the concrete finding is the `_last_response_id`/
    `_last_reasoning_items` race described above, which applies to
    `openrouter` identically to `openai` since they share the same class.
  - **Decision: option (a) rejected, option (b) applied.** `thread_safe_client`
    stays `False` for `openrouter` — flipping it would introduce a real,
    hard-to-debug cross-request state-corruption risk for no benefit
    (correctness always outranks a latency win in this codebase's own
    stated rules). No code changed as a result of this review — the
    existing `False` default was already the safe value; it now has a
    documented reason instead of being an unreviewed default, so a future
    audit doesn't re-flag it as "still needs deciding." `docs/agent_authoring.md`'s
    existing rule ("do not enable it from assumption or model name alone")
    is exactly what this finding confirms in practice.
  - **If this is ever revisited:** the deciding question would be whether
    a *newer* CrewAI release removes or synchronizes the Responses-API
    chaining state, not whether `openrouter` specifically differs from
    `openai` — they are the same code path today and will remain so unless
    CrewAI's `OpenAICompatibleCompletion` stops subclassing
    `OpenAICompletion` unchanged.

### D.4 — Check for redundant DB reads per turn

- **File(s)/function(s) examined:** `persistence/sqlite_store.py`
  (`_attach_steps_many`, referenced from `search_events`/`list_users`-style
  batch reads, per `docs/progress.md`'s IMPROVE-B "event-step reads are
  batched" claim) — not independently counted against a live trace's actual
  SQLite round-trip count in this pass (my `DEEP_DEBUG` capture surfaced
  model-call telemetry clearly; it does not by itself give a per-request
  SQLite call count without deeper instrumentation than this pass added).
- **Verdict:** needs more investigation.
- **Evidence:** `docs/progress.md`'s own claim ("Event-step reads are
  batched... read connections are thread-scoped") was read but not
  independently re-verified against a live trace's actual query count in
  this pass.
- **Proposed fix:** Not proposed — insufficient evidence yet.
- **Suggested regression test:** Wrap a test's SQLite connection with a
  query-counting shim (or use `sqlite3`'s own trace callback) around one
  history question and one event submission against a real
  `SQLitePersistence`, asserting no N+1 pattern (e.g. one query per step
  rather than one batched query for all steps of an event).
- **Priority:** medium.

### D.5 — Confirm timeouts and retry settings aren't silently adding latency

- **File(s)/function(s) examined:** Live telemetry from trace
  `e0c13b475b1644928392e326c4626ca3` (D.2's data), `profiles/demo.py`
  (`MAX_ITER = 8`, `MODEL_TIMEOUT_SECONDS = 30`).
- **What the code actually does:** None of the 7 individual model calls in
  the captured live trace came close to the 30-second `MODEL_TIMEOUT_SECONDS`
  ceiling — the slowest single call (`step_execution`, 11.2s) was well
  under it. Whether any call is silently consuming multiple CrewAI
  iterations before returning (up to `MAX_ITER=8`), which would show up as
  latency without a visible retry/timeout event, was not measured in this
  pass — the captured telemetry gives call-level latency, not an
  internal-iteration-count breakdown.
- **Verdict:** needs more investigation for the iteration-count question;
  not an issue for the timeout-proximity question in this one sample.
- **Evidence:** The 7 latency figures quoted under D.2.
- **Proposed fix:** Not proposed — insufficient evidence yet on the
  iteration-count question.
- **Suggested regression test:** Extend the existing provider telemetry
  (`docs/progress.md` Stage 6, "durable provider telemetry") to expose or
  log per-call iteration counts if not already captured, then sample real
  traffic (or a larger batch of reproduction runs like this pass's) to
  find any stage that's routinely using several iterations — that stage's
  prompt/tool design is the concrete place to investigate first, per the
  plan's own framing.
- **Priority:** low-medium.

---

## Confirmation: no files outside this document were changed

- `git status --porcelain` at the end of this pass: **empty** (clean
  working tree).
- The live reproduction ran against an isolated scratch profile/database
  created outside the repository (`%TEMP%\agentshub_repro_profile\`, plus a
  temporary profile module under `%TEMP%\repro_profile\`), never against
  `data/demo_profile.db`. That file's modification time
  (`2026-09-04 02:24`, confirmed via `ls -la`) predates this investigation
  and was unchanged afterward.
- The reproduction server process (port 8977) and its scratch files were
  stopped/left outside the repository; nothing was left running against
  `profiles.demo`'s real port (8902).
- No source file in this repository was created, edited, or deleted during
  this pass other than this one, `docs/IMPROVE_ALL_SYSTEM.MD`.
