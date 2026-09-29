# Pending live-model verification

**Why this file exists:** the OpenRouter key backing the isolated response_team stack ran out
of credits mid-session (every model call returned `402 Payment Required`; zero events were
created in the last attempted run). Everything below is implemented, unit-tested, and the full
suite is green, but has **not** been confirmed against a real model call. When credits are
restored, run this file top to bottom on a **fresh isolated DB** (never production) and update
it with pass/fail per item.

Isolated stack setup (repeat exactly, per the established pattern this session used):
1. Kill any listener on the isolated ports, wipe `data/response_team/response_team_history.db*`.
2. Sync the current main-repo working tree into the isolated repo copy.
3. Start `python -m api.app profiles.response_team` (`API_PORT=18907`) and
   `python -m bot.simulator_app profiles.response_team` (`SIMULATOR_PORT=18915`,
   `API_PORT=18907`), both with `DEEP_DEBUG=false` unless a specific item below says otherwise.
4. Register `bot-service` as commander directly against the fresh DB
   (`SQLitePersistence(...).write_user("bot-service", "commander")`) — `ensure_simulation_entities`
   does not provision it.
5. Send messages **one at a time**, waiting for each to reach a terminal outcome or hold before
   sending the next. Do not run more than one driver/monitor at once (this session hit the
   memory-pressure reaper repeatedly when several background tasks accumulated).

Sender identities below are resolved numeric persona IDs (`SimulationPersona.offset` + base
`9000000000000000`), not the raw persona key strings — confirmed against
`site_security_officer` (offset 4) == the commander identity `9000000000000004` used
throughout this session's drivers.

---

## 0. Prerequisite check: does bot.simulator_app dispatch the new notification kind at all?

This session's isolated verification always used `bot.simulator_app`, never the real `bot.app`.
Before trusting any commander-alert result below, confirm the simulator actually runs the same
`bot/background_services.py::dispatch_notification` loop that a real bot process does, and that
it reaches the new `"resource_unavailable_alert"` branch (added today) and calls
`interactions.notify_resource_unavailable_alert`, which in turn calls
`deps.api_client.list_commander_chat_ids()` and `deps.telegram_client.send_text(...)` once per
registered commander.

**How to check:** after case 2 below (the first case that should produce a commander alert),
inspect the simulator's own stdout/stderr log for a `telegram_client.send_text` call (or
equivalent simulated-send log line) targeting the commander's chat, separate from the
reporter's own reply. If the simulator's Telegram client is a stub that only logs sends rather
than truly holding them for `/Simulator-msg/poll`, reading the DB directly
(`SELECT commander_alert_text FROM events WHERE event_id = ?`) is the fallback way to confirm
the *content* is correct even if the delivery-path check itself needs a real `bot.app` run
later.

**Pass:** the alert text appears somewhere in the simulator's own log or poll surface, addressed
to the commander's identity, not the reporter's chat.
**Fail:** no evidence the simulator ever attempts this delivery — note this and treat every
"commander saw" result below as "content correct, delivery path unconfirmed" until a real
`bot.app` run checks it.

---

## 1. Resource-unavailable mechanism — the 6 required cases

For **every** case below, check all of:
- `event.outcome` — must be `"handled_resource_unavailable"`, never `"failed"`.
- `event.report_text` (what the reporter sees) — must contain the fact, in fluent Hebrew, with
  the resource kind and area translated (e.g. "רחפן", "השער המזרחי") — never the raw English
  identifier (`"drone"`, `"east_gate"`) and never the words "Commander alert" or "חלופות".
- `event.commander_alert_text` (what every commander sees, in their own private chat only) —
  must contain the fact AND concrete alternatives (cameras covering the area / ready drone
  count / available roster members / remaining force capacity), in Hebrew.
- The `job_finished` notification's `target_chat_ids` — must be exactly `[reporter's own chat]`,
  never widened to include any commander.
- A separate `resource_unavailable_alert` notification must exist for the same event, with
  `target_chat_ids == []` (delivered by the bot via `list_commander_chat_ids()`, not via
  `target_chat_ids`).

### Case 1 — #8 (drone dispatch succeeds, 1st of the 2-drone fleet)
- Sender: `9000000000000002`, chat: `external_forces` (`telegram_chat_id="external_forces"`,
  `telegram_chat_type="supergroup"`).
- Text: `עדכון גזרתי: הלילה נגנב טרקטורון מאצלינו. סבירות גבוהה שהגנבים נעו לאורך ציר המערכת.`
- **Pass:** `outcome == "succeeded"`, `selected_protocol == "report_security_incident"`, a real
  drone mission recorded (`drone_missions` table gains one row). This case is the control —
  it must NOT be resource-unavailable, since the fleet still has 2 ready drones at this point.

### Case 2 — #11 (drone dispatch succeeds, 2nd of 2 — fleet now exhausted)
- Sender: `9000000000000006`, chat: `external_forces`.
- Text: `לכל המרחב: התקבל דיווח שטרם אומת על רכב מסחרי לבן ללא לוחיות זיהוי שנראה נע באיטיות באזור המטעים המזרחיים שלכם.`
- **Pass:** same as case 1 — `outcome == "succeeded"`, a second real drone mission recorded.
  After this case, `list_drones(status="ready")` must be empty.

### Case 3 — #19 (drone unavailable — the actual resource-unavailable trigger)
- Sender: `9000000000000010`, chat: `response_team` (`telegram_chat_id="response_team"`,
  `telegram_chat_type="supergroup"`).
- Text: `סירנות בשער מזרח! ראיתי דמות חשודה בתוך החצר של משפחת לוי ברחוב הזית 12! יש לו משהו ארוך ביד!`
- **Pass:** all bullet points in the section header above. This is the case that, before
  today's fix, incorrectly showed `outcome == "failed"` with a model-composed generic "not
  known what prevented completion" text — confirm that regression is actually gone.

### Case 4 — #20 (drone unavailable, second occurrence)
- Sender: `9000000000000011`, chat: `response_team`.
- Text: `רגע! תושבים מדווחים עכשיו על ירי בלתי פוסק באזור השער המערבי! אני רץ לשם!`
- **Pass:** same bullet points as case 3.

### Case 5 — force pool exhausted (custom, not from the sec001 corpus)
Send in order, waiting for each to finish:
- 5a: sender `9000000000000000`, chat `response_team`, text
  `יש צורך בשני שוטרים בשער המזרחי, דחוף.` — expect a real dispatch of 2 police units
  (`outcome == "succeeded"`, `selected_protocol == "dispatch_neighboring_force"`). If the model
  requests only 1 unit instead of 2, adjust 5b's own wording to still reach the pool limit (2)
  before 5c, or send a third message.
- 5b: sender `9000000000000003`, chat `response_team`, text
  `יש צורך בעוד שוטר אחד בשער המזרחי, דחוף.` — expect this one to hit the pool limit.
- **Pass on 5b (or whichever message first exhausts the pool):** `outcome ==
  "handled_resource_unavailable"`; `report_text` names police in Hebrew ("משטרה") and the area,
  never `"police"`/`"east_gate"`; `commander_alert_text` lists remaining capacity per force kind
  (ambulance/k9/yasam should still show `2/2`) and available roster members.
- **If the model dispatches fewer units than expected and the pool never exhausts:** this is a
  finding about the model's own request-sizing, not a bug in the capacity mechanism — note the
  actual unit counts observed and retry with more explicit phrasing (e.g. state the exact
  number twice) before concluding the mechanism itself is broken.

### Case 6 — squad member unavailable (custom, not from the sec001 corpus)
- Sender: `9000000000000009`, chat `response_team`, text
  `שלחו את אנשי הצוות שלנו (לא כוח חוץ) לבדוק את השטח ליד המטעים המזרחיים.`
- **Pass:** `outcome == "handled_resource_unavailable"`; `report_text` says "חבר צוות" (squad
  member), never `"squad_member"`; `commander_alert_text` lists available roster members (none,
  since this is a fresh DB with no attendance responses recorded) and remaining drone/camera/
  force alternatives.
- **Known risk:** "squad" is a new concept this session invented for `dispatch_neighboring_force`
  — the model may not naturally pick `kind="squad"` from phrasing alone (it has never seen this
  concept in the corpus it was implicitly trained on for this profile). If the model instead
  asks a clarifying question, declines, or picks a different force kind, that is itself the
  finding to report — do not just reword until it "works"; a model that can't discover "squad"
  from natural language is a real UX gap worth knowing about, separate from the mechanism's own
  correctness (already proven by direct unit/agent-level tests).

---

## 2. Situational-picture / lookup must never trigger a camera alert (fix c)

Confirms the executor's clear-before-call fix actually prevents a stray signal from a read-only
lookup leaking into a later, unrelated protocol step.

1. Ask a plain question (not a report) as any registered viewer, e.g. sender
   `9000000000000001`, chat `response_team`: `מה מצב המצלמות באזור המטעים המזרחיים?`
   (east_orchards has no camera in `CAMERAS`/`profiles/response_team.py` — CAM-01/02 are in
   east_fence, CAM-03 is in south_corner).
   - **Pass:** a normal answer stating no camera covers that area. No `resource_unavailable_alert`
     notification appears from this message (check `/Notifications` immediately after).
2. Immediately after (same server process, same `surveillance_agent` instance still live),
   send case 1 or case 2 from section 1 above (a real drone-dispatch report, while the fleet
   still has a ready drone).
   - **Pass:** outcome is `"succeeded"` with a real drone dispatch — never
     `"handled_resource_unavailable"`. If it incorrectly shows resource-unavailable here, the
     clear-before-call fix in `protocols/executor.py` has regressed and a stale camera signal
     leaked across requests.

Note: no protocol declared today (`report_security_incident`, `query_situational_picture`, etc.)
actually lists `get_camera_feeds` in its own `approved_tools`, so there is currently no live
path where the camera signal fires *inside* a real protocol step at all — step 2 here is
testing leak-prevention, not a real camera-triggered alert. If a future protocol adds
`get_camera_feeds` to its `approved_tools`, add a case here that exercises it directly.

---

## 3. Reporter-composer fluency (fix b)

For case 3 or 4 in section 1, read the actual `report_text` the model composed (not just that
it contains the right substrings) and confirm by eye:
- It reads as one natural, professional Hebrew sentence or two — not a template dump, not code-
  switched into English mid-sentence.
- It does not open with a banned phrase (`orchestrator.report_tone.banned_openers`).
- It states plainly what happened (report handled, drone situation) without narrating actions
  the specialist never actually took (a known model tendency observed earlier this session,
  e.g. narrating "cameras were redirected" when `get_camera_feeds`/camera tools were never in
  that step's own `allowed_tools`).

**Pass:** reads naturally; no fabricated actions.
**Fail:** note the exact fabrication or tone problem — this is about model behavior, not the
resource-unavailable mechanism itself, but worth tracking since it affects trust in the report.

---

## 4. Merged-mode / ambiguity-auto-resolve stability — larger sample

Already verified once this session (3 rounds × 7 messages, fresh isolated DB per round, from
before the credits ran out): protocol selection was stable for 6/7 messages; message #24
diverged once (`dispatch_neighboring_force` twice, then `report_security_incident` once) under
`operational_decision_mode="merged"`. This was a single-run sample — not enough to know the real
divergence rate.

**To do when credits return:** run #24 alone, 10 times, fresh isolated DB each time (or reuse
one DB and just replay the message with a fresh sender each round to avoid cross-round state).
Record the selected protocol each time.
**Pass:** if divergence stays rare (≤1-2 in 10) and never produces an unsafe outcome (e.g. never
silently drops a genuine casualty report), the current behavior is acceptable to keep.
**Fail:** if divergence is frequent, or ever produces a worse outcome (e.g. picks a lower-
criticality protocol for a high-risk message), reconsider whether `operational_decision_mode`
should revert to `"separate"` for this specific ambiguous-message shape, or whether the prompt
needs tightening.

---

## 5. Anything else this session left unverified live

- The exact call-count/latency deltas reported for the merged-mode "before vs after" table
  (six messages, single run each) — a single sample, not statistically meaningful. Re-running
  with more samples would sharpen the real savings estimate from the speed-investigation task,
  but is not blocking.
- `fire_station`'s dispatch tools (`dispatch_station_crew`, `request_mutual_aid`,
  `request_hazmat_assessment`) remain completely unwired to the resource-unavailable mechanism
  (by design — this session's fixes were scoped to `response_team` only, per explicit
  instruction). Not a pending verification, just a reminder of scope for next time this area is
  touched.

---

## 6. Keyword-classification removal (2026-09-29) — live-verification items

A live CAM-01 report caught a hardcoded keyword classifier (`_is_approval_policy_question`,
`api/routes.py`) misclassifying a report as a policy question via an accidental substring match
(`"אישור"` inside `"לאישור"`, `"מי"` inside `"יזומית"`) and answering it with a canned string
instead of ever reaching the model. Following that, every keyword/word-matching check that
decided message classification or reply content (not just that one) was removed from the code —
see the session's own summary for the full list (`_is_situational_picture_query`,
`_is_team_roster_query`, `_team_roster_view`, `_is_approval_policy_question`,
`_is_pending_report_cancellation` in `api/routes.py`; the free-text approve/reject word lists in
`bot/app.py`; `_infer_camera_status` in `profiles/response_team.py`). Full suite green
(1667 passed) against fakes/scripts, but several of these now depend on the model doing
something a fake crewai echo cannot exercise. Each needs a real model call to confirm the
replacement actually behaves as intended.

### 6.1 Free-text "situational picture" question (no button/hint)

- Message: `מה תמונת המצב כרגע?` (or similar), from a registered viewer/commander, no
  `protocol_hint` and no button press.
- **What changed:** this used to deterministically route to `build_situational_picture`
  (the multi-domain, concurrent-specialist-fanout builder). It no longer does — it's now just a
  normal message for the model's own intent/question routing (`classify_intent` /
  `plan_message` → `answer_question` / `answer_question_from_plan`).
- **Look for:** does the model still produce a useful, multi-domain-ish answer (roster +
  surveillance + forces), or does it only answer one narrow slice? Does it ever fail to
  classify this as a question at all?
- **Pass:** a genuinely useful answer covering more than one domain when the message is broad,
  OR a single-domain answer that plainly says it's scoped to what was asked (no crash, no
  refusal).
- **Fail:** a crash/`RunFailureError`, a wildly incomplete answer, or misclassification as
  something other than a question.
- **Note:** the explicit-hint/button path (`📊 תמונת מצב כללית`) is unaffected and still reaches
  `build_situational_picture` directly — no need to re-verify that path.

### 6.2 Free-text team-roster question (no button/hint)

- Message: `מי זמין הערב?` / `מי לא זמין?` / `מה הסטטוס של הכיתה?`
- **What changed:** the old dispatch (`_team_roster_view` + a direct call to
  `ag.report_team_availability(view=...)`) was actually **broken** — `report_team_availability`
  only ever accepted `as_of_iso`, never `view`, so this branch would have raised `TypeError` if
  it had ever fired live. It's now removed; these questions fall through to the generic
  `ag.process(text, allowed_tools)` path, letting the model call `report_team_availability`
  itself.
- **Look for:** does the model correctly call `report_team_availability` and produce a sensible
  answer (who's available/unavailable/awaiting), given the question's actual phrasing?
- **Pass:** a correct, on-topic roster answer, no crash.
- **Fail:** a crash, an empty/generic answer, or the model picking an unrelated tool.

### 6.3 Approval-policy question ("does this need approval?")

- Message: `מי צריך לאשר בקשות כאלה?` / `האם זה דורש אישור?` — a genuine question about the
  approval mechanism, not an operational report.
- **What changed:** this used to return a fixed canned string (`api/routes.py`'s old
  `_is_approval_policy_question` branch) without any model call. It now falls through to
  `classify_intent`'s own `conversational` branch, and the model must describe the mechanism
  itself from `system_context`.
- **Look for:** accuracy — does the model correctly say only a commander can approve/reject via
  the buttons, and that missing operational details get asked for first? Does it hallucinate
  anything not in `system_context`?
- **Pass:** an accurate, natural-language description, no hallucinated capability.
- **Fail:** an inaccurate or hallucinated description of the approval mechanism.

### 6.4 A report that superficially resembles an approval question (the original bug)

- Re-send the exact CAM-01 message that triggered this investigation:
  `CAM-01 (גדר מזרחית, מקטע 4) הורדה יזומית לשעתיים לצורך עדכון גרסה תקופתי. עד לאישור חזרה היא
  אינה מספקת תמונה חיה.`
- **Pass:** this is now classified and processed as an actual `camera_status` report (reaches
  `update_camera_status` for CAM-01), not answered as a policy question. `event["outcome"]` is a
  real terminal outcome, not a canned conversational reply.
- **Fail:** it's still misclassified as conversational/policy — would mean the model's own
  classification has the same blind spot the keyword heuristic did, which would be a much more
  fundamental finding.

### 6.5 Pending-report cancellation phrase ("never mind, forget it")

- Set up a pending `event_data` hold (send a report missing a required field so the system asks
  a follow-up question), then reply with something like `עזוב, תשכח מזה, לא חשוב` instead of
  answering the question.
- **What changed:** the old deterministic cancellation (`_is_pending_report_cancellation`) gave a
  polite "cancelled, no action taken" confirmation and explicitly declined the event. That's
  gone. The reply now flows into the existing model-driven `apply_event_data_reply` /
  `extract_event_data_update` path like any other reply attempt.
- **Look for:** what actually happens. Two known possibilities, both acceptable in principle but
  need to be seen: (a) the model recognizes this doesn't address the pending question
  (`addresses_request=False`), the hold is left dangling (not explicitly declined — a known,
  pre-existing gap for any off-topic reply, not new), and the message reprocesses as a fresh
  one; (b) the model tries to force-fit "never mind" into the missing field and produces a bad
  extraction.
- **Pass:** (a) happens, and reprocessing the "cancel" text as a fresh message doesn't do
  anything harmful (e.g. doesn't get misclassified into a new spurious report).
- **Fail:** (b) happens, or the abandoned hold causes visible confusion (e.g. the reporter is
  asked the same follow-up question again on their next unrelated message).

### 6.6 Commander typing "אישור"/"כן" instead of pressing the approval button

- With exactly one open approval hold, have the commander type `אישור` or `כן` as a plain
  message instead of tapping the inline button.
- **What changed:** the old free-text shortcut (`bot/app.py`'s word lists) resolved the hold
  immediately. It's gone; this is now just an ordinary message reaching `classify_intent`.
- **Look for:** what the commander is told. There is no code path today that maps this phrase
  back to "approve the pending hold" — expect it to be classified as `conversational` or
  `needs_clarification` and answered generically, NOT as an approval.
- **Pass:** the commander gets a sensible reply (even if it's just a generic acknowledgment or
  clarification) and is not left thinking the hold was approved when it wasn't — worth
  double-checking the reply doesn't accidentally imply approval happened.
- **Fail:** the reply implies the hold was approved/rejected when it wasn't, or the hold is
  silently left open with no indication to the commander that they need to use the button.
- **Note:** this is an intentional UX regression accepted as part of removing the keyword
  shortcut — if it reads badly live, consider whether `system_context` should tell the model
  about open holds so it can proactively point the commander at the buttons.

### 6.7 Camera-status report producing the correct status per camera

- Re-run cases 2 (`CAM-01 shows intermittent reception interference`) and 4/5-style scenarios
  (`CAM-01 and CAM-02 are offline`, a cut-cable report) from `tests/test_operational_scenarios.py`
  live, plus a "back online" recovery report.
- **What changed:** `_infer_camera_status`'s keyword heuristic (recovery words / offline words →
  active / offline / degraded) is gone. Each camera's resulting status is now a real per-camera
  model decision, made when the specialist agent calls `update_camera_status(camera_id,
  observation, status)` itself. This can no longer be verified by the offline scripted-agent
  suite at all (the fake crewai stand-in never invokes real tools) — it is purely a live
  concern now.
- **Pass:** for each camera named in a multi-camera report, `update_camera_status` is called
  with a status that matches the report's actual content (offline for "cut"/"sabotage",
  active for "back online", degraded for ambiguous/intermittent language) — check via
  `surveillance_agent.surveillance_store.list_cameras()` on the live/isolated DB.
- **Fail:** a wrong status, a missed camera (only one of several updated), or the model failing
  to call the tool at all for one of the named cameras.
