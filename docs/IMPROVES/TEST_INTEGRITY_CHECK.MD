# AgentsHub — Test Integrity Check + Outstanding Items Inventory

**Purpose:** Two read-mostly verification passes. Part A re-verifies the 6
pre-existing tests updated for `REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md`
Part 1's required-fields gate, to rule out the setup change having quietly
weakened what each test actually catches. Part B inventories every
numbered/lettered item across the whole findings series against what's
actually been implemented (per `docs/progress.md`, not just what a
document claims).

---

## Part A — Test integrity: the 6 tests updated for the required-fields gate

**Method:** For each test, the "original assertion" is read verbatim from
`git show HEAD:<path>` (the last committed version — all changes described
in this whole series are still uncommitted working-tree edits, so `HEAD`
is genuinely the pre-change baseline, not a guess from memory). The
masking-failure check was done empirically, not by reasoning alone: a
deliberate break was introduced into the production code path each test
depends on, the affected test(s) were run and confirmed to fail, and the
break was then reverted and the tests re-confirmed passing. Two distinct
deliberate breaks were used (see each entry below); both were reverted
immediately after use and neither is present in the final diff (`git diff
--stat -- orchestrator/flows.py` and a repo-wide grep for the break marker
both confirm this).

### tests/test_api_holds.py::test_clarify_with_a_valid_type_returns_202_and_resumes
- **Original assertion (one sentence):** POSTing a valid classification to
  `/Clarify` on a held event returns `202 queued`, and once the queue
  drains, the event's status is `succeeded` with `classification` recorded
  as the value that was posted.
- **What changed in setup, and why:** The shared `_make_clarification_hold`
  helper (used by all 5 `test_api_holds.py` entries below) now resolves the
  new gate's `area` requirement — `update_event(..., {"area": ...})` +
  `resume_after_event_data(...)` — before its final assertion, because an
  unresolved classification now resolves to the built-in `unclassified`
  event type, which requires `area` and blocks in `waiting_for_event_data`
  *before* a clarification hold exists at all. No change was made inside
  this test's own body.
- **Confirmed original behavior still verified:** Yes. The helper's own
  closing assertion (`job_status(...) == "held_for_clarification"`) is
  unchanged in substance — it was checked immediately after extraction
  before, it's checked immediately after the new prerequisite step now,
  and it still gates whether the helper returns at all. This test's own
  body then exercises the real `/Clarify` HTTP endpoint against a real,
  unmodified clarification hold and confirms a real queued resume to
  `succeeded` — none of that path changed.
- **Masking-failure check result:** Confirmed no masking, empirically.
  Temporarily replaced `_continue_after_required_fields`'s
  `if classification == UNCLASSIFIED_TYPE:` guard with `if False:` (so no
  clarification hold is ever created and the flow falls through to risk
  assessment instead) — ran `tests/test_api_holds.py`: this test failed
  (via the shared helper's `resume_after_event_data` call hitting an
  unscripted risk-assessment prompt, since the fake main agent has no
  response prepared for a path that should never be reached). Reverted the
  break; the test passes again. A broken `held_for_clarification` path
  cannot produce a passing result here.
- **Verdict:** Genuinely fine.

### tests/test_api_holds.py::test_clarify_with_a_value_outside_the_registry_is_rejected
- **Original assertion (one sentence):** POSTing a classification value
  that isn't in the event-type registry to `/Clarify` on a held event
  returns `400` naming `"classification"` as the offending field.
- **What changed in setup, and why:** Same shared-helper change as above,
  same reason.
- **Confirmed original behavior still verified:** Yes, same reasoning as
  above — the helper's prerequisite assertion is unchanged in substance;
  this test's own body (POST an invalid classification, assert `400` +
  field name) is untouched.
- **Masking-failure check result:** Same deliberate break as above, run
  against the same file — this test failed for the same reason (the shared
  helper never reaches a usable held event) and passed again after revert.
- **Verdict:** Genuinely fine.

### tests/test_api_holds.py::test_clarify_rejects_a_missing_classification_field
- **Original assertion (one sentence):** POSTing an empty body to
  `/Clarify` on a held event returns `400` naming `"classification"` as
  the missing field.
- **What changed in setup, and why:** Same shared-helper change, same
  reason.
- **Confirmed original behavior still verified:** Yes, same reasoning.
- **Masking-failure check result:** Same deliberate break, same result
  (failed with the break in place, passed after revert).
- **Verdict:** Genuinely fine.

### tests/test_api_holds.py::test_clarify_requires_commander_level
- **Original assertion (one sentence):** A viewer identity POSTing to
  `/Clarify` on a held event (even with a valid classification) is
  refused with `403`.
- **What changed in setup, and why:** Same shared-helper change, same
  reason.
- **Confirmed original behavior still verified:** Yes, same reasoning.
- **Masking-failure check result:** Same deliberate break, same result.
- **Verdict:** Genuinely fine.

### tests/test_api_holds.py::test_a_second_commander_answering_an_already_resolved_clarification_gets_a_named_conflict
- **Original assertion (one sentence):** After one commander resolves a
  held event's clarification via `/Clarify` (`202`), a second commander's
  later `/Clarify` attempt on the same event is refused with `409`, naming
  the first commander's identity in the conflict message.
- **What changed in setup, and why:** Same shared-helper change, same
  reason.
- **Confirmed original behavior still verified:** Yes, same reasoning —
  this test's own body performs two real, sequential `/Clarify` POSTs
  against a real clarification hold and a real second registered
  commander; none of that changed.
- **Masking-failure check result:** Same deliberate break, same result.
- **Verdict:** Genuinely fine.

### tests/test_integration_hold_restart_and_flow.py::test_events_behind_a_held_event_continue_processing_while_it_waits
- **Original assertion (one sentence):** An event whose classification
  can't be resolved actually reaches a held state
  (`clarification_held is True`) and stays there (`outcome is None`),
  while a second, independently-classifiable event submitted right after
  it still completes to `succeeded` — i.e. one stuck event does not block
  the queue.
- **What changed in setup, and why:** Different shape of change from the
  5 tests above — this one was **not** advanced past the new gate.
  Because the built-in `unclassified` type's required field (`area`) is
  never resolved in this test, the event now stops at the *new*,
  *earlier* checkpoint (`waiting_for_event_data`) rather than reaching
  `clarification_held` at all under this scripted setup. The assertion
  was changed from checking `clarification_held is True` to checking that
  a live, unresolved `event_data` hold exists for the event
  (`fetch_held_event("event_data", ...)["resolved"]` is falsy) — the state
  the event genuinely reaches now.
- **Confirmed original behavior still verified:** Yes, for what this test
  is actually about. Its purpose (stated in its own docstring: "the second
  must complete even though the first is still pending") is a queue/
  concurrency guarantee that one stuck event doesn't block a later,
  independent one — that guarantee is exactly as well demonstrated by a
  stuck `event_data` hold as by a stuck clarification hold; the queue
  treats a held event as held regardless of which hold table it's in. The
  second half of the test (the later event completing) was not touched and
  was not affected by this change. **Minor observation, not a gap that
  needed fixing:** this specific test no longer exercises the combination
  of "queue concurrency" *and* "an event that has actually reached
  `clarification_held`" in the same test — but that combination isn't lost
  coverage overall, since (a) `clarification_held`'s own mechanics are
  thoroughly covered elsewhere (`tests/test_api_holds.py`,
  `tests/test_orchestrator_flows.py`, `tests/test_orchestrator_holds.py`,
  `tests/test_bot_holds.py`), and (b) the queue-concurrency guarantee this
  test specifically targets doesn't depend on which hold kind is involved.
- **Masking-failure check result:** Confirmed no masking, empirically.
  Temporarily made `_apply_required_fields_gate` always `return None`
  (i.e. the gate never creates a hold, regardless of missing fields) — ran
  this test: it failed with `TypeError: 'NoneType' object is not
  subscriptable` (no `event_data` hold was found at all, since the event
  proceeded straight through instead of stopping). Reverted the break; the
  test passes again.
- **Verdict:** Genuinely fine.

**Summary: all 6 tests are genuinely fine — none were masking a gap, and
none needed a further fix.** No production code changes were made in this
pass (both deliberate breaks were reverted immediately after use); no test
assertions were altered beyond what's described above.

---

## Part B — Outstanding items across the whole findings series

**Method:** read all five documents in full plus `docs/progress.md`'s
append-only entries (the actual record of what's been implemented, not
just what a document claims). Grouped by category; each row cites its
source document + item ID. Where `CRITICAL_FIXES_PLAN.MD` gave an item its
own number, that number is included since it's the operational unit most
of this series' work was authorized against.

**Important correction found during this audit** (see "New, unplanned
observation" at the end for detail): `REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md`
Part 1 labels itself "item #6," but `CRITICAL_FIXES_PLAN.MD`'s *actual*
item #6 is a completely different, still-fully-open finding — the
timestamp/"most recent" bug (`DIAGNOSTIC_FINDINGS.MD` A.2). The
required-event-fields mechanism implemented as "Part 1" did *not* close
out the real item #6. The table below reflects the real item #6 as still
open; the required-fields mechanism is listed under its own row.

### Implemented and verified

| Source + ID | Item | Notes |
|---|---|---|
| `TELEGRAM_UX_FINDINGS.MD` A.1 + A.2 / `CRITICAL_FIXES_PLAN.MD` #1 | Onboarding: `/start` handler + Telegram command menu | `bot/app.py`, tested, `docs/progress.md` 2026-09-04 |
| `DIAGNOSTIC_FINDINGS.MD` C.1 (bonus finding) / `CRITICAL_FIXES_PLAN.MD` #2 | Intent-classification evidence-key hardcoding bug | `orchestrator/reasoning.py`, tested |
| `UX_PREDICTABILITY_PLAN.MD` C.3 (finding 2) / `CRITICAL_FIXES_PLAN.MD` #3 | Protocol-selection parser rejects leading reasoning; failure-reason display cap | `orchestrator/reasoning.py`, `bot/interactions.py`, tested |
| `UX_PREDICTABILITY_PLAN.MD` C.3 (finding 1) + `TELEGRAM_UX_FINDINGS.MD` C.2 (outcome-word half) / `CRITICAL_FIXES_PLAN.MD` #4 | Raw internal error text / untranslated outcome & risk words leaking into messages | `bot/app.py`, `bot/interactions.py`, `bot/background_services.py`, tested |
| `TELEGRAM_UX_FINDINGS.MD` C.1 (finding 3, corrects `UX_PREDICTABILITY_PLAN.MD` D.3) / `CRITICAL_FIXES_PLAN.MD` #5 | Async-ACK dead code; queued message now promises a follow-up | `bot/app.py`, tested |
| `TELEGRAM_UX_FINDINGS.MD` B.3 / `CRITICAL_FIXES_PLAN.MD` #7 | Ambiguous event-data-hold reply now fails loudly instead of guessing | `orchestrator/flows.py`, `api/routes.py`, tested |
| `TELEGRAM_UX_FINDINGS.MD` C.1 (finding 2) | Emoji-convention decision, closed 2026-09-04: no emoji anywhere in user-facing messages | Mechanically enforced by `tests/test_messages.py::test_no_emoji_in_any_catalog_message` |
| `UX_PREDICTABILITY_PLAN.MD` B.2 / `CRITICAL_FIXES_PLAN.MD` #8 → `REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md` Part 2 | Original reporter now gets a short, generic notice on an `"uncertain"` outcome, separate from the commander-only detailed one | `persistence/sqlite_store.py`, `api/routes.py`, `bot/contracts.py`, `bot/transports.py`, `bot/interactions.py`, `bot/background_services.py`, tested |
| `UX_PREDICTABILITY_PLAN.MD` A.1 + A.2 / `CRITICAL_FIXES_PLAN.MD` #9 → `REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md` Part 3 | Always-on protocol/reason trailing line on completed report/request results | `bot/interactions.py`, `bot/contracts.py`, `api/routes.py`, tested. Caveat, already flagged in that doc: A.2's own proposed wording used `risk_reason`; the implementation used `protocol_reason` (the *selection* reason) instead — a deliberate reading, not yet confirmed by the user. |
| `REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md` Part 1 | Required-event-fields mechanism: `unclassified` built-in event type, early pre-protocol-selection gate | `orchestrator/flows.py`, `profiles/*`, tested. Does *not* correspond to `CRITICAL_FIXES_PLAN.MD`'s actual item #6 — see callout above. |

### Explicitly deferred by user

| Source + ID | Item | Why deferred |
|---|---|---|
| `DIAGNOSTIC_FINDINGS.MD` A.7 | "Situation picture" shows false-safe status (safety-critical) | Held back from the very first authorization for a future extensible-check design, not a simple prompt tweak — still genuinely open, highest real-world risk item in the whole series |
| `UX_PREDICTABILITY_PLAN.MD` A.3 | Situation-picture fix's proposed message shape | Depends entirely on A.7 landing first; deferred alongside it |
| `DIAGNOSTIC_FINDINGS.MD` A.2 / `CRITICAL_FIXES_PLAN.MD` #6 (the real one) | Timestamp semantics for "most recent" (`occurred_at` vs. receipt time) | Original authorization: "do NOT touch... do not change default behavior here yet" — needs the user's call on which timestamp defines recency. Still fully open — not touched by the required-fields work despite that work's document mislabeling itself "item #6" |
| `UX_PREDICTABILITY_PLAN.MD` B.1 | Silent partial-exclusion caveat for history/recency answers | Depends on the same timestamp decision (A.2/item #6) above — its proposed fix extends the same `occurred_at_is_fallback` field |
| `UX_PREDICTABILITY_PLAN.MD` D.1 + D.2 / `CRITICAL_FIXES_PLAN.MD` #10 | Staged progress updates within the one-message status lifecycle | Explicitly "not implementable as literally proposed without new cross-process plumbing" — large, needs its own design pass |

### Open design question, never resolved

| Source + ID | Item | Notes |
|---|---|---|
| `TELEGRAM_UX_FINDINGS.MD` C.2 (bidi-mixing risk) | Hebrew RTL sentence mixing an LTR hex event ID mid-string | Structural risk confirmed from the template text; visual rendering in a real Telegram client was never checked ("not resolvable from here") — no decision recorded anywhere |
| `TELEGRAM_UX_FINDINGS.MD` D.2 / lower-priority list | No user-side control over notification volume (mute/digest) | A concrete `/quiet`-toggle proposal exists but was never put to the user as a decision to make |
| `DIAGNOSTIC_FINDINGS.MD` B.2 / `UX_PREDICTABILITY_PLAN.MD` B.3 | Viewer-scoped recency/history answers don't say they're scoped | Proposed fix (one-line suffix) exists; no decision on whether/how to build it recorded anywhere |

### Not started, no open question

| Source + ID | Item | Notes |
|---|---|---|
| `DIAGNOSTIC_FINDINGS.MD` D.3 / `CRITICAL_FIXES_PLAN.MD` #11 | LLM/client reuse cache never activates for `openrouter` | Plain, agreed-shape fix (do the provider-capability review, then enable or document) — not started |
| `DIAGNOSTIC_FINDINGS.MD` D.2 | Sequential model-call latency — `operational_decision_mode="merged"` not activated | Config activation, not new code — gated on "live rollout gates" per `docs/progress.md`'s SPEED_PLAN history, not a design question |
| `DIAGNOSTIC_FINDINGS.MD` C.1 (incidental) | `orchestrator/flows.py`'s `process_message` is dead code (never called from `api/routes.py`) | Flagged as "worth removing... separately from this investigation" — optional hygiene, not a live bug |
| `TELEGRAM_UX_FINDINGS.MD` A.3 | No static `/help` command; "what can you do" always pays a full model call | Concrete proposed fix, no ambiguity — just not built |

### Low priority / informational only

| Source + ID | Item |
|---|---|
| `DIAGNOSTIC_FINDINGS.MD` A.1, A.3, A.4, A.5 | Recency/reference routing, History-Agent summarization, conversation-memory staleness guard, caching/connection staleness — all investigated, no issue found |
| `DIAGNOSTIC_FINDINGS.MD` B.3 | Precedent-lookback timestamp handling "partially guarded, not fully audited" |
| `DIAGNOSTIC_FINDINGS.MD` C.2, C.3, C.4 | Capability-answer grounding factuality, ambiguous-reference breadth, bilingual parity — each "needs more investigation," not independently verified |
| `DIAGNOSTIC_FINDINGS.MD` D.1 | Warmup/model-tier assignment — no issue found |
| `DIAGNOSTIC_FINDINGS.MD` D.4, D.5 | Redundant per-turn SQLite reads, per-call CrewAI iteration counts vs. timeout — neither measured |
| `TELEGRAM_UX_FINDINGS.MD` B.1 | `/profile add`'s pipe-separated-field syntax — one remaining typed-syntax friction point |
| `TELEGRAM_UX_FINDINGS.MD` B.2 | Hold-resolution UX — "not an issue for the core mechanism" (buttons, not typed replies) |
| `TELEGRAM_UX_FINDINGS.MD` D.1 | Notification-generator inventory — feeds D.2, not itself a defect |
| `UX_PREDICTABILITY_PLAN.MD` E.1 | Commander vs. viewer answer phrasing — confirmed identical, no divergence risk |
| Terminology drift | Bare `"Event {id}"` (no "ID" suffix) in `notice.uncertain` vs. `"Task ID"`/`"Job ID"` elsewhere — the `"Job ID"`/`"Task ID"` half was unified by item #5's fix; this third form is cosmetic, still present |

### New, unplanned observation

- **Documentation numbering mismatch (not a code bug):**
  `REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md` Part 1 is titled "item #6" and
  its context line claims to close out `CRITICAL_FIXES_PLAN.MD`'s three
  "needs your call" items (#6, #8, #9). But `CRITICAL_FIXES_PLAN.MD`'s
  actual item #6 (line 172 of that file) is "Events with an unresolvable
  timestamp silently vanish from 'most recent'/history answers" — the
  timestamp/recency decision, sourced from `DIAGNOSTIC_FINDINGS.MD` A.2 —
  a completely different topic from the required-event-fields mechanism
  Part 1 actually implements. Items #8 and #9 do match correctly (Parts 2
  and 3 respectively). This session's own earlier report-back to the user
  repeated the same "item #6" label without independently cross-checking
  it against `CRITICAL_FIXES_PLAN.MD`'s text, so the mislabeling wasn't
  caught until this audit. Net effect: no code is wrong, but the real item
  #6 (timestamp semantics) remains exactly as open and deferred as it was
  before this whole document was written — it should not be read as
  resolved anywhere in `docs/progress.md`.

---

## Addendum — `fetch_events_range` fix: two follow-up verification questions

Both questions below were investigated by reading the actual scheduler
code, not by inference. Question 1's investigation surfaced a real,
concrete bug (a crash, not a data-consistency issue) — fixed in this same
pass, since the process note explicitly allowed a fix if verification
revealed an actual problem, and this is a forward-only crash fix, not a
backfill.

### Question 1 — summary-scheduler consistency

**Finding: real bug, now fixed. No backfill or reconciliation is needed —
summaries are naturally re-derivable and nothing already stored is at risk.**

`history/summaries.py`'s `SummaryScheduler.reconcile()` calls
`self._all_events(now)` → `fetch_events_range(...)`, which — after the
item #6 fix — can now return events with `occurred_at=None` (previously
excluded at the SQL level). Reading `reconcile()` line by line:

- The loop that decides *which* daily periods need reconciling already
  guards `if occurred_at is None: continue` (line ~158) — unaffected by
  the fix, since it always skipped such events deliberately.
- The `period_events` list comprehension immediately below it did **not**
  have the same guard: `parse_timestamp(event["occurred_at"])` was called
  unconditionally for every event in the full `events` list. Since
  `parse_timestamp(None)` raises `AttributeError: 'NoneType' object has no
  attribute 'strip'`, **the very next scheduler run after any event with
  an unresolved `occurred_at` exists in the database would crash** — this
  was latent in the old code but unreachable, because the old, buggy
  `fetch_events_range` never returned such an event in the first place.
  Confirmed empirically: reverted the fix, ran a new reconciliation test
  with one such event present, watched it crash with exactly that
  traceback, then restored the fix and confirmed the same test passes.
- Fixed with the same guard already used one loop up:
  `if event.get("occurred_at") is not None and start <= parse_timestamp(...) < end`.
  `history/summaries.py`, one line.
- Added `test_reconciliation_does_not_crash_on_an_event_with_unresolved_occurred_at`
  to `tests/test_history_precedent.py`.

**Why no backfill is needed, once the crash is fixed:** `_daily_stale`
(and `_parent_stale`) only look at `period_events` — which, both before
and after this fix, never contains an event with an unresolved
`occurred_at` (the crash-fix's guard excludes it the same way the period-
determination loop already did). So an event with `occurred_at=None`:
- Never causes an *already-stored* daily/monthly/yearly summary to be
  marked stale or regenerated on its own — no retroactive change, no
  double-count, no conflict with anything already written.
- Is still never indexed into a summary's structured `event_index`
  either — `history.summaries._event_index` has its own, separate,
  pre-existing filter (`if event.get("occurred_at") is not None`, line 30)
  that this fix didn't touch, so which events get *indexed* in a summary
  is completely unchanged from before.
- The one real (and purely additive) behavior change: if a period's
  summary is regenerated for some *other*, legitimate reason (a genuinely
  new event with a real `occurred_at` arrives), `generate_summary`'s own
  internal `fetch_events_range` call will now also pick up any
  unresolved-`occurred_at` event whose `received_at` happens to fall in
  that exact period, and that event's raw content will be visible to the
  History Agent composing the summary *prose* (not the index) — a strictly
  richer summary, never a contradiction of one already stored. If no such
  regeneration is ever triggered, the event simply stays invisible to
  every summary-level (as opposed to raw-query-level) answer forever,
  exactly as it did before this fix — a pre-existing, separate design
  choice (summaries are occurred_at-indexed by design) that this fix
  deliberately did not change, since expanding *what gets indexed* would
  be a redesign, not a bug fix.
- Summaries regenerate deterministically from current database state each
  time `reconcile()` runs (that's the entire point of `_daily_stale`/
  `_parent_stale`) — nothing about them depends on history of *how* they
  were produced, so there is no stale, orphaned, or inconsistent state a
  backfill could even be correcting.

### Question 2 — is the legacy narrative `query()` path actually reachable?

**Finding: yes, genuinely reachable in production today — not dead code.**

Traced real call sites, not just the function's existence:

- `orchestrator/reasoning.py`'s `answer_question` (the **legacy planner
  path — the default** `OptimizationPolicy()` mode, per
  `DIAGNOSTIC_FINDINGS.MD` A.1's own finding) and `answer_question_from_plan`
  (the merged-planner path) both have a fallback branch: when a question
  routes to "agents" rather than the dedicated "history" operation, and
  one of the chosen specialist agents is a `HistoryAgent` instance, they
  call `history_query_service.query(task_text, ...)` directly — the exact
  function this fix touches.
- Both `answer_question` and `answer_question_from_plan` are themselves
  called from real production code, not just tests: `api/routes.py`'s
  `/Msg` handler calls both (selecting between them by planner mode), and
  `orchestrator/flows.py`'s `process_message` calls `answer_question`
  directly for its `"question"` branch.
- `history_agent` is not profile-optional — `profiles/loader.py`'s
  `_construct_core_agents` always constructs one and merges it into the
  same `AgentRegistry` (`api/app.py`: `build_agent_registry(core_agents,
  loaded_profile.agents)`) that `answer_question`'s routing model chooses
  specialists from. So the routing model naming "history_agent" as a
  plain specialist (rather than using the structured "history" operation)
  is a real, live routing outcome, not a hypothetical one.
- **No regression risk beyond the intended fix**: `query()`'s only path
  to persistence is `retrieve_range` → `fetch_events_range`/
  `fetch_events_by_type_area_window`, both already fixed and already
  covered by existing tests that exercise `query()` directly
  (`tests/test_history_query.py::test_query_uses_summary_without_double_counting_and_applies_filters`,
  `::test_partial_day_falls_back_to_raw_events`, both passing) — ordering,
  window computation, and sender-identity scoping are all unchanged; the
  only behavioral difference is that an event with an unresolved
  `occurred_at` is no longer silently excluded.
