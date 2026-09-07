# DEMO_READY — Minimum Path to a Stable Demonstration

## Purpose

This is the shortest credible path to a system you can run and show live,
without it breaking in front of an audience. It is deliberately not
production readiness.

Everything needed to do the work is written out here. This document is
self-contained.

**Nothing here is decided.** Each task lists the decisions to make before
starting the work. Those decisions are the point — don't let them be made
implicitly by whoever implements first.

**Deliberately out of scope** (not needed for a local demonstration, and
worth doing before any real deployment): dependency vulnerability auditing,
secrets management infrastructure, database backup and recovery, network
exposure and TLS, rate limiting and abuse protection, monitoring and
alerting, version tracking, and automated deployment pipelines.

---

# Phase 1 — Prove the system actually works

Without this phase, a demo is a gamble.

## Task 1 — Build the sensor simulator

**Decisions to make first:**
- Does the demo need live, continuous sensor traffic, or is a handful of
  manually-triggered events enough to tell the story? A demo that runs
  itself is more impressive but more can go wrong live.
- Which scenarios must the demo trigger on demand — a normal event, a
  repeated event that closes on precedent, an unclassifiable event that asks
  for clarification, an event needing approval? The simulator should produce
  exactly the ones you plan to show, on command.

**Work:**
- Write a standalone program (not part of the API or bot packages) that
  sends synthetic sensor events, as free-form English text, to
  `POST /Event`.
- Accept a target port as a command-line argument, so it can be pointed at
  any running deployment rather than one hardcoded instance.
- Accept an emission rate (events per second or minute).
- Support a burst mode that sends many events at once — this is what
  exercises serial processing and database write contention.
- Support emitting repeated events with the same classification and area, so
  precedent lookup has real matches to find during a live run rather than
  only in test fixtures.
- Support emitting text that fits no loaded classification, to trigger the
  clarification path live.
- Generate text that reads like a real sensor report, not a fill-in-the-blank
  template. Field extraction is being exercised here too, and templated
  input under-tests it.

---

## Task 2 — Run the end-to-end flow test

**Decisions to make first:**
- None. This is the foundational proof that the whole chain works together,
  and everything else assumes it.

**Work:**
- Drive one event from the simulator through every stage: extraction, risk
  assessment, protocol selection, precedent lookup, task formulation,
  execution, insights, judgment, and the history write.
- Assert on what each stage actually wrote to the event record — not merely
  that the run completed. Confirm all of these are present and correct: the
  classification, the risk level and its stated reason, the selected
  protocol and its stated reason, the precedent result, every execution step
  with its task text and result, the insight text, and the final verdict.
- Confirm a single trace ID connects every log record from ingestion through
  to the final write.
- If this doesn't pass, nothing else on this list matters yet.

---

## Task 3 — Verify the specific paths the demo will show

**Decisions to make first:**
- What is in the demo script? Likely candidates: the clarification path,
  the approval path, message intent handling, precedent closure. Anything
  you won't show doesn't need verifying for the demo specifically.
- Will the demo include a restart while a decision is pending? (Genuinely
  compelling to show — the system surviving a restart with a held event
  intact.) If so, the restart cases below move from optional to required.

**Work** — build only the checks covering flows in your actual script:

*Clarification path:*
- Submit text no classification fits, from both the sensor and Telegram
  sources; confirm both are held rather than forced into the nearest type.
- Submit a sensor event whose stated type isn't in the loaded registry;
  confirm it's held, not silently rejected or accepted.
- Resolve a hold with a valid type; confirm the flow resumes at risk
  assessment with the other extracted fields intact.
- Attempt to resolve with a type outside the registry; confirm refusal.
- Confirm a viewer-level identity cannot resolve a hold.
- Confirm events queued behind a held event kept processing while it waited.
- If showing a restart: restart mid-hold and confirm the held event survives
  and is still resolvable.

*Approval path:*
- Send an event matching a flagged protocol at high risk with a clear match;
  confirm it waits for approval.
- Send an event matching an unflagged protocol; confirm it runs with no
  hold.
- Approve a held run; confirm it resumes at task formulation and completes.
- Reject a held run; confirm it ends declined, with that outcome recorded.
- Confirm a viewer cannot approve.
- Confirm a second commander answering an already-answered hold is told it's
  already resolved, naming who resolved it and when.
- If showing a restart: restart mid-hold and confirm the pending run
  survives and remains answerable.

*Message intent handling:*
- Send a question; confirm it's answered directly with no event created.
- Send a report; confirm it becomes an event classified by extraction
  against the registry.
- Send a viewer's request needing a flagged protocol; confirm it becomes a
  human-activation event and waits for approval.
- Send the identical flagged request as a commander; confirm it runs
  directly.
- Confirm the sender is told, in every case, which of the three
  (question/report/request) their message was taken as.

*Precedent closure:*
- Confirm a repeated low-risk event matches its precedent and closes without
  running its protocol.
- Confirm an identical event above the risk threshold still runs its
  protocol despite the match.
- Confirm a match outside the lookback window is ignored, and that widening
  the window through live settings brings it back into scope.
- Confirm commanders are notified on every precedent-based closure, with the
  matched precedent included.

---

## Task 4 — Confirm profile isolation

**Decisions to make first:**
- Does the demo show two deployments running side by side? If not, skip this
  task for now — a single deployment starting cleanly is enough.
- If yes: two genuinely different profiles (more compelling — the same build
  serving different missions), or the same profile twice?

**Work** — only if showing more than one deployment:
- Run two profiles at once, on separate ports, with separate database files.
- Write events under each and confirm no event from one appears in the
  other's history queries or precedent search.
- Register a user under one profile and confirm the other refuses that same
  identity.
- Confirm the two live settings stores are independent — changing the risk
  threshold in one leaves the other unchanged.

---

# Phase 2 — Make it runnable and presentable

## Task 5 — Package the deployment

**Decisions to make first:**
- What is the demo machine — a laptop, a VM, a server someone connects to?
  This determines what "packaged" needs to mean.
- Does the demo start from a genuinely empty state each time (more
  impressive, riskier live), or from a pre-seeded database with history
  already present (safer, and necessary if you want to show precedent
  matching or history queries without waiting for data to accumulate)?
- If pre-seeded: who creates that seed data, and is it generated by the
  simulator ahead of time or hand-authored?

**Work:**
- Package the backend, the database, and the bot to run together on the demo
  machine.
- Take the profile as a launch argument, and derive every host, port, and
  file path from that profile — the identical build should run elsewhere
  with no code change, only a different profile.
- Verify the package starts correctly from nothing: an empty directory,
  database migrations run, the administration command adds the first
  commander, and the system then serves requests.
- Then, per the decision above, either confirm the from-nothing path is what
  the demo uses, or build and verify a repeatable pre-seeded starting state.

---

## Task 6 — Basic health check

**Decisions to make first:**
- Is a simple "process up, database reachable" check enough, or do you also
  want to see at a glance that the profile loaded and the event queue is
  idle? The latter is more useful when something looks stuck mid-demo.

**Work:**
- Implement a minimal unauthenticated health endpoint on the API, separate
  from the authenticated system-status endpoint (which requires credentials
  and returns far more than a health check needs).
- The point is that you can tell in one second whether the backend is alive
  when something looks wrong, without digging through logs in front of an
  audience.

---

## Task 7 — Startup scripts and a rehearsed reset

**Decisions to make first:**
- How do you restart cleanly mid-demo if something goes wrong — is there a
  single command that resets to a known-good starting state?
- Do the API and bot start with one command or two? Two is fine for a demo,
  as long as it's scripted and rehearsed, not typed from memory.
- Does the demo need graceful shutdown, or is stopping and restarting
  acceptable? For a demo, usually acceptable — but confirm a hard stop
  mid-event doesn't corrupt the database and prevent a restart.

**Work:**
- Write simple, tested scripts on top of the packaged deployment so that
  starting, stopping, and resetting the demo are each a single command
  you've run successfully several times.
- Skip process supervision, restart-on-crash policies, and the full
  process-failure matrix — those matter for real deployment, not a demo.

---

## Task 8 — Logs you can read live, written to the database

**Decisions to make first:**
- **Write contention.** The database uses a serialized single-writer design,
  already verified under concurrent load. Log lines are typically far more
  frequent than events, so routing them through that same writer adds
  significant write volume to the exact bottleneck that serves event
  processing. Decide how to handle this before building: log to the database
  synchronously through the existing writer (simplest, highest contention
  risk), batch log writes and flush periodically (lower contention, logs lag
  slightly behind reality), use a separate database file purely for logs
  (no contention with event processing at all, but loses the ability to join
  logs against event records in one query), or write to both a file and the
  database with different verbosity levels.
- **What gets logged to the table** — everything the system currently logs,
  or only the structured, event-related records (stage transitions, trace
  IDs, outcomes) with lower-level diagnostics staying in a file? A table
  makes sense for records you'll query; it's a poor fit for stack traces and
  debug noise.
- **Schema.** At minimum: timestamp, trace ID, severity, stage/component,
  message. Decide whether the event ID gets its own indexed column (worth it
  if you'll query "show me everything about this event"), and whether any
  structured payload is stored alongside the message text.
- **Growth and cleanup.** A log table grows without bound. Decide the
  retention policy now — delete rows older than N days, cap total rows, or
  accept unbounded growth for the demo's short lifetime and handle it before
  real deployment.
- **Is this a demo requirement or a permanent design decision?** If it's
  primarily so you can query and display logs during the demo, a simpler
  approach may serve. If it's a permanent architectural choice, it deserves
  its own entry in the main work plan rather than living only here.

**Work:**
- Add the log table to the database schema, with a migration, following the
  same pattern every other table in the system uses.
- Implement the chosen write path (synchronous, batched, or separate
  database) per the decision above.
- Confirm the logging write path cannot block or fail event processing — a
  logging failure must never take down or stall a real event. Verify this
  deliberately, by forcing a log write to fail and confirming the event
  still processes.
- Confirm that following one event by its trace ID actually works end to
  end, from ingestion through to the final write, by querying the table.
- Confirm logs are also visible live during the demo (a query you can run,
  or a tail-equivalent) — being in a table is only useful if you can watch
  it while presenting.
- Re-run whatever load testing exists against the system with database
  logging enabled, and confirm the added write volume didn't introduce lock
  errors or lost writes. The previous concurrency verification was done
  without this extra write traffic.
- Skip retention automation, log shipping, and sensitivity review if this is
  demo-only — but record the decision so it isn't forgotten.

---

## Task 9 — A quickstart you've actually followed

**Decisions to make first:**
- Is this for you alone (a personal runbook so you don't have to remember
  steps under pressure), or will someone else need to run the demo?

**Work:**
- Write the exact sequence from fresh checkout to running demo.
- Then follow it start to finish, on a clean machine or a clean directory,
  at least once. The point isn't the document — it's discovering the step
  you forgot before the demo discovers it for you.

---

# Phase 3 — Rehearse

## Task 10 — Full dry run

**Decisions to make first:**
- What's the demo script, beat by beat? Which scenario opens, which is the
  centerpiece, which closes?
- What's the fallback if a model call is slow or fails mid-demo — wait, skip
  to the next scenario, or fall back to a recording?
- How long is the demo, and does the slowest realistic path (a full protocol
  execution with several model calls) fit inside it? Measure this before you
  find out live.

**Work:**
- Run the entire demo end to end, start to finish, at least twice, on the
  actual demo machine, from the actual reset state, following the actual
  script.
- Time it. Note every point where you waited longer than felt comfortable.
- Deliberately break something mid-run — stop the bot, kill the API — and
  confirm you can recover within the demo using the reset from Task 7. This
  is the single highest-value item on this list, because it's the failure
  you can't talk your way out of live.

---

# Suggested order

1. Task 2 (end-to-end flow) — nothing else is meaningful until this passes.
2. Task 1 (simulator) — needed to trigger demo scenarios on demand.
3. Task 3 (verify the demo's specific flows) — scoped to your actual script.
4. Task 5 (packaging) and Task 7 (startup/reset scripts) together.
5. Task 8 (database logging) — do this before Task 6, since the health
   check may want to report on it, and before the dry run, since it changes
   the system's write profile and needs re-verification under load.
6. Task 6 (health check) — your live safety net alongside the logs.
7. Task 4 (profile isolation) — only if the demo shows two deployments.
8. Task 9 (quickstart) — write it as you finalize the above.
9. Task 10 (dry run) — last, and do not skip it.

---

# A note on what this is not

A demo-ready system is not a production-ready one. Completing everything
here means the system runs and can be shown convincingly. It does not mean
it's safe to expose to a network, run unattended, or trust with data you
can't afford to lose. The items listed as out of scope at the top of this
document remain genuinely necessary before any of that.
