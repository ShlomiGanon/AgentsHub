# Server Report — Verification Audit

Investigation-only audit of the whole codebase against `docs/work_plan.md`
§1.1–§9.22 and `docs/progress.md`. No code, test, or other doc file was
modified to produce this report — this file is the only artifact of the
pass.

**Method.** `docs/progress.md` is itself an unusually detailed, largely
self-critical build log — most subtasks already carry an honest
"Deviations" note naming exactly what's simplified, stubbed, or
unverified. Re-deriving all ~130 subtasks from scratch would mostly
reproduce that log. So this audit (a) spot-verified a representative,
risk-weighted sample of progress.md's claims directly against the running
code and test suite (architecture boundaries, every real entry point,
composition wiring, the two hardest-to-fake claims — trace-ID propagation
and criticality validation — plus several "done" subtasks picked because
their claims were specific and checkable), and (b) did an independent,
non-claim-driven sweep for the two failure modes the task explicitly
warns about: code with no production caller, and cross-cutting gaps no
single subtask entry would surface on its own. Two real, previously
unreported findings came out of (b) — §1.8's specific-event logging, and
`orchestrator.flows.process_report`/`process_request`/`process_message` —
detailed below. Full suite confirmed green during this audit:
**732 passed, 0 failed**, `tests/test_architecture.py` passing.

---

## Part A — Subtask verification

### A.0 — How to read this section

For the large majority of subtasks, independent verification (source
reading, grep-based cross-referencing of every claimed function/class,
and running the relevant tests) confirmed progress.md's own account is
accurate: the code named exists, does what the entry says, and the named
tests exercise the distinguishing behavior rather than a happy path or an
import. Those are listed compactly by mission below, with the specific
evidence checked. Subtasks with a real gap, a stale claim, or a deviation
worth a second look get their own detailed subsection.

### A.1 — Mission 1: Foundations (1.1–1.10)

| # | Subtask | Verdict | Evidence checked |
|---|---|---|---|
| 1.1 | Repo skeleton, import-graph check | Fully implemented & tested | `tests/test_architecture.py`'s `ENTRY_POINTS` dict matches `docs/allowed_calls.md` entry-for-entry (both read in full, cross-checked field by field); test passes for real. |
| 1.2 | Domain vocabulary | Fully implemented | `docs/vocabulary.md` exists. |
| 1.3 | Base configuration | Fully implemented & tested | `config/base.py`; deployment-specific values correctly absent. |
| 1.4 | Profile structure | Fully implemented & tested | `profiles/spec.py`, `docs/profile_spec.md`. |
| 1.5 | Profile loading and selection | **See §A.1.5 below** | |
| 1.6 | Startup validation | Fully implemented & tested | `profiles/loader.py:100`, `isinstance(protocol.criticality, CriticalityLevel)` confirmed present by direct read (this is the fix work_plan.md §1.6 itself documents finding necessary — verified it is actually in the file, not just described). |
| 1.7 | Runtime settings store | Fully implemented & tested | `config/settings_store.py`; independence confirmed via `tests/test_integration_profile_isolation.py` (two real `SettingsStore` instances). |
| 1.8 | Structured logging and run tracing | **Not fully implemented — see §A.1.8 below** | |
| 1.9 | Permission model | Fully implemented & tested | `auth/permissions.py` — one `is_permitted(level, action)` shared function, ordered `PermissionLevel` enum. |
| 1.10 | User administration command | Fully implemented & tested | `cli/user_admin.py`; confirmed no equivalent exists in `api/` or `bot/` (grep for `def (add|create|remove|delete)_user` across both packages: zero matches). |

#### A.1.5 — 1.5 Profile loading and selection: stale progress.md deviation

**Status: fully implemented — but progress.md's own entry is stale and
should say so.** The 1.5 entry (docs/progress.md:80-89) says core-agent
construction "is a documented no-op seam
(`profiles/loader.py::_construct_core_agents`) returning an empty
mapping... This is a stub, not a completed requirement." That was true
when written (Mission 1, before the Agent Framework existed). It stopped
being true during Mission 6: `orchestrator.flows.assemble_core_agents`
(added in the "Merge remediation" entry, `orchestrator/flows.py:109`) now
genuinely merges the Main, History, and Insights agents, and
`api/app.py:76` calls it as part of real startup wiring
(`build_context`). Confirmed by reading `api/app.py:64-105`: profile
loading → `assemble_core_agents` → `build_agent_registry` → `FlowDeps` →
queue started → scheduler started, all real, no stub. The 1.5 entry
itself was never revisited to record this — see Part C.

#### A.1.8 — 1.8 Structured logging and run tracing: genuinely still partial

**Status: partially implemented, and the "will land with §5/§6" promise
was never kept.** `tools/logging_config.py` (JSON formatter, profile-name
stamping) and `tools/tracing.py` (trace-ID contextvar) are real and
tested (`tests/test_logging.py`). Trace-ID propagation into real request
handling was separately verified this session (§A.9.2 below) and is
genuinely wired. But the eleven *specific* named events §1.8 requires —
intent decision, extraction result naming empty fields, risk
level+reason, protocol selection+reason, hold kind, precedent lookup
(window/matches/closure), per-step task+result, tool calls including
blocked ones, retries with cause, insight text, final verdict — are
**still not logged anywhere in production code**, six missions after the
1.8 entry said "those call sites land with §5/§6."

Verified directly: `grep -r "logger\.\(info\|debug\|warning\|error\)"`
returns **zero matches** in `orchestrator/` and **zero matches** in
`history/` — the two packages that own essentially every event this
bullet list names (intent, extraction, risk, selection, holds, precedent,
step task/result, insight, verdict). The only packages with any
`logger.*` calls at all are `protocols/executor.py` and
`protocols/executor.py` (covers "every retry with the cause" and
plausibly tool-call logging via `agents/base.py`, the only other file
with logger calls) — a small fraction of the list. `api/` has none
either. `tests/test_logging.py` only exercises the generic
formatter/tracing mechanism with synthetic `logging.getLogger("test")`
calls; no test anywhere asserts that a real intent decision, risk
assessment, or verdict is actually logged. This is a real, unclaimed gap
— not a "looks complete but isn't wired" trap (nothing calls itself
done), but the Mission-status table still marks Mission 1 "Done" with no
asterisk against it. See Part C.

### A.2 — Mission 2: Data Layer (2.1–2.13)

All 13 subtasks: **fully implemented and tested**, confirmed by direct
reading of `persistence/schema.py`, `persistence/sqlite_backend.py`,
`persistence/interface.py`, `persistence/schema.py`,
`registries/event_types.py`, `registries/areas.py`,
`fixtures/seed_events.py`, and `tests/test_persistence_conformance.py`
(the backend-swap suite, confirmed engine-agnostic — no SQLite import in
the test file). Specific checks: `fetch_held_event(kind, event_id)`
(§2.13) present in both `persistence/interface.py` and
`persistence/sqlite_backend.py` and exercised in
`tests/test_persistence_conformance.py`; the composite
`(classification, area)` index (§2.8) present in `persistence/schema.py`;
the `[start, end)` half-open range convention (a Mission-5-prerequisite
follow-up, §2.6/2.7/2.9/2.10) is the same convention `history/query.py`
depends on and later Bug 1/Bug 2 fixes (§9.19/§9.20, below) had to work
within — consistent end to end.

### A.3 — Mission 3: Agent Framework (3.1–3.12)

All 12 subtasks: **implemented as specified, but with one pervasive,
honestly-disclosed caveat that this audit independently confirmed still
holds**: `crewai` is not installed in this environment (`pip show crewai`
/ `import crewai` both fail; `requirements.txt` keeps it commented out).
Every subtask touching the live model boundary (3.5 CrewAI adapter, 3.6
model routing, 3.10 timeout/error translation, 3.9's `UNCLEAR_TASK:`
sentinel convention) is implemented against crewai's *documented* API and
tested via a monkeypatched fake standing in for the real module — never
against a real install. This is called out per-subtask in progress.md
already; what this audit adds is confirmation that **the caveat is still
live today**, not resolved since, and that it is the single largest
correctness risk in the whole system (see the cross-cutting note in the
final priority list) because it also silently covers every Main
Agent/History Agent/Insights Agent prompt convention built in Missions 5
and 6 (risk scoring, protocol selection, task formulation, success
judgment, intent classification, summarization) — none of these prompt
formats have ever been run against a live model. 3.1/3.2/3.3/3.4/3.7/3.8/
3.11/3.12 (the non-model-boundary subtasks) are fully verified with no
caveat: `agents/base.py`'s per-call `allowed_tools` enforcement (3.7) via
a contextvar was read directly and is a real security check, not
decorative; `agents/reference.py`'s two stub tools (read-only
`check_status`, side-effecting `record_action`) match §3.11's bullets
exactly.

### A.4 — Mission 4: Protocol Engine (4.1–4.8)

7 of 8 fully implemented and tested as described (`protocols/model.py`,
`protocols/loader.py`, `protocols/editor.py`, `protocols/executor.py`,
`protocols/executor.py`, `profiles/demo.py`). One item needs an update:

#### A.4.6 — 4.6 Retry exhaustion handling: stale "partially done" status

progress.md's own 4.6 entry (written during Mission 4, before the
orchestrator existed) says "partially done — executor-level behavior
only," explicitly deferring "write partial results onto the event
record," "notify the originator," and "move on to the next event" to
§6.11/§8.11 (neither existed yet). Both now do, and this audit confirmed
the wiring is real: `orchestrator/flows.py`'s `continue_from_risk_assessment`
calls `record_step_execution` inside the step loop (so partial results
are written incrementally, not only at the end) and
`record_event_outcome(..., "failed", failure_reason=...)` on exhaustion;
`bot/notifications.py` + the `job_failed` notification kind
(`api/operations.py`) cover the originator notification; the serial
queue (§6.15) already guarantees the next event proceeds regardless. §9.14's
integration test (`tests/test_integration_retry_exhaustion.py`) exercises
this exact path for real and confirms a second event still completes
after the first exhausts. **4.6 is effectively fully done**, but its own
progress.md entry has never been updated or superseded to say so — flagged
for Part C.

### A.5 — Mission 5: History System (5.1–5.10)

All 10 subtasks: **fully implemented and tested**, subject to the same
crewai-unverified caveat as Mission 3 for `agents/history.py`'s prompt
behavior and `history/extraction.py`'s model-driven field extraction
(both real code, tested via injected fakes/`model_invoker` stand-ins,
never a live model). Verified directly: `history/query.py::find_precedents`
uses the hierarchical summary-first lookup §5.8 requires (read directly,
not assumed); `history/query.py`'s range-scoped assembly (§5.9) uses
half-open UTC intervals; `tests/test_history_fidelity.py` runs the real
three-level pipeline over the seed dataset and checks contradiction
preservation, not just "it ran."

### A.6 — Mission 6: Main Agent Orchestration (6.1–6.15)

All 15 subtasks: **fully implemented and tested** in their final form.
Note for context, not a finding: eight of these (6.2, 6.5, 6.6, 6.9, 6.11,
6.12, 6.13, 6.14) have two progress.md entries each — an early "not
implemented this mission, blocked on §5" entry followed later by a real
"done" entry once §5 landed. This is the log's own documented,
intentional append-only pattern ("if a subtask is revisited later, add a
new entry... never edit or remove a prior entry") and is not a
discrepancy — the later entry is authoritative and this audit confirmed
the later entries' claims (e.g. `orchestrator/holds.py`'s clarification
functions, `orchestrator/precedent.py`'s three independent closure checks
read directly at `orchestrator/precedent.py`, `orchestrator/flows.py`'s
full new-event flow) are all real, present, and tested. One genuine
finding inside this mission's code, found independently (not claimed or
disclaimed anywhere in progress.md):

#### A.6.11 — Dead code: `orchestrator.flows.process_report`/`process_request`/`process_message`

`orchestrator/flows.py` defines three functions —
`process_report` (line 217), `process_request` (line 260), and
`process_message` (line 278, which calls the other two) — implementing a
**fully synchronous, single-call** version of the new-event/message flow.
They are fully built, and `tests/test_orchestrator_flows.py` exercises
them directly (17 call sites). But **no production entry point calls
them**: `grep`-ing the whole repository for calls to any of the three
(excluding their own mutual calls and the test file) returns nothing.
`api/ingestion.py`'s own module docstring says so explicitly: it "Composes
the split primitives (`begin_report`/`run_report_extraction`,
`begin_request`/`continue_from_risk_assessment`) itself rather than
calling `orchestrator.flows.process_message`, which runs a report or
request synchronously start to finish — exactly what §7.2 exists to avoid
blocking a request on." `api/ingestion.py` and `api/operations.py` independently
confirmed to use the same split-primitive pattern, never these three.

This is not a bug — the split primitives are the real, correct,
production-used implementation, and the all-in-one functions are a
plausible earlier design that §7.2's async-job requirement superseded.
But it is exactly the pattern the audit brief calls out by name: fully
built, fully tested, genuinely never reached from any real entry point.
Recommend either deleting `process_report`/`process_request`/
`process_message` (and their 17 dedicated test call sites) or documenting
them explicitly as a deliberately-retained alternate/reference
implementation — currently neither is done, so a future reader has no way
to tell "orphaned" from "intentionally kept" without this kind of audit.

### A.7 — Mission 7: API Layer (7.1–7.12)

All 12 subtasks: **fully implemented and tested**, verified directly:
`api/app.py`'s `build_context`/`build_app` wire all 8 blueprints
(events, messages, jobs, holds, protocols, system, users, notifications);
`api/errors.py`'s `ApiError` hierarchy is the only way `api/` code raises
an HTTP-visible failure (confirmed by reading `api/errors.py` — 7
subclasses, three `error_class` values matching §7.10's three-class
requirement); §7.12's `steps_completed`/`failed_step_agent_name` fields
confirmed present in `api/operations.py` (`_steps_completed`,
`_failed_step_agent_name` helper functions, lines 33/45, wired into the
response body at lines 68-77).

### A.8 — Mission 8: Telegram Frontend (8.1–8.14, +8.15)

All 14 numbered subtasks plus the unnumbered 8.15 (`HttpApiClient`):
**fully implemented and tested**, and — the claim most worth
independently checking, since it's the one that would be easiest to
silently leave stubbed — **`bot/app.py` genuinely constructs the real
`HttpApiClient`, not a fake, as its production default**: confirmed at
`bot/app.py:31` (`from bot.http_api_client import HttpApiClient`) and
`bot/app.py:76` (`api_client = HttpApiClient(f"http://localhost:{loaded_profile.api_port}")`)
inside `build_deps`, the function every real bot process calls.
`bot.api_client.UnimplementedApiClient` still exists in the codebase but
only as a deliberately-retained test double (`tests/test_bot_notifications.py`'s
poll-loop-degrades-gracefully test needs a null object whose every method
raises) — not a leftover stub masquerading as a real path; confirmed it
is never constructed inside `bot/app.py`'s own production code, only in
tests. `bot/notifications.py::NotificationCursorStore` confirmed
wired into `bot/app.py:294` (`register_handlers`'s `_post_init`), not
just defined and tested in isolation. `api/operations.py`'s
`fetch_notifications_since` and the `notification_log` table (migration
8) confirmed present in `persistence/sqlite_backend.py`,
`persistence/interface.py`, and exercised by
`tests/test_persistence_conformance.py`.

### A.9 — Mission 9: Integration and Hardening (9.1–9.22)

All 22 subtasks: **fully implemented and tested**. This mission was built
during the same session as this audit and its claims were the most
directly checkable; three specific, high-value spot-checks:

#### A.9.2 — Trace ID: genuinely wired, not just built

Confirmed directly (not merely trusted from progress.md): `api/ingestion.py`,
`api/ingestion.py`, and `api/operations.py` all import and call
`tools.tracing.trace_context`/`new_trace_id`, and it is not merely
imported-but-unused — `api/ingestion.py`'s `post_event` and
`api/ingestion.py`'s `post_msg` wrap both their synchronous prefix and
their queued continuation's closure in `with trace_context(trace_id):`.
This is the fix work_plan.md itself documents as closing a real,
previously-existing gap (the mechanism existed since Mission 1 but had no
real caller until this mission) — independently confirmed the fix is
actually present in the file, matching exactly the "trace-ID mechanism"
example the audit brief names as the pattern to watch for. `docs/api_spec.md`'s
Service-identity section and `docs/operator_guide.md`'s "Reading the run
logs" section both correctly describe the *current* behavior (a hold
resumption gets a fresh trace ID, not a continuation).

#### A.9.19/9.20 — Bugs 1 and 2: fixes confirmed present in code, not just narrated

`history/interface.py` re-exports `storage_timestamp` from
`history.time_utils` (confirmed present — satisfies the layering fix,
also independently confirmed against `tests/test_architecture.py`'s
passing `ENTRY_POINTS`, which lists only `history.interface`/`history.query`
as `history`'s entry points). `history/query.py::find_precedents`'s
window-widening fix and `api/ingestion.py`/`api/ingestion.py`'s `_now()`
both read directly and confirmed to match the described fix, not just
asserted by the log.

#### A.9.21 — Deployment: real launch entry points confirmed for all four processes

`api/app.py:138`, `bot/app.py:304`, `cli/user_admin.py:50`, and
`tools/simulator.py:162` each define a real `main(argv)` with a matching
`if __name__ == "__main__":` guard — confirmed by direct grep across all
four files, not assumed from the module names. This closes the exact gap
work_plan.md documents finding (`api/app.py` previously had no runnable
launch path at all).

#### A.9.CI — CI file coverage: verified mechanically, currently accurate

`.github/workflows/ci.yml`'s claimed invariant ("every `test_*.py` file
appears in exactly one mission step") was checked mechanically this audit
(diffed the file listing against every path referenced in the workflow
file): **zero files unreferenced, zero files referenced more than once**.
This invariant was violated twice earlier in the session (per
progress.md's own account) and is confirmed genuinely restored, not
merely claimed restored.

---

## Part B — Is the system actually connected end to end?

### B.1 — Entry-point trace

| Entry point | Traced to | Verdict |
|---|---|---|
| `python -m api.app <profile>` | `api.app.main` → `create_app` → `build_context` (real profile load, real persistence, real registry, real queue+scheduler started) → `build_app` (all 8 blueprints registered) → `Flask.run()` | Real, reaches production code, no stub. |
| `python -m bot.app <profile>` | `bot.app.main` → `build_deps` → real `HttpApiClient` (confirmed, §A.8) → `register_handlers` (message handler, `/profile`, `/settings`, callback-query handler, notification poll loop with a real `NotificationCursorStore`) | Real. |
| `python -m cli.user_admin --profile <profile> add/update/remove/list` | `cli.user_admin.main` → `profiles.loader.load_profile` (same loader the system uses, per §1.10's own requirement) → `persistence.interface.open_persistence` → direct user-table writes | Real; confirmed no code in `api/` or `bot/` duplicates this write path (§A.1's grep). |
| `python -m tools.simulator --port ...` | `tools.simulator.main` → `_post_event` → real `POST /Event` over `urllib` against a running API process | Real; not a fake traffic generator. |
| `POST /Event` | `api/ingestion.py::post_event` → `orchestrator.flows.begin_report` (sync) → queued `run_report_extraction` → `history.interface.extract_event`/`record_*` → risk/selection/precedent/holds/executor/insights/judgment via `continue_from_risk_assessment` | Real, full chain confirmed (§A.6, §A.9.2). |
| `POST /Msg` | `api/ingestion.py::post_msg` → `orchestrator.main_agent.classify_intent` → question path (`orchestrator.question_flow.answer_question`, synchronous) or report/request path via the **same** split primitives `POST /Event` uses (not `process_message` — see §A.6.11) | Real; converges correctly, per §7.5's own dedicated convergence test. |
| `POST /Approve/<id>` / `POST /Clarify/<id>` | `api/operations.py` → `orchestrator.flows.resolve_approval`/`resolve_clarification` → `fetch_held_event` by event ID → resume via `continue_after_approval`/`continue_after_clarification` | Real. |

### B.2 — Code with no production caller

- **`orchestrator.flows.process_report`, `process_request`, `process_message`** — fully built, fully tested (17 test call sites in `tests/test_orchestrator_flows.py`), never called from `api/`, `bot/`, `cli/`, or `tools/`. Detailed at §A.6.11. This is the one significant instance of the exact failure mode the audit brief names by example (trace-ID mechanism, `api.app.build_context()`); everything else checked (`assemble_core_agents`, `fetch_notifications_since`, `NotificationCursorStore`, `HttpApiClient`, the four `main()` entry points, the trace-ID context managers) was confirmed to have a real production caller, not just a test caller.
- No other orphaned public entry-point-module code was found in this pass. `agents.runtime`, `agents.runtime`, `protocols.executor`, `profiles.loader`, `history.time_utils`, `history.query`, and other declared-internal modules are correctly *not* entry points and are reached only from within their own package — consistent with `docs/allowed_calls.md` and confirmed by `tests/test_architecture.py` passing.

### B.3 — Composition-point verification

| Composition point | Proven by | Real? |
|---|---|---|
| Profile loading → running app | `api/app.py::build_context` (read directly, §A row above); `tests/test_integration_deployment.py` (real empty-directory-to-serving-request test) | Yes |
| Bot's HTTP client → real API routes | `bot/http_api_client.py::HttpApiClient` against `tests/api_fakes.py::RunningApiServer` (a real `werkzeug` socket server) — `tests/test_bot_http_api_client.py` (23 tests, every method) | Yes |
| API → orchestrator → persistence | `orchestrator/flows.py`'s use of `history.interface.record_*` functions exclusively (never a raw `persistence.update_event` from orchestrator code — confirmed by grep: only `history/interface.py` and `history/write.py` call `persistence` write operations directly from within `orchestrator/flows.py`'s call graph) | Yes |
| Queue actually driving flows | `api/app.py::_dispatch_queue_item` + `SerialEventQueue.start()` (line 93); `tests/test_integration_serial_processing_under_load.py` (real 25-event burst, overlap-detection lock, not inferred from timing) | Yes |
| Scheduler actually running against real history | `api/app.py:95-96` (`SummaryScheduler(persistence, history_agent); scheduler.start()`) inside real startup wiring, not just constructed in a test; `tests/test_integration_history_accuracy.py` drives a real `reconcile()` pass end to end | Yes |

### B.4 — Dead code / orphaned modules / leftover scaffolding

- `orchestrator.flows.process_report`/`process_request`/`process_message` — see §A.6.11/§B.2. Recommend a decision (delete or explicitly document as intentional) rather than leaving it ambiguous.
- `bot.api_client.UnimplementedApiClient` — **not** dead; confirmed a live, intentional test double (§A.8). No action needed, but worth noting since it superficially looks like exactly the kind of leftover placeholder this section should flag — it isn't one.
- No other orphaned modules found. `docs/DEMO_READY.md` exists alongside `docs/PRODUCTION_READY.md` and was not investigated in depth this pass (out of scope of work_plan.md's own subtask list) — worth a quick sanity check that its content is current, but not reviewed here.

---

## Part C — Does `docs/progress.md` tell the truth?

### C.1 — Entries claiming something done that's actually not/partially done

- **§1.8**, via the Mission-status table's blanket "Mission 1 ... Done.**
  The 1.8 entry itself already says "partially done," naming the eleven
  specific events it still owes. That promise was never fulfilled — see
  §A.1.8. This is the one place in the whole document where the summary
  table's claim ("Done") and the underlying entry's own claim ("partially
  done") directly disagree, and six missions of subsequent work never
  reconciled them.

### C.2 — Entries overstating what was verified

None found beyond what's already self-disclosed. The document is
unusually careful about labeling unverified-against-a-live-model claims
(3.5, 3.6, 3.10, and every Main-Agent/History-Agent prompt-format
decision in 5.x/6.x) — every one of the roughly dozen "unverified prompt
convention" notes checked was in fact still unverified (crewai still not
installed, §A.3) and none of them overclaims live verification it didn't
do.

### C.3 — Subtasks built but with no progress.md entry at all

None found. Every subtask 1.1–9.22 has at least one entry (some have two,
per the append-only "not implemented this mission" → later "done"
pattern, which is by design and not a gap — see §A.6).

### C.4 — Deviation/limitation/deferred-item entries that are now stale

- **§1.5**'s "core-agent construction is a stub" note — resolved by
  Mission 6's `assemble_core_agents` and never marked resolved in the 1.5
  entry itself. See §A.1.5.
- **§4.6**'s "partially done — executor-level behavior only" — the
  remaining pieces (partial-result persistence, originator notification,
  queue continuation) are all real today via §6.11/§8.11/§8.12. See
  §A.4.6.
- Neither is misleading in isolation (both correctly describe the state
  *at the time they were written*, and the log's own header says never to
  edit a prior entry), but both currently read, out of context, as live
  limitations they no longer are. A short "superseded by §X, see that
  entry" addendum (as an *new* append-only entry, not an edit) would close
  this without violating the log's own append-only rule.

### C.5 — Internal contradictions, including against the Mission-status table

- The one substantive contradiction is §C.1 (1.8 vs. the status table).
- No other contradiction found between the Mission-status table and the
  entries below it — all nine rows' "Done" claims check out against this
  audit's own verification, with 1.8 as the sole exception.

---

## Prioritized findings

**Genuine correctness/safety-relevant gaps:**

1. **§1.8 — operational logging is materially incomplete.** No
   production code in `orchestrator/` or `history/` logs the intent
   decision, extraction result, risk assessment, protocol selection,
   hold kind, precedent lookup, per-step task/result, insight text, or
   final verdict — despite `docs/operator_guide.md` telling an operator
   to "find any log line mentioning the event... filter the whole log
   stream to that one [trace ID] value." Today, for most of a run, there
   would be nothing to find. This is the single most concrete, fixable
   gap this audit surfaced. (§A.1.8, §C.1)
2. **Crewai has never been installed or exercised.** Every AI-driven
   judgment in the system — extraction, risk scoring, protocol selection,
   task formulation, success judgment, intent classification,
   summarization, insight generation — is implemented against a
   documented-but-unverified API and tested exclusively via mocks/fakes.
   This is honestly and repeatedly disclosed throughout progress.md
   per-subtask, but never stated once as the single cross-cutting fact it
   is: **no part of the "AI agent" behavior this system exists to run has
   ever actually run.** Not a new discovery, but worth stating plainly in
   one place, since a reader skimming the Mission-status table's nine
   "Done" rows would not otherwise realize this. (§A.3)

**Cleanup / cosmetic (no functional risk):**

3. `orchestrator.flows.process_report`/`process_request`/`process_message`
   — dead from a production standpoint; either delete (with their 17 test
   call sites) or document as intentional. (§A.6.11, §B.2, §B.4)

**Documentation accuracy only (code is fine, the log text is outdated):**

4. §1.5's and §4.6's progress.md entries describe stale limitations that
   were resolved by later missions and never marked as such. (§C.4)

No other correctness, security, or architectural gap was found in this
pass. The composition points that most commonly hide "looks complete but
isn't wired" failures — profile→app startup, bot→API over real HTTP,
API→orchestrator→persistence, the serial queue, and the summary scheduler
— were each independently confirmed wired to real code, not stubs (Part
B).
