# AgentsHub — Consolidated Critical-Findings Implementation Plan

Planning document only. Synthesizes the critical/high-priority items from
the three completed investigation passes:

- `docs/IMPROVES/DIAGNOSTIC_FINDINGS.MD` (correctness bugs)
- `docs/IMPROVES/UX_PREDICTABILITY_PLAN.MD` (message-content predictability)
- `docs/IMPROVES/TELEGRAM_UX_FINDINGS.MD` (moment-to-moment Telegram UX)

No code was written, edited, or deleted to produce this plan. Per the
user's explicit instruction, the "situation picture" false-safe bug
(`DIAGNOSTIC_FINDINGS.MD` item A.7, and `UX_PREDICTABILITY_PLAN.MD`'s A.3,
which is entirely dependent on it) is deliberately excluded — that item is
being held back for a separate, larger design decision about turning it
into an extensible protocol/flow rather than a quick patch.

Several item IDs (`A.1`, `A.2`, `B.2`, `B.3`, `C.1`, `C.2`, `D.1`, `D.2`,
`D.3`) are reused across all three source documents for *different*
findings — every reference below names the full source document
explicitly to avoid ambiguity.

---

## Recommended implementation order — reasoning

Ordered cheapest-and-highest-impact first, then grouped by shared root
cause so related fixes land together rather than being touched twice.
**#1–#5 are one connected cluster**: everything in it is about the bot
either saying nothing, saying the wrong thing, or dumping raw internal
text at the user — fixing them together (and in this order: cheapest
first, then the two root-cause parsing bugs, then the two message-content
cleanups they feed into) avoids re-touching `bot/app.py` and
`orchestrator/reasoning.py` in separate, overlapping passes. **#6–#7** are
silent-wrong-answer correctness bugs, grouped next. **#8–#10** are
visibility/design improvements — real, but each has an open design
question for you to weigh in on, so they're ordered after the
unambiguous fixes. **#11** is a standalone infra item with no dependency
on anything else, placed last only because it's not user-facing-urgent.

---

### 1. First-contact onboarding: silent `/start`, invisible commands (source: `TELEGRAM_UX_FINDINGS.MD` A.1 + A.2)
- **The problem:** The bot has no handler for `/start` at all — a brand-new
  user's first, most natural action gets literally no response — and it
  never registers a command list with Telegram, so `/profile`/`/settings`
  are invisible unless a user already knows they exist.
- **Where it lives:** `bot/app.py`, `register_handlers` (currently
  registers only `CommandHandler("profile", ...)` and
  `CommandHandler("settings", ...)`, no `"start"`, no `set_my_commands`
  call).
- **What the fix would concretely involve:** Add a `CommandHandler("start",
  ...)` that greets the caller with one short message (branching only on
  whether `resolve_caller` finds them registered — reusing the existing
  `auth.unregistered` text for the unregistered case). Separately, call
  `Bot.set_my_commands([...])` once during `_post_init` with the two real
  commands and short descriptions. Two independent, small additions to the
  same function.
- **Estimated size:** small.
- **Risk if left unfixed:** every new commander/viewer's first experience
  with the bot is silence or guesswork, undermining trust before they've
  sent a single real message.
- **Dependencies/ordering notes:** none — fully independent of every other
  item in this plan; good first PR.

### 2. Report/request submission hard-fails on ordinary messages (source: `DIAGNOSTIC_FINDINGS.MD`, "Bonus finding" filed under C.1)
- **The problem:** The prompt that asks the model to classify a message's
  intent shows a single generic JSON example whose `evidence` object uses
  the literal key `"question"` — the model copies that key verbatim even
  when the real intent is `report`/`request`, and the parser then rejects
  it outright. This reproduced on every real report tried in testing (3/3
  attempts).
- **Where it lives:** `orchestrator/reasoning.py` — `_build_intent_prompt`
  (the hardcoded example, ~line 322) and
  `_parse_structured_intent_response` (the validation that rejects a
  mismatched key).
- **What the fix would concretely involve:** Either give the prompt three
  parallel examples (one per intent, each showing its own evidence key),
  or — the more durable option, matching how the newer merged-planner
  schema already avoids this exact problem — change the expected shape to
  always require all three possible evidence keys rather than one that
  must match whatever intent was determined.
- **Estimated size:** small.
- **Risk if left unfixed:** ordinary field reports and action requests —
  the system's core purpose — routinely fail outright with a raw error
  instead of being processed.
- **Dependencies/ordering notes:** this is the *root cause* most often
  responsible for triggering #5 below (the raw-error-text exposure) —
  fixing this first reduces how often #5's path is even hit, though #5 is
  still worth fixing independently as a safety net for other errors.

### 3. Protocol-selection parser rejects normal model reasoning, dumps it raw on failure (source: `UX_PREDICTABILITY_PLAN.MD`, C.3 "new evidence")
- **The problem:** The legacy protocol-selection parser requires the
  model's *entire* response to be exactly two lines (`SELECTED: ...` /
  `REASON: ...`); real model output commonly reasons through the options
  first, which the parser rejects — and when it does, the model's whole
  ~700-word raw reasoning block gets inserted, verbatim and uncapped, into
  the Telegram message the user receives. Reproduced in 2 of 3 identical
  test runs.
- **Where it lives:** `orchestrator/reasoning.py` —
  `_parse_selection_response` / `_SELECTED_PATTERN` (a strict
  `re.fullmatch`); `bot/interactions.py` — `format_job_result` (inserts
  `result.failure_reason` with no length cap, lines ~103–104).
- **What the fix would concretely involve:** Two independent changes,
  best done together: (a) make the parser tolerant of leading reasoning
  text before the `SELECTED:`/`REASON:` block (anchor the match at the
  end of the string, or explicitly instruct the model to respond with
  *only* the two-line format), and (b) regardless of (a), cap how much of
  any `failure_reason` `format_job_result` ever inserts into a message —
  first sentence only, full detail stays in logs/`DEEP_DEBUG`.
- **Estimated size:** medium.
- **Risk if left unfixed:** a `failed` outcome — an entirely ordinary,
  expected event in this system — can deliver a wall of raw
  chain-of-thought text to a phone, the single worst message-shape
  violation found across all three passes.
- **Dependencies/ordering notes:** part of the same "stop showing broken
  or raw text" cluster as #2, #4, #5 — the length-cap half of this fix
  (b) is generically useful and should land regardless of whether (a) is
  also done.

### 4. Raw/untranslated internal values leak into user-facing messages (source: `UX_PREDICTABILITY_PLAN.MD` C.3 "plan's original ask" + `TELEGRAM_UX_FINDINGS.MD` C.2 — merged, same underlying mechanism)
- **The problem:** Two instances of the same pattern — a value gets
  interpolated into a user-facing template without ever being made
  user-safe or translated. (1) A failed API call's raw internal validation
  string (e.g. `"primary intent 'report' requires exact evidence"`) is
  shown to the user directly, in both languages — the Hebrew template
  still splices in the raw *English* string. (2) A completed job's
  outcome word (`succeeded`/`failed`/`uncertain`/...) is inserted into the
  Hebrew result message without translation, producing sentences like
  "תוצאה: succeeded."
- **Where it lives:** `bot/app.py` (the `except ApiRequestError:
  reply = messages.text("error.request_failed", reason=exc.message)`
  handler); `messages/en.py`/`messages/he.py` (`error.request_failed`,
  `result.verdict`); `history/event_pipeline.py` (`VALID_OUTCOMES`, the
  six fixed English outcome strings, never mapped to Hebrew anywhere).
- **What the fix would concretely involve:** One shared mechanism for
  both: give `RunFailureError`/`ApiRequestError` a short, generic,
  catalog-driven user-safe message distinct from the internal one (which
  stays exactly as detailed as today in logs); add a small six-entry
  outcome-word translation table and route `event["outcome"]` through it
  wherever it's interpolated into a message.
- **Estimated size:** small-medium.
- **Risk if left unfixed:** every raw/technical failure reads as broken
  jargon to any user, and reads even more obviously wrong to a
  Hebrew-reading one — a visible, repeated "this system is buggy"
  signal.
- **Dependencies/ordering notes:** same "stop showing raw/wrong text"
  cluster as #2, #3, #5. Independent of #2/#3's fixes — this is a
  defense-in-depth layer that helps regardless of whether those root
  causes are also fixed.

### 5. Async-ACK message is dead code and doesn't set a follow-up expectation (source: `TELEGRAM_UX_FINDINGS.MD` C.1, corrects `UX_PREDICTABILITY_PLAN.MD` D.3)
- **The problem:** In the function that replies after a report/request is
  queued, an early `return` makes a later `elif` branch unreachable —
  meaning the well-worded text promising "You'll hear back here once it's
  done" is never actually sent. Every real queued report instead gets a
  plainer message with no explicit follow-up promise.
- **Where it lives:** `bot/app.py`, `_submit_and_format_message` (the
  `if submission_result.job_id: return ...` / unreachable
  `elif submission_result.job_id:` two lines later).
- **What the fix would concretely involve:** Delete the unreachable
  branch (dead-code cleanup), and fold its better wording into the
  message that's actually live (`"status.async_ack"`) so the real ACK
  explicitly promises a follow-up, in both languages.
- **Estimated size:** trivial.
- **Risk if left unfixed:** every queued report/request leaves the user
  without a clear signal that a result is still coming — small per
  occurrence, but it's the single most common bot interaction (every
  report goes through this path).
- **Dependencies/ordering notes:** none — fully independent, and about as
  cheap a fix as exists in this plan; good to land alongside #1 early.

### 6. Events with an unresolvable timestamp silently vanish from "most recent"/history answers (source: `DIAGNOSTIC_FINDINGS.MD` A.2)
- **The problem:** "Most recent event" queries order and filter on
  `occurred_at`, which for human/Telegram reports is model-extracted and
  can resolve to nothing; nothing holds such an event for a commander to
  fix, and the search query unconditionally excludes any event with a
  null value there — so it becomes permanently invisible to every recency
  or history question, with no error and no signal that anything was
  omitted.
- **Where it lives:** `history/event_pipeline.py` (`extract_event`, the
  `occurred_at = received_at if source == "sensor" else
  model_occurred_at` line, and `determine_clarification_hold` in
  `orchestrator/holds.py`, which checks only `classification`, never
  `occurred_at`); `history/query.py` (`answer_most_recent_event`); 
  `persistence/sqlite_store.py` (`_search_where`'s `IS NOT NULL` clause).
- **What the fix would concretely involve:** When a Telegram report's
  `occurred_at` can't be resolved, fall back to receipt time instead of
  leaving it null (the `occurred_at_is_fallback` field already exists for
  exactly this, currently hardcoded to `False`). Separately — and this
  needs your call, not a unilateral pick — decide whether "most recent"
  should be defined by `occurred_at` or by receipt time going forward, and
  apply that choice consistently everywhere recency is computed.
- **Estimated size:** medium.
- **Risk if left unfixed:** the system can confidently answer "what's the
  last event" with a wrong, stale answer and no indication anything was
  excluded — the original bug this whole investigation series was
  commissioned to explain.
- **Dependencies/ordering notes:** independent of the message-formatting
  cluster (#1–#5); the "which timestamp means recency" decision may also
  affect precedent-lookback logic (flagged, not fully audited, in
  `DIAGNOSTIC_FINDINGS.MD`'s B.3 — see lower-priority list).

### 7. A free-text reply can silently update the wrong pending report (source: `TELEGRAM_UX_FINDINGS.MD` B.3)
- **The problem:** When a report is missing required details, resolving it
  by free-text reply picks the single most-recently-created matching
  pending item for that sender/conversation with no check for whether
  more than one is waiting — a reply meant for an earlier report can get
  silently applied to a different, more recent one instead.
- **Where it lives:** `api/routes.py`, lines ~205–216 (the
  `event_data_hold` matching loop in the `/Msg` handler).
- **What the fix would concretely involve:** Either (a) let a reply
  reference which event it's answering (echo a short ID in each
  clarifying question, expect it back), or (b) the simpler option that
  matches this codebase's existing preference for avoiding ambiguity by
  construction: allow only one in-flight `event_data_hold` per
  conversation/sender at a time, queuing a second one's question until the
  first is answered.
- **Estimated size:** medium.
- **Risk if left unfixed:** a commander's correction to one report can be
  silently misapplied to an unrelated one, with no indication it happened.
- **Dependencies/ordering notes:** independent of everything else in this
  plan; note that approval/clarification holds (button-driven) are
  already immune to this by construction, so no similar fix is needed
  there.

### 8. "Uncertain" outcomes are commander-only and dump the full raw insight with no headline (source: `UX_PREDICTABILITY_PLAN.MD` B.2)
- **The problem:** An uncertain verdict does generate a distinct notice,
  but only to commanders — never to the original reporter if they're a
  viewer — and it inserts the entire raw insight text with no leading
  one-line summary, which in live testing ran to several hundred words.
- **Where it lives:** `bot/interactions.py`
  (`notify_uncertain_verdict`/`format_uncertain_verdict_notice`);
  `persistence/sqlite_store.py` (the outcome→notification-kind mapping);
  `bot/interactions.py`'s `format_job_result` (never selects the
  `uncertain_verdict` `MessageKind` even for the reporter's own copy).
- **What the fix would concretely involve:** Decide (your call) whether a
  viewer reporter should get the full commander-level notice or a shorter
  version, then extend delivery to them either way; separately, change the
  notice template to lead with a short extracted/truncated headline before
  the full insight, and give `format_job_result` a real `"uncertain"`
  branch so even the reporter's ordinary copy is labeled honestly instead
  of a generic `[RESULT]`.
- **Estimated size:** medium.
- **Risk if left unfixed:** a viewer who files a report that comes back
  uncertain — potentially flagging a real problem, as the live-captured
  example did — never finds out, and even commanders get an
  unreadable-at-a-glance wall of text.
- **Dependencies/ordering notes:** none blocking; touches the same
  `format_job_result` function as #3 and #5, worth being aware of if
  sequencing PRs to avoid merge conflicts, but not required to be done
  together.

### 9. No "why" reaches the user for an ordinary completed report/request (source: `UX_PREDICTABILITY_PLAN.MD` A.1/A.2)
- **The problem:** Risk level and protocol-choice reasoning are already
  shown on hold notifications, but never on an ordinary completed
  report/request — the common case carries zero explanation of what was
  decided or why.
- **Where it lives:** `bot/interactions.py`, `format_job_result` (would
  need `risk_level`/`risk_reason`/`selected_protocol_name` added to
  `bot/contracts.py`'s `JobResult`, sourced from data `api/routes.py`'s
  `_job_payload` already reads off the event row).
- **What the fix would concretely involve:** This has a genuine, open
  design choice flagged in the source finding rather than a single
  obvious answer: either (a) an always-on one-line suffix on
  protocol-triggering results only (small, matches the existing
  `approval.flagged` wording style), or (b) a "why?" follow-up that
  reuses the existing conversation-memory reference resolver to answer
  on demand (keeps the default message exactly as short as today, costs a
  small new question-route). The source document recommends (b) for a
  phone screen but flags this as yours to decide.
- **Estimated size:** medium (either option; (b) is somewhat larger than
  (a)).
- **Risk if left unfixed:** no functional risk — a predictability/trust
  gap: a commander can't tell why the system did what it did without
  digging into `DEEP_DEBUG`.
- **Dependencies/ordering notes:** needs your decision on (a) vs (b)
  before sizing precisely; otherwise independent.

### 10. Staged progress updates for long-running reports need new plumbing (source: `UX_PREDICTABILITY_PLAN.MD` D.1/D.2, distinct from `DIAGNOSTIC_FINDINGS.MD`'s same-numbered D.1/D.2)
- **The problem:** The existing "one status message, one edit" lifecycle
  only covers the sub-second synchronous ACK for a queued report — the
  actual multi-stage pipeline runs afterward, in a different process (the
  API's background queue worker), with no live channel back to the bot to
  support any further edits, and the eventual result always arrives as a
  brand-new message. A "this is taking a while" signal (`C.2` in the same
  document) has the identical root gap.
- **Where it lives:** `bot/app.py`/`bot/presentation.py` (status-message
  lifecycle); `api/routes.py` (queue submission, lines ~382–410);
  `bot/background_services.py` (notification dispatch — has no concept of
  a non-terminal progress event today).
- **What the fix would concretely involve:** New, but reuses existing
  infrastructure rather than inventing a new stream: (1) the bot
  remembers `event_id → (chat_id, status_message_id)` locally after
  sending the ACK; (2) the queue worker emits a new, non-terminal
  notification kind (e.g. `"stage_progress"`) at 1–2 chosen checkpoints
  (or on a time threshold, for the "taking a while" case), delivered
  through the existing long-poll notification mechanism; (3) the bot's
  poll loop gets a new branch that looks up the remembered message ID and
  calls the existing single-edit primitive again instead of sending a new
  message.
- **Estimated size:** large.
- **Risk if left unfixed:** no functional risk — a report that takes 90
  seconds looks identical to one that takes 30, which is a real but
  lower-stakes predictability gap than the correctness/message-content
  items above.
- **Dependencies/ordering notes:** the best checkpoint candidates
  (`protocol_selection`, `step_execution`/`insight_generation`) were
  identified by measuring live latency variance
  (`UX_PREDICTABILITY_PLAN.MD` C.1) — worth a quick re-check of that data
  before picking exact checkpoints, since it may shift if #2/#3 above
  change how often/where the pipeline fails early.

### 11. The LLM/client reuse cache never activates for the configured provider (source: `DIAGNOSTIC_FINDINGS.MD` D.3)
- **The problem:** The performance cache built for reusing LLM client
  objects across calls is gated on a `thread_safe_client` capability flag
  that is `False` by default and is never set `True` for any provider —
  including `openrouter`, the one actually configured — so the cache is
  provably inert for every current deployment.
- **Where it lives:** `agents/contracts.py` (`ProviderCapabilities`,
  `provider_capabilities`); `agents/runtime.py` line ~246 (the sole
  gating read).
- **What the fix would concretely involve:** Either do the
  source/concurrency review this codebase's own cache rules already
  require before marking a provider safe (needs particular care for
  `openrouter` specifically, since it proxies to various upstream
  providers — OpenAI-compatible thread-safety can't just be assumed to
  transfer), and mark it `True` if it holds up; or, if that review isn't
  done now, document plainly that the cache currently provides no benefit
  for any shipped profile, so it isn't mistaken for active infrastructure
  later.
- **Estimated size:** small-medium (the code change itself is trivial;
  the review it depends on is the part with real, hard-to-bound scope).
- **Risk if left unfixed:** no correctness risk — a standing, currently-
  wasted performance opportunity; the D.2 latency trace in
  `DIAGNOSTIC_FINDINGS.MD` shows warm-call latency is a real, measurable
  cost, so this isn't purely theoretical.
- **Dependencies/ordering notes:** fully standalone; can be picked up any
  time independent of everything else in this plan.

---

## Also worth knowing about, lower priority

- Sequential model-call latency is real (~39s/7 calls per report), but no
  model-tier misassignment — the already-built "merged" decision mode
  would shrink it, just isn't turned on.
- Rolling-summary staleness doesn't leak into recency answers — routing
  already keeps that separate.
- Conversation-memory's "always re-read the DB, never trust a remembered
  reply" guard is confirmed working correctly.
- No read-after-write staleness risk found in the persistence layer;
  asking about an event while it's still queued is expected, not a bug.
- Viewer-scoped answers don't currently say they're scoped — a one-line
  suffix fix is proposed but not yet built.
- Precedent-lookback's own timestamp handling looks partially guarded,
  but wasn't fully audited — worth a closer look, especially alongside
  item #6 above.
- "What can you do" capability-answer factuality (does the model ever
  invent or omit a capability) — not independently verified.
- Ambiguous-reference clarification breadth (too narrow or too eager) —
  not independently verified.
- Bilingual (Hebrew) parity for every fix above still needs its own
  verification pass once the English-side fixes land.
- Redundant per-turn SQLite reads — not measured in this pass.
- Per-call CrewAI iteration counts vs. the 30s timeout — not measured;
  no call came close to timing out in the one trace captured.
- Commander vs. viewer answers are generated from identical prompt logic,
  confirmed — no divergent-phrasing risk found.
- No user-side control over notification volume exists at all (no
  mute/digest) — a concrete `/quiet`-toggle proposal is on file if wanted.
- `/profile add`'s seven-pipe-separated-field syntax is the one remaining
  typed-syntax friction point — approval/clarification are already
  button-driven and fine.
- Minor message-terminology drift ("Task ID" vs. bare "Event {id}") and
  the fact that no emoji exist anywhere in the message catalog today
  (worth a deliberate, one-time style decision rather than an accidental
  first instance).

---

## Confirmation

No source file in this repository was created, edited, or deleted to
produce this plan — only this one new document,
`docs/IMPROVES/CRITICAL_FIXES_PLAN.MD`, was written. The three source
findings documents were read but not modified.
