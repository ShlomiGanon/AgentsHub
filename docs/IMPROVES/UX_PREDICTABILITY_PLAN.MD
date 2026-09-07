# AgentsHub — UX & Predictability Investigation Findings

Second read-only investigation pass, per `UX_PREDICTABILITY_PLAN.md`. Same
discipline as the first pass (`docs/IMPROVES/DIAGNOSTIC_FINDINGS.MD`): every
finding is grounded in the real, current source or a live reproduction
against an isolated scratch profile/DB — never the real deployment data.

**Investigation date:** 2026-09-04

**Method note:** two isolated reproduction runs were made this pass, both
against scratch copies of `profiles.demo` (ports 8977/8978, scratch SQLite
DBs under `%TEMP%`, real OpenRouter model calls, `DEEP_DEBUG=true`), never
against `data/demo_profile.db`. Where a live trace or a literal message
catalog string is quoted below, it's copied verbatim from the current
source or a captured trace.

---

## Executive Summary

| Priority | Item | Verdict |
|---|---|---|
| **Critical** | **C.3 (new evidence)** — a `failed` outcome's raw, multi-paragraph LLM reasoning text reaches the user verbatim | **Confirmed cause**, reproduced live in 2 of 3 identical repeated runs. The legacy protocol-selection parser (`_SELECTED_PATTERN.fullmatch`) rejects any response that includes reasoning before the `SELECTED:`/`REASON:` lines — which real model output frequently does — turning the whole raw ~700-word chain-of-thought dump into `event["outcome_failure_reason"]`, which `format_job_result` then inserts into the Telegram message **verbatim, unformatted, with no truncation**. |
| **Critical** | **C.3 (plan's original ask)** — the first pass's report/request-classification 422 also reaches the user raw | **Confirmed cause.** `bot/app.py`'s `except ApiRequestError: reply = messages.text("error.request_failed", reason=exc.message)` puts the server's internal validation string (`"primary intent 'report' requires exact evidence"`) directly into the reply, in both languages (the Hebrew template still splices in the raw **English** internal string). |
| High | **A.1** — reasoning visibility inventory | Risk level, protocol name, and protocol/ambiguity reasoning **are** shown today, but only on the two hold-notification paths (`approval_needed`, and implicitly `no_match`) — never on an ordinary completed report/request. `format_job_result` (the common-path formatter) includes only the outcome verdict, steps, and insight text — no risk level, no protocol name, no "why this protocol." |
| High | **B.2** — is "uncertain" status ever user-visible | **Partially handled, with a gap.** An `"uncertain"` outcome generates *two* notifications (`persistence/sqlite_store.py`'s `_OUTCOME_NOTIFICATION_KINDS`) — a generic `job_finished` and a distinct `uncertain_verdict` notice with its own `[NOTICE — uncertain verdict — no reply needed]` header. But the distinct notice (a) goes to **commanders only**, never the original (possibly viewer) reporter, and (b) dumps the full raw insight text with no leading one-line summary — violating the plan's own "essential fact in the first line" rule. |
| High | **D.1/D.2** — status-message lifecycle and staged-update feasibility | Precisely mapped: the "one status message, one edit" lifecycle applies **only** to the synchronous HTTP round-trip. For reports/requests, that round-trip ends in well under a second with just the "queued" ACK (`ctx.queue.submit(...)` then an immediate `202` — confirmed by reading `api/routes.py` lines 382–410). The entire multi-stage pipeline (the first pass's 7-call chain) runs **after** that response, inside the API's background queue worker, with **no live channel back to the bot process**, and the eventual result is delivered as a **new** message (`send_reply`/`send_text`), never a further edit of the original status message. **The plan's D.2 proposal (edit the same status message at checkpoints) is not implementable as literally stated without new cross-process plumbing** — see D.2 below for exactly what that would take. |
| Medium-High | **C.1** — latency variance across repeated runs | 3 identical sensor events, same run: `protocol_selection` showed the widest spread (3,010ms, range 3.46s–6.47s) of the three stages measured across all 3 runs; `extraction` (823ms spread) and `risk_assessment` (445ms spread) were comparatively stable. 2 of 3 runs terminated early (see the critical C.3 finding above), so only 1 data point exists for `task_formulation` onward — reported honestly as partial data, not extrapolated. |
| Medium | B.1 | Total-absence-of-results is handled honestly (`_cant_answer_reply`, a real "I don't have a way to answer that" message) — but *partial*, silent omission (the first pass's A.2 NULL-`occurred_at` exclusion) has no equivalent signal; there is no code path that can express "some records may be missing" versus "these are all the records." |
| Medium | B.3 | Confirmed silent: `sender_identity_filter` narrows the SQL criteria with nothing passed into the History Agent's prompt or the final answer text indicating scoping happened. |
| Medium | A.2, A.3 | Design proposals only (per the plan's own "document, don't implement" framing for this pair) — see entries for concrete one-line templates, insertion points, and the always-on vs. on-request trade-off evaluation. |
| Low-Medium | C.2 | Confirmed: no threshold-based "still working" edit exists anywhere in the code for a message still processing — the user sees the "queued" ACK and then nothing until the separate completion notification arrives, however long that takes. |
| Low | D.3 | The existing ACK wording is already fairly good (`"bot.job_queued": "Job ID: {job_id}. You'll hear back here once it's done."`) — sets a clear expectation of a follow-up. Minor phrasing tightening proposed, not a fix for a real gap. |
| Low | E.1 | Not an issue — commander and viewer answers are generated from identical prompt-building code; only the underlying filtered data differs, matching README's stated design. |

**Recommended order to act on this document**, given what was actually
found (adjusted from the plan's proposed order now that C.3 turned out to
have two confirmed, severe instances): the two C.3 findings first (raw
internal/LLM text reaching users is the most acute predictability failure
found), then B.2 (uncertain-status visibility gap), then A.1/A.2 (why
suffix), then D.1/D.2 (status lifecycle, now that its real shape is mapped),
then the rest.

---

## Part B — Silence and false confidence

*(investigated first, per the plan's own Part F ordering)*

### B.1 — Inventory every place the system can go silent instead of saying "I don't know"

- **File(s)/function(s) examined:** `orchestrator/reasoning.py`
  (`_cant_answer_reply`, line 1490; `answer_question`, lines 1495–1608),
  `history/query.py` (`query_spec`, lines 421–422: `if not events: raise
  HistoryQueryError(...)`).
- **What the code actually does:** When a history query's SQL criteria
  match **zero** rows, `query_spec` raises `HistoryQueryError`, which
  `answer_question` catches and turns into `_cant_answer_reply(str(exc))` —
  a real, honest reply: `"I don't have a way to answer that. no stored
  events match the requested history filters."` This is not silence — it's
  a working, if slightly stiffly-worded, "I don't know."
  However, this mechanism only fires on **total** absence of matches. The
  first pass's A.2 finding (an event with `occurred_at = NULL` silently
  excluded by the `IS NOT NULL` clause while *other* events still match)
  and A.5 finding (an event still on the async queue, not yet indexed) are
  both cases where the query **succeeds** with a non-empty, merely
  *incomplete* result set — nothing in `query_spec`, `search_events`, or
  `answer_most_recent_event` can distinguish "this is the complete,
  authoritative result" from "this is what matched, but something may be
  missing" — there is no signal of partial-result uncertainty anywhere in
  the return path (`HistoryAnswer` carries `total_events_matched` and
  `truncated`, but `truncated` only means "more matched than were shown,"
  never "some records were structurally excluded from matching at all").
- **Verdict:** contributing factor — the *total-absence* case is already
  handled honestly; the *silent partial-exclusion* case (which is exactly
  what makes the first pass's A.2 bug dangerous) has no corresponding
  signal anywhere in the codebase.
- **Evidence:** Code reading of the three functions/lines named; the first
  pass's A.2 entry for the concrete exclusion mechanism.
- **Proposed fix:** Not a new mechanism from scratch — extend
  `HistoryAnswer`/`ExtractionResult`'s existing `occurred_at_is_fallback`
  field (already proposed as part of the first pass's A.2 fix) so that when
  a `search_events` result is built from a criteria set where any known
  event was excluded solely for a `NULL` value in the ordering column (a
  simple `COUNT(*) WHERE occurred_at IS NULL` alongside the main query,
  scoped the same way), the History Agent's prompt includes a one-line
  caveat instruction, and the returned answer can carry a short trailing
  clause like *"(N record(s) with an unresolved time weren't included)"* —
  only when that count is nonzero, never as boilerplate.
- **Telegram shape check:** One short trailing clause, appended only when
  the condition is true — matches the plan's "short suffix, not a caveat
  paragraph" rule. Example: `"The most recent event was X.\n\n(1 report
  with an unresolved time wasn't included in this check.)"` — two lines,
  not a report.
- **Suggested regression test:** Submit two events, one with an
  unresolvable timestamp (per the first pass's A.2 regression test), ask a
  recency question, and assert the answer both names the correct event
  *and* includes the new caveat clause (once the A.2 fallback fix and this
  caveat are both implemented — until then, assert the caveat is present
  even with the current NULL-exclusion behavior, to prove detectability).
- **Priority:** medium.

### B.2 — Check whether "uncertain" event status is ever user-visible

- **File(s)/function(s) examined:** `persistence/sqlite_store.py` (line
  79–84, the outcome-to-notification-kind mapping — confirmed:
  `"uncertain": ("job_finished", "uncertain_verdict")`), `api/routes.py`
  (`_uncertain_verdict_payload`, lines 987–989; `_job_payload`, lines
  1015–1024), `bot/interactions.py` (`format_uncertain_verdict_notice`,
  lines 415–424; `notify_uncertain_verdict`, lines 427–431;
  `format_job_result`, lines 98–113), `messages/en.py` (`header.result`,
  `header.uncertain_verdict`, `notice.uncertain`, `result.verdict`).
- **What the code actually does:** An `"uncertain"` outcome is not silent —
  it produces **two separate Telegram messages**: (1) the ordinary
  `job_finished` notification, formatted by `format_job_result`, whose
  header is the generic `"[RESULT]"` (`format_job_result`'s `kind` is only
  ever `"result"` or `"declined"` — `"uncertain_verdict"` exists as a
  `MessageKind`/header string but is **never selected** by this function),
  body `"Verdict: uncertain"` plus steps/insight; and (2) a distinct
  `uncertain_verdict` notification, `"[NOTICE — uncertain verdict — no
  reply needed]\n\nEvent {event_id} finished with an uncertain
  verdict.\n\nInsight:\n{insight}"`, sent via `notify_uncertain_verdict` —
  but **only** to `await deps.api_client.list_commander_chat_ids()` — every
  registered commander, never the original reporter if they're a viewer,
  and never anyone by way of the ordinary per-sender delivery the other
  notification kinds use.
- **Verdict:** contributing factor (not fully silent, but has two real
  gaps: viewer-invisibility, and an unbounded-length raw insight dump with
  no leading summary).
- **Evidence:** Live-captured in the first pass's own A.7 reproduction — the
  fire report's insight text (several hundred words, quoted in full in
  `DIAGNOSTIC_FINDINGS.MD`'s A.7 entry) is exactly what `{insight}` would
  interpolate into `notice.uncertain` verbatim, with no summarization or
  leading headline.
- **Proposed fix:** Two changes: (1) if a viewer submitted the event, add
  their own chat to the delivery list for `uncertain_verdict` too (or, more
  conservatively, deliver a shorter viewer-appropriate version, since a
  viewer may not need the full commander-level insight detail — this is a
  policy decision to confirm with the user, not unilaterally picked here).
  (2) Change `notice.uncertain`'s template to lead with a **one-line
  headline extracted or truncated from the insight**, with the full insight
  text either omitted for a normal chat message or made available on
  request (see A.2's "why?" follow-up idea, which applies equally well
  here) — not inline by default. `format_job_result` should also be given
  a fourth `MessageKind` branch for `outcome == "uncertain"` so the header
  itself reads `"[NOTICE — uncertain verdict...]"` instead of the generic
  `"[RESULT]"` even on the `job_finished` copy, so a reporter's *own* first
  message (which everyone gets, viewer included) is honestly labeled.
- **Telegram shape check:** Point (2)'s headline-first version is the shape
  this plan wants; without it, today's `notice.uncertain` is a clear
  violation of the "first line has the essential fact" rule when the
  insight is long (which the live-captured example shows it commonly is).
- **Suggested regression test:** Trigger an uncertain-outcome event as a
  registered viewer's own report; assert the viewer's chat receives *some*
  message calling out the uncertain status (today: it does not, only
  commanders do — this test would currently fail, documenting the gap).
  Separately, assert `notice.uncertain`'s first line/paragraph is under a
  fixed short-length budget regardless of insight length.
- **Priority:** high.

### B.3 — Check viewer-scoping disclosure

- **File(s)/function(s) examined:** `history/query.py` (`query_spec`,
  lines 366–465, especially `criteria = self._criteria(normalized,
  sender_identity=sender_identity_filter)`; `_history_agent_prompt` — the
  function that actually builds the History Agent's prompt text, called
  with only `context_instruction`, `question`, and `views`, never the
  scoping filter itself).
- **What the code actually does:** `sender_identity_filter`, when set (a
  viewer's own identity), narrows the SQL `WHERE` clause before any query
  runs. Nothing about that restriction is threaded into
  `_history_agent_prompt`'s instruction text or into the returned
  `HistoryAnswer.answer` string — the History Agent is simply handed
  whatever (already-filtered) `views` list came back and writes its answer
  as if that list is the complete picture, because from its own vantage
  point, it is. There is no code path anywhere in this pipeline that knows
  both "a caller-narrowing filter was applied" *and* "an answer is about to
  be returned" at the same point where it could inject a disclosure clause.
- **Verdict:** confirmed cause (of the plan's stated concern).
- **Evidence:** Code reading of `query_spec`/`_history_agent_prompt`;
  `answer_most_recent_event` has the identical shape (`sender_identity_filter`
  only ever reaches the SQL criteria).
- **Proposed fix:** In `answer_question`/`answer_question_from_plan`
  (`orchestrator/reasoning.py`), after getting `caller_sender_identity_filter`
  from the caller (already computed in `api/routes.py` at the point the
  question is dispatched), append a fixed short suffix to the returned
  answer text — not a prompt change (avoids relying on the model to
  remember to mention it) — whenever `caller_sender_identity_filter is not
  None`: e.g. `"\n\n(showing only events you reported)"`. A pure
  post-processing append is simpler, cheaper, and more reliable than
  prompting the model to self-report scoping, and guarantees the clause
  appears exactly once, in a fixed wording, every time scoping was
  actually applied.
- **Telegram shape check:** One short parenthetical clause appended once —
  matches the plan's explicit ask ("one short clause, not a policy
  explanation"). Needs a Hebrew equivalent in `messages/he.py` (not written
  here, per this pass's read-only constraint) — flagging that both
  catalogs need the new key together.
- **Suggested regression test:** Ask the same recency question as a
  registered commander and as a registered viewer (in a two-sender
  scenario where the true most-recent event was submitted by someone else);
  assert the viewer's answer contains the new suffix and the commander's
  does not.
- **Priority:** medium.

---

## Part A — Decision transparency (why did it do that?)

### A.1 — Inventory what reasoning is currently visible vs. hidden

- **File(s)/function(s) examined:** `bot/interactions.py`
  (`format_job_result`, lines 98–113; `format_approval_prompt`, lines
  373–405; `format_no_match_notice`, lines 434–447), `messages/en.py`
  (`approval.risk`, `approval.flagged`, `notice.no_match`, `result.verdict`).
- **What the code actually does:**
  - **Approval holds** (`format_approval_prompt`): shows `"Risk: {risk_level}
    ({risk_reason})"` plus, for a flagged protocol, the protocol name —
    shown to **commanders only** (the only ones who can answer an approval
    hold).
  - **No-match notices** (`format_no_match_notice`): shows risk level/reason
    and the rejection reason.
  - **Ordinary completed results** (`format_job_result`, the path every
    successfully-processed report/request eventually reaches): shows only
    `header`, `"Verdict: {outcome}"`, `steps_completed`, and `insight_text`
    — **no risk level, no protocol name, no reasoning for why that protocol
    was chosen over another.** This is the common case (a routine report
    that doesn't hit a hold) and it currently carries zero "why" information
    at all.
  - **Precedent-based auto-closure**: `_precedent_closure_payload`
    (`api/routes.py` lines 992–1001) carries `matched_precedent_event_id`
    and `precedent_ending` — check whether `bot/interactions.py` actually
    formats and sends this (a `precedent_closure` notification kind exists
    in the same `_OUTCOME_NOTIFICATION_KINDS` mapping) — **not fully traced
    to a message-formatting function in this pass**; flagged as needing one
    more read before A.2's fix touches this path too.
- **Verdict:** confirmed — reasoning is (a) never shown on the common
  completed-result path, (b) shown to commanders only on hold paths.
- **Evidence:** Code reading of the functions/lines named.
- **Proposed fix:** See A.2.
- **Suggested regression test:** N/A (inventory item).
- **Priority:** N/A (feeds A.2).

### A.2 — Design a short, Telegram-appropriate "why" suffix

- **Proposed insertion point:** `bot/interactions.py`'s `format_job_result`
  — add one optional line after the verdict line, before steps/insight,
  populated only when the underlying `JobResult` carries the needed fields
  (today it doesn't — `bot/contracts.py`'s `JobResult` dataclass would need
  `risk_level`/`risk_reason`/`selected_protocol_name` added, sourced from
  `_job_payload` in `api/routes.py`, which already has this data available
  on the `event` row it reads — this is a payload/dataclass extension, not
  new computation).
- **Proposed wording**, matching the existing `approval.flagged`
  precedent's exact style (`"Protocol flagged for approval: {protocol_name}\n{risk}"`,
  where `{risk}` is `"Risk: {risk_level} ({risk_reason})"`): a new catalog
  key, e.g. `"result.why": "Protocol: {protocol_name} ({risk_level} risk,
  {risk_reason})"` — matching the plan's own example shape almost exactly.
- **Unconditional vs. conditional — explicit recommendation:** attach this
  line **only** to protocol-triggering outcomes (reports/requests that
  actually selected and ran a protocol) — i.e., only inside
  `format_job_result`, never inside `answer_conversationally`'s reply path
  or `answer_question`'s reply path. Plain Q&A and conversation replies
  have no "protocol chosen" concept at all, so there's nothing to attach —
  this isn't really a close call.
- **"Why?" follow-up — evaluated:** the conversation-memory reference
  resolver (confirmed working correctly for the active `legacy` planner
  path per the first pass's A.1/A.4 entries) already resolves "that event"
  / demonstrative references to a stable Event ID using only the remembered
  turns — a literal `"why?"` follow-up in the same conversation would need
  no new plumbing to resolve *which* event it's asking about, since it's
  the most recently discussed one. What it *would* need: a new question
  type/route that, given a resolved Event ID, answers specifically "why was
  this protocol chosen" from the stored `risk_reason`/`protocol_reason`
  fields (`orchestrator/flows.py`'s `record_event_state`'s
  `STATE_UPDATE_FIELDS` already persists both) — this is a small, well-scoped
  new capability, not a redesign.
- **Recommendation:** given the "short and to the point" rule and that the
  underlying data (`risk_reason`, `protocol_reason`) is already persisted
  per-event, the **on-demand "why?" follow-up is the better fit for a phone
  screen** — it keeps the default message exactly as short as it is today.
  The one-line always-on suffix is the simpler implementation and still
  Telegram-appropriate (one line, matching existing `approval.*` style) but
  adds a line to *every* protocol-triggering result whether the user wants
  it or not. **This is exactly the trade-off the plan's own ground rules
  ask to flag rather than resolve unilaterally** — recommending the "why?"
  follow-up, flagging the always-on suffix as the simpler fallback if the
  user prefers not to add new routing.
- **Telegram shape check:** Always-on suffix: adds exactly one line to
  protocol-triggering results only — small, bounded, matches existing
  precedent. "Why?" follow-up: adds nothing to the default message; the
  follow-up's own answer should itself stay to 1–2 sentences (the raw
  `protocol_reason`/`risk_reason` strings observed in this and the prior
  pass's traces are already single sentences, so no further truncation
  logic is obviously needed — worth confirming once real traffic exists).
- **Suggested regression test:** For the always-on variant: submit a report
  that triggers a protocol, assert the reply contains a `"Protocol:"` line
  with the correct name/risk. For the follow-up variant: submit a report,
  then ask "why?" in the same conversation, assert the reply names the
  protocol and cites the actual persisted `risk_reason`/`protocol_reason`.
- **Priority:** high.

### A.3 — Check the situation-picture fix's message shape specifically

- **Proposed template**, building on the first pass's A.7 fix (which adds a
  history route checking non-terminal outcomes) and matching this
  codebase's existing "most urgent fact first" precedent
  (`approval.flagged`/`notice.no_match` both lead with the actionable fact,
  not a preamble):
  ```
  ⚠️ Unresolved: fire report, North Sector, 4 min ago (uncertain — see below)

  Everything else: nominal (South Sector checked, no anomalies).
  ```
  Two short lines/blocks: the single most urgent open item first (with
  area + elapsed time + outcome word), then a compact all-clear summary for
  the rest — not a table (the current live-reproduced bug's answer used a
  Markdown table, which is exactly the "structured report" shape the plan's
  ground rules warn against for a phone). If there is **no** open item, the
  reply should just be the second line alone ("Everything nominal — no open
  reports.") — never lead with an empty/reassuring headline when there's
  nothing to report, and never omit the headline when there is.
- **Verdict:** design proposal (not implemented; the underlying fix it
  depends on — first pass's A.7 — is also not implemented yet).
- **Evidence:** First pass's A.7 entry (the live-captured false-safe
  answer, which used a Markdown table — direct evidence of what shape to
  move *away* from), plus this codebase's own `approval.flagged`/
  `notice.no_match` precedent for "lead with the actionable fact" style.
- **Proposed fix:** Once A.7's routing fix (history route for non-terminal
  outcomes) lands, the situation-picture answer's composition step should
  be given an explicit instruction (in whatever prompt assembles the final
  answer) to lead with any non-terminal event found, using the
  elapsed-time-since-`occurred_at` + area + outcome shape shown above, and
  to keep the "everything else" summary to one line — not a per-area
  table.
- **Suggested regression test:** Per the first pass's A.7 regression test
  (submit a high-criticality event, ask for the situation picture) —
  additionally assert the reply's **first line** (not just "somewhere in
  the reply") names the open event, and assert the reply does not contain
  Markdown table syntax (`|` column separators) for a single-open-item
  case.
- **Priority:** high (depends on A.7 being fixed first; the shape work
  itself is small).

---

## Part D — Progress visibility for longer-running actions

*(investigated before Part C, per the plan's own ordering, since D.1 is a
prerequisite for C.2)*

### D.1 — Map the current status-message lifecycle precisely

- **File(s)/function(s) examined:** `bot/presentation.py` (`replace_status`,
  full file, 40 lines), `bot/app.py` (`present_incoming_message`, lines
  ~230–290 area — status sent, then replaced once with `_submit_and_format_message`'s
  result), `api/routes.py` (lines 382–410, the report-handling branch),
  `messages/en.py`/`messages/he.py` (`status.thinking`, `status.async_ack`,
  `bot.job_queued`).
- **What the code actually does:**
  1. On any incoming free-form message, the bot sends **one** status
     message: `messages.text("status.thinking")` — English: `"The model is
     thinking..."`; Hebrew: `"המודל חושב..."`.
  2. `present_incoming_message` then makes **one** synchronous `POST /Msg`
     call and awaits its full response.
  3. For a **question/conversational/clarification**, that response
     already *is* the final answer (this is genuinely synchronous
     end-to-end — no async gap).
  4. For a **report/request**, `api/routes.py` (lines 388–410) writes the
     initial row (`begin_report`), immediately queues the real work
     (`ctx.queue.submit(WorkItem((event_id, _work), ...))`), and returns
     `202 {"taken_as": "report", "event_id": ..., "status": "queued"}`
     **without waiting for any of it to run** — confirmed by direct
     reading; the queued closure (`run_report_extraction`, the whole
     7-model-call pipeline measured in the first pass and again in C.1
     below) executes later, on the queue's own worker thread.
  5. `replace_status` (`bot/presentation.py`) is called exactly **once**
     per incoming message, with whatever text step 3/4 produced — for a
     report, that's just `"status.async_ack"`/`"bot.job_queued"`'s ACK
     text, edited into the original "thinking" message.
  6. The actual outcome, whenever the queue gets to it, is delivered
     **later**, as a **separate, new** message
     (`bot/background_services.py`: `send_reply`/`send_text`, never a
     further `edit_status` call, and never referencing the original status
     message's ID at all — that ID isn't even threaded into the
     notification-delivery code path).
  There is truly only one message and one edit, confirming the plan's own
  premise — but the *scope* of what that one edit covers is narrower than
  it might appear: for reports/requests, it covers only the sub-second ACK,
  not the outcome.
- **Verdict:** confirmed (the lifecycle is exactly "one status message, one
  edit," as documented) — with the important, previously-implicit
  clarification that for reports/requests, the edited text is the ACK, and
  the real outcome is an entirely separate message with no connection back
  to the status message's lifecycle.
- **Evidence:** Code reading of all files/lines named; corroborated by this
  pass's live reproduction, where every `POST /Event`/`POST /Msg` for a
  report returned in well under a second with `"status":"queued"`, while
  the actual processing (visible via `GET /Job` polling) took tens of
  seconds afterward.
- **Proposed fix:** N/A (mapping item — feeds D.2).
- **Suggested regression test:** N/A.
- **Priority:** N/A.

### D.2 — Propose staged status updates within the existing one-message lifecycle

- **File(s)/function(s) examined:** Same as D.1, plus `bot/transports.py`
  (`PTBTelegramClient.edit_status`/`HttpApiClient` — confirmed `edit_status`
  is a single, simple Telegram `editMessageText` call with no batching or
  multi-edit tracking today), `orchestrator/event_queue.py` (not read in
  full this pass; referenced only to confirm the queue is a background
  worker with no existing subscriber/callback mechanism visible from the
  `api/routes.py` call sites read).
- **Feasibility verdict, stated plainly per the plan's explicit ask:** **Not
  implementable as literally proposed ("edit the same status message 1–2
  times at checkpoints") without new cross-process plumbing.** The
  mechanical blocker, precisely: the bot process's only handle on the
  specific status message (`status_message_id`) exists for the duration of
  its single synchronous `POST /Msg` call — which, for a report/request,
  returns before even the *first* of the seven pipeline stages begins (the
  queue hasn't started work yet at the point `202` is returned). There is
  no existing channel for the API's background queue worker (running in a
  **different process** from the bot) to push "stage X just finished" back
  to the bot in real time, and even if there were, nothing today threads
  the original `status_message_id` through to where that push would need
  to originate (the notification-delivery path only knows `chat_id` and
  `reply_to_message_id` — never `status_message_id`).
- **What it would actually take** (described precisely, not implemented):
  1. The bot would need to **remember** `(event_id → chat_id,
     status_message_id)` locally after sending the ACK (a small in-memory
     or lightly-persisted map, scoped like `NotificationCursorStore`
     already is per-deployment).
  2. The API would need a new, lightweight way to expose "this event just
     entered stage X" — either (a) extend the existing notification
     mechanism with a new, non-terminal `kind` (e.g. `"stage_progress"`)
     the queue worker writes at 1–2 chosen checkpoints (matching
     `docs/vocabulary.md`'s existing notification-kind pattern), delivered
     through the same long-poll `GET /Notifications` the bot already
     consumes for everything else — the lowest-new-surface-area option — or
     (b) a separate, smaller polling endpoint scoped to in-flight event
     IDs only.
  3. The bot's notification-poll loop (`bot/background_services.py`) would
     need a new branch: on a `"stage_progress"` notification, look up the
     remembered `status_message_id` for that `event_id` and call
     `edit_status` again (not `send_text`) — reusing `replace_status`'s
     existing single-edit primitive, just called more than once per event
     instead of exactly once.
  This is a genuinely small, well-scoped addition (option 2a) — it reuses
  the existing notification/poll infrastructure rather than inventing a new
  stream, which is exactly what the plan's own ground rules ask for — but
  it is real new plumbing, not a one-line change to `format_job_result` or
  similar.
- **Which stages, per C.1's variance data:** `protocol_selection` (widest
  measured spread, and — per the first pass's D.2 — collapsible with
  `risk_assessment` if `operational_decision_mode="merged"` is ever
  activated, which would remove one of the two candidate checkpoints
  entirely) and `step_execution`/`insight_generation` (the two longest
  individual stages in the first pass's single full sample, ~11s and ~9s)
  are the best candidates *if* this is built — each comfortably exceeds the
  plan's own ">3–5s worth it" threshold. `extraction` and `risk_assessment`
  (2.4–3.4s consistently) are marginal at best and probably not worth a
  checkpoint edit on their own.
- **Telegram shape check:** Each checkpoint edit should stay one line, in
  the same register as the existing `"status.thinking"` text — e.g.
  `"בוחר פרוטוקול..."` / `"Choosing a protocol..."` — never restating
  progress already implied (no "step 2 of 7"-style counters, which would
  read as more machinery than a phone status line should show).
- **Suggested regression test:** Once built: submit a report that's
  artificially delayed past the >3–5s threshold for a chosen checkpoint
  (via a test double), assert exactly the expected number of `edit_status`
  calls happen (not more) and that the final edit still lands correctly
  even if a checkpoint edit was skipped/raced.
- **Priority:** medium (real gap, but correctly scoped as "needs new
  plumbing," not a quick win).

### D.3 — Check async ACK clarity for slow paths

- **File(s)/function(s) examined:** `messages/en.py`/`messages/he.py`
  (`status.async_ack`, `bot.job_queued`, `bot.taken_as`, `bot.waiting_approval`).
- **What the code actually does:** For a queued report/request, the user
  sees (via `bot/app.py`'s `_submit_and_format_message`, lines 181–189):
  `"Got it — taken as a {kind}."` followed by, on its own line,
  `"Job ID: {job_id}. You'll hear back here once it's done."` (Hebrew:
  `"מזהה משימה: {job_id}. התוצאה תישלח כאן בסיום."`) — or, for the
  simpler `"status.async_ack"` variant used elsewhere: `"Got it — your
  request is queued.\nTask ID: {task_id}"`.
- **Verdict:** not an issue — this already reads as an acknowledgment, not
  a final answer, and explicitly promises a follow-up message in both
  languages. This is one of the few places this pass found already meeting
  the plan's bar without changes.
- **Evidence:** Direct catalog text quoted above, both languages.
- **Proposed fix:** None required. Minor, optional tightening: the two
  slightly different ACK phrasings (`bot.job_queued` vs `status.async_ack`)
  could be unified to one wording used everywhere a job is queued, purely
  for consistency — a copy-editing nice-to-have, not a predictability gap.
- **Suggested regression test:** N/A (no defect to guard against).
- **Priority:** low.

---

## Part C — Response-time consistency and predictability

### C.1 — Reuse the first pass's D.2 latency trace; classify variance sources

- **File(s)/function(s) examined:** Live `DEEP_DEBUG` telemetry from 3
  freshly-submitted, identically-worded sensor events (this pass's own
  reproduction, port 8978), compared against the first pass's single
  sample.
- **What the live data actually shows:**

  | Stage | n | values (ms) | spread (ms) | mean (ms) |
  |---|---|---|---|---|
  | `extraction` | 3 | 3178, 2987, 2356 | 823 | 2,840 |
  | `risk_assessment` | 3 | 2967, 2649, 3094 | 445 | 2,903 |
  | `protocol_selection` | 3 | 5628, 6474, 3464 | **3,010** | 5,189 |
  | `task_formulation` | 1 | 3924 | — | — |
  | `step_execution` | 1 | 10025 | — | — |
  | `insight_generation` | 1 | 10881 | — | — |
  | `success_judgment` | 1 | 4061 | — | — |

  Only 1 of 3 runs reached `task_formulation` and beyond — the other two
  terminated early at `protocol_selection` with a **parse failure** (see
  the critical C.3 finding below); this is reported honestly as an n=1
  sample for those later stages, not padded or extrapolated from the first
  pass's single earlier sample.
- **Verdict:** confirmed cause (for the variance question specifically):
  `protocol_selection` has by far the widest measured spread (3.0s, nearly
  7× `risk_assessment`'s spread) among the three stages with real repeated
  data.
- **Evidence:** Live telemetry table above, from this pass's own
  reproduction (trace IDs available in the scratch DB, since deleted per
  this investigation's cleanup discipline — the raw `model_invocation_finished`
  records were captured and summarized here before deletion).
- **Proposed fix:** See D.2 above for the predictability angle
  (`protocol_selection` is the best checkpoint candidate precisely because
  it's both slow *and* the least predictable). Separately: activating
  `operational_decision_mode="merged"` (first pass's D.2 recommendation)
  would remove `protocol_selection` as a *separate* stage entirely
  (merging it with `risk_assessment`) — worth noting this could reduce
  variance by elimination, not just by speeding up the merged call, though
  that would need its own repeated-run measurement once merged mode is
  actually evaluated.
- **Suggested regression test:** A latency-distribution test (not a
  pass/fail correctness test) that runs N identical events and asserts the
  standard deviation of the slowest 1–2 stages stays under a documented
  threshold — useful as a live-traffic monitoring signal more than a CI
  gate, given real provider variance is expected.
- **Priority:** medium.

### C.2 — Check whether the user ever gets a "this is taking a while" signal

- **File(s)/function(s) examined:** `bot/presentation.py`,
  `bot/background_services.py`, `bot/app.py` — confirmed, per D.1, that
  there is exactly one status-message edit and no threshold-based logic
  anywhere in these files that inspects elapsed time and sends/edits
  anything mid-flight.
- **Verdict:** confirmed cause (of the plan's stated concern) — there is no
  such signal today. A report that takes 39 seconds and one that takes 90
  seconds (e.g. hitting `step_execution`'s retry path) look identical to
  the user: the ACK, then silence, then the result.
- **Evidence:** Absence confirmed by reading every file in the status/
  notification delivery path (`bot/presentation.py` in full,
  `bot/background_services.py`'s dispatch function, `bot/app.py`'s
  message-handling functions) — no timer, no threshold check, no
  "still working" text anywhere in the current source.
- **Proposed fix:** This is the same underlying gap as D.2 — the fix is the
  same new plumbing (a `"stage_progress"`-style notification), just with a
  time-threshold trigger instead of (or in addition to) fixed pipeline
  checkpoints: if a queued event hasn't reached a terminal outcome within,
  say, 20–30 seconds of being accepted, the queue worker (or a lightweight
  watchdog alongside it) emits one such notification, and the bot edits the
  status message once with a short "still working" line: `"עדיין
  בודק... זה לוקח קצת יותר מהרגיל"` / `"Still checking... this is taking a
  bit longer than usual."` — exactly the plan's own suggested wording,
  confirmed to fit the one-line register of the existing `status.thinking`
  text.
- **Telegram shape check:** One line, same tone/length as the existing
  "thinking" text — not an apology paragraph, not a technical explanation
  of why it's slow.
- **Suggested regression test:** A test double that holds a queued event's
  processing open past the threshold; assert exactly one "still working"
  edit fires (not zero, not repeated spam) before the final result.
- **Priority:** medium (same plumbing dependency as D.2 — natural to build
  together).

### C.3 — Check for predictable failure messaging ✅ COMPLETED

> **Status: both findings implemented.** Finding 1: `bot/app.py` now shows a
> generic `error.run_failure_generic` catalog message instead of the raw
> internal string — narrowed to the specific `error_class == "run_failure"`
> case (the one `ApiError` subclass this codebase always raises from raw
> internal text; every other subclass already used deliberately-crafted
> catalog text and was left unchanged) rather than every `ApiRequestError`.
> Finding 2: `_parse_selection_response`'s three patterns now tolerate
> leading reasoning prose before the `SELECTED:`/`REASON:` block (fix a),
> and `format_job_result`/`format_failure_notice` both cap `failure_reason`
> at 240 characters (fix b). See `docs/progress.md` (2026-09-04,
> "CRITICAL_FIXES_PLAN item 3: protocol-selection parser tolerates leading
> reasoning, failure text is capped" and "...item 4: raw internal values no
> longer leak into Telegram messages").

- **File(s)/function(s) examined:** `bot/app.py` (lines 170–176,
  272–273 — `except ApiRequestError: reply = messages.text("error.request_failed",
  reason=exc.message)`), `messages/en.py`/`messages/he.py`
  (`error.request_failed`), `bot/interactions.py` (`format_job_result`,
  lines 98–113), `orchestrator/reasoning.py` (`_parse_selection_response`,
  `_SELECTED_PATTERN.fullmatch` — the legacy protocol-selection parser).
- **What the code actually does — two independent, both confirmed live
  this pass, failure surfaces:**
  1. **Synchronous 4xx from the API** (the first pass's C.1 bonus
     finding — report/request intent classification hard-failing):
     `bot/app.py` catches `ApiRequestError` and renders
     `"error.request_failed"`: English `"Request failed: {reason}"`,
     Hebrew `"הבקשה נכשלה: {reason}"` — in **both** languages, `{reason}`
     is `exc.message`, which is the server's raw internal validation
     string verbatim (e.g. `"primary intent 'report' requires exact
     evidence"`). In the Hebrew case this produces a sentence that's
     mostly Hebrew with a raw **English** technical fragment spliced in —
     arguably a worse outcome for a Hebrew-reading user than the English
     case is for an English-reading one.
  2. **Newly found this pass, live-reproduced in 2 of 3 identical runs**:
     an *async* `"failed"` outcome whose `outcome_failure_reason` is set to
     `f"could not parse protocol selection response: '{raw_model_text}'"`
     — where `raw_model_text` was, in the captured live examples, a
     **~700-word** chain-of-thought block (the model reasoning through each
     candidate protocol in bullet points before its final `SELECTED:`/
     `REASON:` lines — natural, common LLM behavior that
     `_SELECTED_PATTERN.fullmatch` rejects outright because it requires the
     *entire* response to be exactly two lines). `format_job_result` (line
     103–104: `if result.failure_reason: lines += ["", result.failure_reason]`)
     inserts this **entire, unformatted, multi-paragraph string** into the
     Telegram message with no truncation, no summarization, and no
     distinction from a one-sentence failure reason — this is a `failed`
     job's *normal* delivery path (`job_finished` → `format_job_result`),
     not an edge case requiring special reproduction conditions; it fired
     on 2 of 3 completely ordinary identical submissions in this pass.
- **Verdict:** confirmed cause — critical for both.
- **Evidence:** Finding 1: `bot/app.py` lines quoted, cross-referenced with
  the first pass's C.1 entry. Finding 2: live-captured `outcome_failure_reason`
  strings from this pass's reproduction (event IDs `47ea2d99...` and
  `4fe3d9bf...`), each beginning `"could not parse protocol selection
  response: '..."` followed by the model's full reasoning — quoted in
  representative excerpt in this document's investigation notes; the
  mechanism (`_SELECTED_PATTERN.fullmatch` requiring the whole response to
  match, with no tolerance for leading reasoning text) is confirmed by
  reading `orchestrator/reasoning.py`'s `_parse_selection_response` and
  `_SELECTED_PATTERN`'s definition (a `re.fullmatch` against a strict
  two-line `SELECTED: ...\nREASON: ...` pattern).
- **Proposed fix:** For finding 1: give `RunFailureError`/`ApiRequestError`
  a **user-safe** message separate from the internal one — e.g. the API
  raises with both an internal `message` (for logs/traces, kept exactly as
  detailed as today) and a short catalog key the bot renders instead of the
  raw string, defaulting to something like `"api.report_failed_generic"`:
  `"Couldn't process that — try rephrasing, or contact a commander."` /
  `"לא הצלחתי לעבד את זה — נסה לנסח מחדש או פנה למפקד."` (matching the
  plan's own suggested wording near-verbatim). For finding 2, two
  complementary changes: (a) fix `_parse_selection_response`'s brittleness
  itself — allow leading reasoning text before the `SELECTED:`/`REASON:`
  block (e.g. `re.search` for the pattern anchored at day's end of the
  string, or explicit prompt instructions to respond with *only* the
  two-line format, matching how other legacy parsers in this codebase
  already handle this per the first pass's C.1 bonus finding's own proposed
  fix) — this is a correctness fix, not just a UX one, and should be
  cross-referenced with that finding rather than duplicated as new work;
  (b) regardless of (a), `format_job_result` should **cap** how much of
  `result.failure_reason` it ever inserts into a Telegram message — e.g.
  first line/sentence only, with the rest available only via
  `DEEP_DEBUG`/logs, never dumped in full into a phone notification.
- **Telegram shape check:** Finding 1's fix: identical short-message shape
  to the plan's own suggested wording — one line. Finding 2's fix (b): caps
  an unbounded wall of text down to a single sentence — a large, concrete
  ergonomics improvement on its own, independent of whether the underlying
  parser (fix (a)) also gets hardened.
- **Suggested regression test:** For finding 1: trigger the intent-
  classification 422 (per the first pass's own C.1 regression test) and
  assert the bot-rendered reply does **not** contain the substring `"primary
  intent"` or any raw internal validation vocabulary. For finding 2: feed
  `_parse_selection_response` (or the full pipeline, per this pass's live
  reproduction) a response with reasoning prose before a valid `SELECTED:`/
  `REASON:` block and assert it parses successfully rather than failing;
  separately, assert `format_job_result`'s output length for a long
  `failure_reason` stays under a fixed budget (e.g. one paragraph) even
  when given a multi-hundred-word input.
- **Priority:** critical.

---

## Part E — Cross-role consistency

### E.1 — Compare commander vs. viewer answer *phrasing*, not just scope

- **File(s)/function(s) examined:** `history/query.py` (`query_spec`,
  lines 397–465 — the same `_history_agent_prompt(context_instruction,
  question, views)` call regardless of `sender_identity_filter`;
  `answer_most_recent_event`, lines 561–567 — same, one shared
  `_history_agent_prompt` call).
- **What the code actually does:** Both a commander's and a viewer's
  question run through identical prompt-construction and identical
  History Agent invocation code. The **only** difference anywhere in this
  path is which rows `sender_identity_filter` allows into `views` before
  either caller's prompt is built — the instruction text, tone, and
  structure requested of the model are byte-for-byte the same regardless
  of caller role.
- **Verdict:** not an issue — matches README's stated design exactly; no
  divergent phrasing-logic risk found.
- **Evidence:** Code reading of the two call sites named — both take an
  identical `context_instruction` string and pass it to the same
  `_history_agent_prompt` helper with no role-conditional branching
  anywhere in between.
- **Proposed fix:** N/A.
- **Suggested regression test:** N/A (confirms existing behavior; a
  snapshot test comparing prompt text generated for a commander vs. a
  viewer, with data held constant, would make this guarantee explicit and
  catch a future regression, if considered worth adding).
- **Priority:** low.

---

## Confirmation: no files outside this document (and the one permitted move) were changed

- **The one permitted filesystem change**: `docs/IMPROVE_ALL_SYSTEM.MD` was
  moved (not edited — verified identical byte size, `49512` bytes, before
  and after) to `docs/IMPROVES/DIAGNOSTIC_FINDINGS.MD`, via a plain
  filesystem move (no git operation). A repository-wide search for any
  reference to the old path (`grep -rn "IMPROVE_ALL_SYSTEM"`) found
  **none**, so no other file needed updating for the rename.
- `git status --porcelain` at the end of this pass:
  ```
  ?? docs/IMPROVES/
  ```
  — only the new folder (containing the moved file and this new document),
  as untracked additions. No existing tracked file was modified.
- Both live reproductions this pass ran against isolated scratch
  profiles/databases created outside the repository (`%TEMP%\agentshub_repro_profile\`,
  `%TEMP%\agentshub_repro_profile2\`, plus temporary profile modules under
  `%TEMP%\repro_profile\`), on ports 8977/8978 — never against
  `data/demo_profile.db` or port 8902. Both reproduction servers were
  stopped and all scratch files deleted after use.
- No source file in this repository was created, edited, or deleted during
  this pass other than this document and the one permitted move above.
