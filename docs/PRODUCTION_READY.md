# NEXT_STAGE — Production Readiness

## Purpose and relationship to `docs/work_plan.md`

`docs/work_plan.md` defines Missions 1–9. All nine are complete and
verified — Mission 9 ("Integration and Hardening", §9.1–§9.22, see
`docs/progress.md`'s own entries for what each subtask actually found and
built) provided the *functional* proof that the system behaves correctly
end-to-end, a first packaging pass (§9.21, `api.app.main`/`bot.app.main`),
and operator documentation (§9.22, `docs/operator_guide.md`).

This document covers what's needed **beyond** Mission 9 to run the system in
production. It does not restate Mission 9's tasks. Where a task here
overlaps or depends on a Mission 9 subtask, that's called out explicitly, so
the two plans stay coordinated rather than producing duplicate or
conflicting work.

**Nothing in this document has been decided.** Each task lists the decisions
that must be made before the work starts. Those decisions are the point —
don't let them be made implicitly by whoever implements first.

**Assumed prerequisite:** Mission 9 is complete — confirmed, see above.
Several tasks below build directly on §9.21's packaging (`api.app.main`)
and would conflict with it if built first.

---

## Task 1 — Dependency pinning and vulnerability auditing

**Relationship to Mission 9:** None. Entirely new scope.

**Decisions to make first:**
- Pin transitive dependencies too (full lockfile, e.g. `pip-compile`/
  `uv.lock`), or only direct ones? Full pinning is more reproducible but
  more maintenance.
- Which vulnerability scanner, and what severity threshold fails a build
  versus merely warns?
- Who is responsible for reviewing and acting on scan findings, and on what
  cadence?

**Work:**
- Pin every dependency to an exact version per the decision above.
- Add the chosen scanner and run it against the pinned set; resolve or
  explicitly document every finding.
- Verify what's pinned matches what CI actually installs and tests against
  — drift here already caused one confirmed bug in this project (the
  `run_polling` test failing against `python-telegram-bot==22.8`).

---

## Task 2 — Secrets management

**Relationship to Mission 9:** §9.3 tests that a missing environment
variable fails loudly and names itself. This task covers how secrets are
*supplied* in production, which §9.3 doesn't address.

**Decisions to make first:**
- Where do production secrets live: environment variables from a process
  manager, a `.env` file with restricted permissions, or a real secrets
  manager? This depends on where you're actually deploying, which hasn't
  been decided yet either.
- Is a `.env.example` (documenting variable names with placeholder values)
  wanted in the repo, or does that risk implying real values belong there?
- What's the rotation procedure for the Telegram bot token if it leaks, and
  who can perform it?

**Work:**
- Implement the chosen supply mechanism.
- Audit the repo, including git history and test fixtures, for any
  committed secret.
- Confirm `.gitignore` excludes real secrets files, live database files, and
  generated logs.

---

## Task 3 — Process management and graceful shutdown

**Relationship to Mission 9:** §9.21 packages the backend, database, and bot
to run on localhost for a demonstration. This task takes that package and
makes it survive real operation. **Do not build a competing startup
mechanism** — extend §9.21's, or explicitly decide to replace it and say so.

**Decisions to make first:**
- What runs the processes in production: systemd, Docker/Compose,
  supervisor, a cloud platform's own runner? This is the largest undecided
  question in this document and several other tasks depend on the answer.
- Do the API and bot run as two separate services, or one supervised unit?
  (They're two processes today.)
- On crash, restart automatically and indefinitely, or restart with a
  backoff and alert after N failures?

**Work:**
- Implement the chosen process management around §9.21's package.
- Implement graceful shutdown: on SIGTERM, stop accepting new requests, let
  the current serial-queue item finish, and exit cleanly rather than dying
  mid-write.
- Verify the failure modes: bot dies while API runs, and vice versa.
  Confirm neither corrupts data, and specifically confirm a held event
  awaiting a commander is still resolvable after the bot restarts, not
  orphaned.

---

## Task 4 — Health and readiness endpoints

**Relationship to Mission 9:** None directly, though whatever Task 3 chooses
for process management will likely consume these.

**Decisions to make first:**
- Should the health endpoint be unauthenticated? (A process manager or load
  balancer generally can't authenticate — but an unauthenticated endpoint
  reveals the service exists. Decide what it may and may not disclose.)
- Distinguish "liveness" (process is up) from "readiness" (database
  reachable, profile loaded, ready to serve), or one combined check?
- Does the bot process need its own equivalent, and if so, how is it
  exposed given it isn't an HTTP server today?

**Work:**
- Implement the chosen endpoint(s) on the API, deliberately separate from
  `GET /SYSTEM`, which requires authentication and returns far more than a
  health check should.
- Implement the bot-side check per the decision above.

---

## Task 5 — Logging in a real deployment

**Relationship to Mission 9:** §9.22 documents reading run logs and following
an event by trace ID. That task assumes logs are structured and accessible —
this task makes that true in production. §9.22 should be written after this.

**Decisions to make first:**
- Log to file directly, or to stdout for the process manager to capture?
  (Depends on Task 3's answer.)
- Structured logs (JSON, machine-parseable) or human-readable text? JSON is
  better for tooling, worse for reading directly over SSH.
- Rotation policy: by size, by age, how many kept?
- Do logs need to ship off the machine (an aggregator), or is local disk
  plus manual review acceptable at this system's real scale?
- What's the retention period, and does anything in the logs count as
  sensitive (event text may describe real incidents)?

**Work:**
- Implement the chosen destination, format, rotation, and retention.
- Verify every log line carries what §9.22's trace-ID walkthrough will
  need: timestamp, trace ID where applicable, severity, and stage.

---

## Task 6 — Database backup and recovery

**Relationship to Mission 9:** §9.21 verifies a deployment can start from
nothing (empty directory, migrations run). This task covers the opposite
case: recovering an existing deployment's data.

**Decisions to make first:**
- Backup frequency, and how far back backups are kept?
- Where do backups live — same machine (fast, but useless if the machine is
  lost), or off-machine?
- Are backups encrypted at rest? (Event text may describe real incidents.)
- What's the acceptable data-loss window? This determines frequency and is a
  real operational question, not a technical one.

**Work:**
- Implement the backup mechanism, confirming it runs safely against a live
  system given the serialized-writer design already verified under
  concurrency.
- Implement and actually test a restore: given a backup, bring a fresh
  deployment to that state and confirm it starts and serves correctly. An
  untested restore is not a backup.
- Implement the chosen retention policy so backups don't accumulate
  unbounded.

---

## Task 7 — Network exposure and transport security

**Relationship to Mission 9:** §9.21 targets localhost for the
demonstration. Production means the API is reachable by something that isn't
on the same machine — this task covers that gap, which Mission 9 explicitly
does not.

**Decisions to make first:**
- Is the API exposed publicly, or only on a private network / behind a VPN,
  with the bot as the only client? This substantially changes everything
  below.
- If exposed: is there a reverse proxy (nginx, Caddy) in front, and does it
  terminate TLS?
- TLS certificates: managed automatically (Let's Encrypt), provided
  manually, or terminated upstream by a cloud load balancer?
- Does the sensor traffic (`POST /Event`) come from the same network as the
  bot, or somewhere else with different exposure needs?

**Work:**
- Implement the chosen exposure model.
- If the API is reachable over any untrusted network, confirm every request
  is over TLS — the `X-Identity` authentication scheme in use is only as
  safe as the transport carrying it.
- Verify the deployment can't accidentally bind to a public interface when
  a private one was intended.
- If the admin web panel (`api/admin.py`, `/admin`) is enabled, this applies
  to it at least as much as to the rest of the API: its login password,
  session cookie, and CSRF token all travel in the clear without TLS. Unlike
  `X-Identity`, this is a typed password a person enters in a browser, not a
  value an existing client already sends over whatever transport is chosen
  elsewhere — treat it as a hard blocker for enabling the panel outside
  localhost, not just a "confirm TLS is on" checkbox.

---

## Task 8 — Resource limits and backpressure

**Relationship to Mission 9:** §9.19 tests serial processing under a
simulator burst — a well-behaved load. This task covers hostile or
malfunctioning load, which §9.19 doesn't.

**Decisions to make first:**
- Maximum accepted request body size for `POST /Event` and `POST /Msg`?
- Is rate limiting needed given the real traffic pattern (sensors plus a
  small number of Telegram users), or is the serial queue's natural
  backpressure sufficient?
- If the event queue grows beyond some size, reject new events with an
  explicit error, or keep accepting and let it grow?
- Should a single misbehaving sensor be able to be blocked or throttled
  independently of others?

**Work:**
- Implement the chosen limits.
- Verify that flooding the API cannot cause unbounded memory growth, and
  that whatever rejection behavior was chosen returns a clear, documented
  error per §7.10's existing error contract rather than a bespoke response.

---

## Task 9 — Monitoring and alerting

**Relationship to Mission 9:** §9.20 reviews cost and latency as a one-time
analysis. This task covers ongoing, continuous observation, which Mission 9
doesn't address at all.

**Decisions to make first:**
- What actually needs alerting? Candidates: the API or bot process being
  down, model-call failures spiking, the event queue backing up, a hold
  going unanswered for too long, the summary scheduler failing, disk filling
  up. Which of these matter enough to wake someone?
- Who receives alerts, and through what channel? (Telegram itself is
  tempting, but alerting through the system being monitored is fragile.)
- Is a real monitoring stack wanted, or is "check the logs and the health
  endpoint" acceptable at this scale?
- What's the acceptable time-to-detection for the system being fully down?

**Work:**
- Implement the chosen monitoring and alerting.
- Specifically confirm the case where the system is silently degraded rather
  than down — e.g. the API is up and healthy but every model call is
  failing, or holds are being created but notifications aren't reaching
  anyone.

---

## Task 10 — Versioning and release tracking

**Relationship to Mission 9:** None directly, though it makes §9.21's
deployment verifiable in practice.

**Decisions to make first:**
- What versioning scheme: semantic versioning tied to git tags, commit
  hashes, dates, or something else?
- Should the running version be exposed via the API (in the health endpoint
  from Task 4, or `GET /SYSTEM`), and is that safe given Task 7's exposure
  decision?
- Is a changelog maintained, and by whom?

**Work:**
- Implement the chosen scheme.
- Make the running deployment's version discoverable, so a bug report can be
  correlated with the exact code that produced it, and so a deployment can
  be confirmed to have actually picked up a change.

---

## Task 11 — CI/CD beyond the test suite

**Relationship to Mission 9:** Mission 9's tests will need CI steps of their
own, following the existing per-mission pattern. That's Mission 9's work.
This task is about what CI does *besides* running tests.

**Decisions to make first:**
- Does CI deploy automatically on merge to a release branch, or is
  deployment a deliberate manual step? (A manual step is a legitimate
  choice — but it should be a decision, not an omission.)
- Should CI fail on a vulnerability finding from Task 1, and at what
  severity?
- Are there other gates worth adding: linting, type checking, test coverage
  thresholds? Each has a maintenance cost.

**Work:**
- Implement whatever was decided.
- If deployment stays manual, document that as a deliberate choice in the
  README (Task 12), so it doesn't read as an oversight later.

---

## Task 12 — Developer-facing README

**Relationship to Mission 9:** §9.22 writes *operator* documentation
(profiles, protocols, users, live settings, reading logs). This task is the
*developer/deployer* counterpart: getting from a fresh checkout to a running
system. The two should cross-reference each other rather than overlap.

**Decisions to make first:**
- What's the intended audience — only people who already know this codebase,
  or someone encountering it fresh?
- How much does the README duplicate versus link to §9.22's operator docs?

**Work:**
- Write the README covering: what the system is, in a few sentences;
  installing dependencies; running the test suite; starting a local instance
  from a fresh checkout; where to find the operator documentation.
- Verify every command in it by running it against a genuinely fresh
  checkout — not by reasoning about what should work.

---

## Suggested sequencing

The largest open decision is Task 3's (what runs the processes in
production), because Tasks 4, 5, 6, 7, and 9 all depend on the answer.
Resolve that first, even before starting Task 1.

After that:

1. Tasks 1 and 2 (dependencies, secrets) — quick, and prerequisites for any
   safe first deployment.
2. Task 7 (network exposure) — decide this early; it constrains Tasks 4, 9,
   and 10.
3. Tasks 4 and 5 (health, logging) — needed before Task 3's process
   management can be built meaningfully around them.
4. Tasks 3 and 6 (process management, backup/recovery).
5. Tasks 8, 9, 10, 11 (limits, monitoring, versioning, CI/CD) — can proceed
   in parallel once the above are settled.
6. Task 12 (README) last, since it documents everything above.

**Before starting any of this:** confirm Mission 9's §9.21 (deployment
packaging) is either complete or explicitly deferred. Several tasks here
extend it, and building them first would produce a competing deployment
mechanism that would need to be reconciled later.
