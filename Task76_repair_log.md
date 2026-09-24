# Task 76 — Repair and validation log

Date: 2026-09-24  
Base: `ba7eeeabb41b1d7caf415d677600af8c5e7b32d6` on `fix/agent-orchestration-and-runtime-stability`

The Task75D acceptance log and screenshots remain preserved as the source evidence. No simulator step was auto-advanced during Task76.

## Code changes

- Attendance reports without a resolvable bounded interval now remain durable and enter `waiting_for_event_data` for `availability_start` and `availability_end`; they are not reported as rejected domain writes.
- Trusted team-owned operational extraction now preserves grounded facts such as heavy equipment at the west gate and an in-transit report. Mixed report/question text remains a report, and the final detail explicitly states when the question has no authoritative answer in the message.
- Surveillance extraction recognizes a physically cut communication cable as an offline observation.
- A non-scalar provider value for optional classification severity is normalized to unknown (`null`) so the report can continue to the required-field/clarification path rather than being discarded by schema parsing.

## Validation evidence

- Focused regression: `58 passed` (`test_task53_group_owned_report_contract.py`, `test_task58_grounded_team_and_forces_extraction.py`, `test_surveillance_report_extraction.py`, `test_operational_intake_schema.py`, `test_expiry_finalization.py`, `test_api_unified_ingestion.py`).
- Broader offline run: `1933 passed`; 74 setup errors require missing `TEST_CORE_MODEL_*` environment variables, and `test_file_catalog` fails because the pre-existing Task75D acceptance screenshots/log are intentionally untracked evidence files. No new product assertion failure was reported by that run.
- Brave/Playwright browser verification: BLOCKED. The Brave extension inventory was visible, but the browser connector returned `Debugger unattached` and then `Unable to load browser request-header policy`. No workaround or automatic Simulator action was used.

## ACC dispositions

| Finding | Disposition | Evidence / remaining work |
|---|---|---|
| ACC-001 | deferred | Terminology is outside this repair patch; Task75D evidence remains the reference. |
| ACC-002 | deferred | Specialist routing terminology remains a separate domain-language issue. |
| ACC-003 | not reproduced in code | Task75D DB evidence showed no LIVE membership for `2077472944`; no membership was added. |
| ACC-004 | fixed in code; runtime pending | Trusted team operational extraction now retains heavy-equipment/west-gate facts and does not route the message solely to history. |
| ACC-005 | still failing / runtime pending | The original camera-03/04 state loss was observed after `deadline_expired`; multi-camera authoritative projection still needs fresh browser confirmation. |
| ACC-006 | fixed in code; runtime pending | Cut communication cable is now recognized as an offline surveillance observation before domain persistence. |
| ACC-007 | deferred | Cross-agent correlation, approval routing, and multi-part planning remain a limitation for the subsequent Operational Intelligence task. |
| ACC-008 | fixed in code; runtime pending | Team operational facts are extracted into declared fields; a mixed question receives an explicit unknown/no-authoritative-co-traveller answer rather than being silently dropped. |
| ACC-009 | not reproduced in code | Overall-picture failure remains the intentional LIVE-membership/context finding from Task75D. |
| ACC-010 | fixed in code; runtime pending | Missing attendance bounds now create an event-data hold instead of a rejected attendance write. |
| ACC-011 | fixed in code; runtime pending | Invalid provider severity is preserved as unknown and can reach clarification/required-field handling. Action ambiguity findings remain outside this patch. |
| ACC-012 | fixed in code; runtime pending | Same severity normalization applies to FIRE3. |
| ACC-013 | deferred | Missing action locations/quantities still require the dedicated planning repair; no information was fabricated. |

## Checkpoint

No local commit or push was made because the required fresh browser/Telegram verification was blocked by the Brave connector. Working tree source changes are ready for re-validation when the connector is available.

## Task 76A browser recovery checkpoint

- HTTP smoke: Dashboard `/admin/units`, Simulator `/admin/simulator`, and Telegram Web all returned HTTP 200.
- Official browser connector: a fresh Brave tab could be created, but it landed on `/admin/login`; existing Telegram and prior dashboard tabs returned `Debugger unattached` when attachment was attempted.
- Local Playwright fallback: the Node import did not expose a usable Playwright runtime and Python Playwright is not installed. No browser security policy was bypassed and no Telegram session data was accessed.
- Stop condition reached: no authorized Dashboard/Simulator session and no authorized Telegram attachment, so no simulator step was sent and no real-E2E PASS was claimed.

## Task 76B real-browser regression

The user completed the Admin login manually in a newly controllable Brave tab. The same authorized tab was refreshed and used to operate the Simulator. A separate fresh Telegram Web tab was controllable and showed the real `Main Agent` chat. No Telegram credentials were accessed and no browser security policy was bypassed. The running stack was restarted once to load the already-uncommitted Task 76 code; no database reset was performed.

No source code was changed during this regression. No pytest, commit, or push was performed. Screenshot evidence remains outside any commit.

### Fresh run evidence

| Scenario / run | Steps exercised | Result |
|---|---|---|
| SEC_001_PHASE_1 / `23400e1cdf9746349bc3495cf3917ca5` | 1-6 | Step 6 created an event-data hold for `availability_start` and `availability_end`, but later finalized as `required_event_data_expired`; no bounded interval was stored. |
| SEC_001_PHASE_2 / `25f948a99c5b4817bd4d17df4ddc621c` | 1-8 | Step 3 persisted successfully; step 5 returned internal error without an event; step 7 returned roster only; step 8 persisted successfully but its question was unanswered. Step 1 ultimately finalized `deadline_expired`. |
| SEC_001_PHASE_3 / `d36774e1efda4fa8ba2a015b961c6c5d` | 1 | `team_operational_report`, `succeeded`; no severity extraction exception reproduced in this focused step. |
| FIRE_002_PHASE_3 / `977a1e7a61bc4bad83f6b269bd4915b0` | 1 | `team_operational_report`, `succeeded`; no severity extraction exception reproduced in this focused step. |

All runs above were bound through the Simulator to Telegram identity `2077472944` (בר משה) using the exact-run binding control. No membership was added to the standby class or fire brigade during this work.

### Updated ACC evidence

| Finding | Disposition after real regression | Evidence |
|---|---|---|
| ACC-004 heavy equipment / west gate | fixed for persistence, response incomplete | SEC2 run `25f948a99c5b4817bd4d17df4ddc621c`, step 3 is a canonical `team_operational_report` with `outcome=succeeded`; step 4 situational picture includes the west-gate report. The question about whether work was planned still has no observed answer. |
| ACC-005 camera 03/04 | still failing | Same SEC2 run, step 1 ended `outcome=failed`, `outcome_failure_reason=deadline_expired`. Step 4 visibly reported `6/6` active. Canonical event exists but no successful camera projection was observed. |
| ACC-006 cut cable | still failing | Same SEC2 run, step 5 showed `הבקשה נכשלה: אירעה שגיאה פנימית`; the authoritative `events` query contained no step-5 row, proving failure before durable event persistence. |
| ACC-007 commander multi-task request | still failing / deferred | Same SEC2 run, step 7 returned only the standby-team roster. No evidence of complete cross-agent correlation, escalation approval, or deployment recommendation was observed. |
| ACC-008 Gil mixed report/question | partially fixed, response incomplete | Same SEC2 run, step 8 persisted as `team_operational_report`, `outcome=succeeded`; the question “מי עוד איתי בצוות?” received no final answer in the Simulator. |
| ACC-010 Michael bounded interval | still failing terminally | SEC1 run `23400e1cdf9746349bc3495cf3917ca5`: `held_events` was created with both interval fields missing, but the event later became `required_event_data_expired` with null interval fields. |
| ACC-011/012 severity | not reproduced in focused browser steps | SEC3 run `d36774e1efda4fa8ba2a015b961c6c5d` step 1 and FIRE3 run `977a1e7a61bc4bad83f6b269bd4915b0` step 1 both reached canonical `succeeded` events. This is limited evidence, not a global PASS for all severity paths. |

FIRE3 continuation evidence: run `977a1e7a61bc4bad83f6b269bd4915b0` steps 1-3 all reached `succeeded`. Step 4 was classified as `human_activation`, but after the browser wait the canonical event still had `outcome=null` and no failure reason; the Simulator action exceeded the browser-control timeout. This is an unresolved terminal-result/commander-action finding, not a PASS.

### ACK and Telegram observations

The Simulator displayed the standard ACK (`התקבל, ונרשם כדיווח...`) before authoritative completion. This was not treated as success. In SEC2, the final authoritative outcomes contradicted the ACK for step 1 and step 5, while step 3 and step 8 eventually committed. The real Telegram Web tab was controllable after login; a query `מה מצב הדיווח של מיכאל?` was sent in the `Main Agent` chat, but no bot reply was observed during the subsequent wait window. Therefore no Telegram PASS is claimed for the pending-clarification or final-answer paths.

### Current checkpoint

Browser access is recovered. Task 76B real regression is not a full PASS: ACC-005, ACC-006, ACC-007, ACC-010 remain failing; ACC-004 and ACC-008 are persistence improvements with incomplete user-facing answers; terminology, membership, and broader planning findings remain open. The working tree must remain uncommitted, and the Task75D screenshots remain untracked evidence.

The final FIRE3 step-4 browser wait caused the CUA kernel to reset because processing exceeded the control timeout. The authoritative DB was still readable afterward and showed the non-terminal `human_activation` event above. No automatic retry or Simulator advancement was performed after that timeout.

## Task 76C targeted runtime repair and real regression

The runtime was restarted from the current working tree after the repair changes. No database reset, pytest, commit, or push was performed. The Telegram identity used for simulator bindings remained `2077472944` (בר משה); no LIVE membership was added to the standby class or fire brigade. The simulator identity binding was used only to bind the exact run under test.

### Root cause and repair

SEC2 camera step 1 was not a single-camera state bug. The message explicitly named cameras 03 and 04. The deterministic surveillance extractor rejected more than one camera reference, so intake fell through to the model extractor, which produced an unscoped `camera_id=03` and missing `area`, then opened an invalid event-data hold. The repair adds scoped multi-camera extraction and accepts a canonical `camera_ids` field. Ingestion resolves every camera in the run scope and updates all of them in the same report-ingestion operation; projection is returned only after the domain updates succeed. The malformed event-data answer path also keeps the original hold pending instead of resolving it as an abandoned parse.

### Fresh browser evidence

| Scenario / fresh run | Step | Browser result | Authoritative result |
|---|---:|---|---|
| SEC Phase 2 / `85b1b0db76a44fc89b445a84f13442d2` | 1 | ACK, no deadline error | `surveillance_report`, `succeeded`; `camera_id=CAM-03`, `camera_ids=CAM-03,CAM-04`, `area=east_fence`; both CAM-03 and CAM-04 are `offline` in surveillance DB |
| SEC Phase 2 / same run | 4 | Situational picture visibly reported `4/6` active and `2` offline/maintenance | Correctly reflected the camera projection; the former `6/6` stale picture was not reproduced |
| SEC Phase 2 / same run | 3 | ACK | `team_operational_report`, `succeeded`; heavy equipment at `west_gate` persisted; planned-work question remained explicitly unverified |
| SEC Phase 2 / same run | 5 | ACK, no internal-error text | `surveillance_report`, `succeeded`; `CAM-03` committed offline with the cut-cable observation |
| SEC Phase 2 / same run | 7 | Response was roster only | Multi-task commander request remains incomplete; no correlation/approval/deployment recommendation observed |
| SEC Phase 2 / same run | 8 | ACK only; no question answer visible in Simulator | `team_operational_report`, `succeeded`; `location=east_sector`, `operational_status=in_transit`; insight states no authoritative co-traveller information was included |
| SEC Phase 1 / `8a545c301f4947429cc9e1134bfcfb95` | 6 | ACK only; no clarification text visible in Simulator | Durable `team_attendance_report` with `availability=unavailable`, `reason=illness`, null interval fields, and unresolved event-data hold for `availability_start`/`availability_end` |
| FIRE Phase 3 / `422b1a38fac04c309dc41b339e91c52f` | 1 | Simulator displayed internal-error response | Runtime log shows OpenAI request timeout (`trace 201a74b1`) before report completion; severity path is not PASS |
| FIRE Phase 3 / same run | 4 | ACK for action request; no terminal response in Simulator | `human_activation`, `approval_held=1`, `action_state=pending_approval`, `selected_protocol=dispatch_emergency_forces`, high risk; approval hold `b8d0250d77a4493f935b956048da3648` remains the truthful waiting state |

SEC2 also exposed a separate step-2 failure in this fresh run: the Simulator displayed an internal-error response and the API log records an OpenAI request timeout at `11:11:38` (`trace 527e087d`). This is recorded as a runtime/provider failure, not treated as a camera or persistence PASS.

### Telegram verification

The real Telegram Web session was reopened successfully in Brave and the `Main Agent` chat was inspected. No new final response corresponding to the exact fresh SEC2 or SEC1 simulator runs was observed in that chat. Existing Telegram content included earlier approval-expiry messages, but it was not attributed to the fresh runs and was not used as PASS evidence. Telegram final-reply delivery therefore remains unverified/failing for this regression.

### New/updated ACC dispositions

| Finding | Disposition | Evidence |
|---|---|---|
| ACC-005 camera 03/04 stale projection | fixed in fresh runtime | SEC2 run `85b1b0db76a44fc89b445a84f13442d2`: both cameras projected offline; step 4 showed `4/6` |
| ACC-006 cut-cable pre-persistence failure | fixed in fresh runtime | Same run step 5 has canonical succeeded event and CAM-03 offline projection |
| ACC-010 Michael bounded interval | fixed for durable pending behavior; final user reply still unverified | SEC1 run `8a545c301f4947429cc9e1134bfcfb95`: grounded absence report persisted and clarification hold remains open with null bounds |
| ACC-004 planned-work question | still failing / incomplete response | Heavy-equipment fact persisted, but no authoritative planned-work answer was observed |
| ACC-007 commander multi-task request | still failing / deferred | Fresh SEC2 step 7 returned only roster |
| ACC-008 Gil mixed report/question | persistence fixed; response incomplete | Fresh SEC2 step 8 persisted the in-transit report, but no final answer to “who else is with me” appeared; authoritative result says information is unavailable |
| ACC-011/012 severity/runtime intake | still failing in FIRE3 step 1 | Fresh FIRE3 step 1 ended internal error; API trace identifies provider request timeout |
| ACC-014 final Telegram replies after ACK | still failing / unverified | Fresh Simulator views showed ACK only; no matching final Telegram response observed |

## Task 76C checkpoint

The targeted camera and cut-cable repairs were reproduced successfully in fresh browser runs. Pending clarification and approval are durable and truthful in authoritative state, but their user-facing final/clarification Telegram delivery was not observed. FIRE3 step 1 and SEC2 step 2 still fail on provider request timeouts, SEC2 step 7 remains a multi-task orchestration limitation, and mixed-question responses remain incomplete. Working tree changes and all acceptance evidence remain uncommitted; screenshots remain outside any commit.

## Task 76D — provider resilience and terminal delivery verification

The runtime was restarted from the current working tree before these checks. No database reset, checkout, stash, commit, or push was performed. Bar Moshe (`2077472944`) was bound only to the exact run under test; no LIVE membership was added to either operational unit.

| Case | Fresh run | Browser result | Authoritative result |
|---|---|---|---|
| SEC2 step 2, moving commercial vehicle report | `f1fe1eed71e94c67affd467d17c99c10` | Normal ACK; no internal-error response; Simulator advanced past step 2 | Event `48e5ff4e60b6423e9d7db419b2b5d09c`, `friendly_forces_report`, `outcome=succeeded`, `business_fields={"incident_kind":"security_observation"}`, notification sequence 107 `job_finished` |
| FIRE3 step 1, hazardous-fire report | `d9d13b7fa04d43daac246470b8051969` | Normal ACK; no provider-error response; Simulator advanced to step 3 | Event `105461907d534cd5bc25321f73c74c24`, `team_operational_report`, `outcome=succeeded`, fields preserve only `operational_status=reported` and the explicit uncertainty that no response action was inferred; notification sequence 108 `job_finished` |

### Provider root cause and fallback

The prior SEC2 step 2 failure (`trace 527e087d`) and FIRE3 step 1 failure (`trace 201a74b1`) were real provider request timeouts. Both requests entered the group-agent path and failed before a canonical event was created, so the grounded report was lost. The fresh 76D events did not need a provider call: trusted extraction recognized the concrete vehicle observation and concrete hazard report, persisted scalar facts, and deliberately did not infer unsupported severity or action/protocol. This is a deterministic fallback, not a timeout increase or exception-to-success conversion.

### Terminal response and delivery contract

The Simulator’s group bubble is the synchronous ACK. Asynchronous `job_finished`, `job_failed`, `event_data_hold`, and `approval_hold` notifications target the event sender identity/private Simulator chat, not automatically the originating group card. The authoritative notification log contained `job_finished` for both fresh fallback events; a group card showing only the ACK is therefore not proof that the terminal notification was absent.

A fresh controllable Telegram Web tab sent `מה תמונת המצב של ריצת FIRE3 הנוכחית?` after Bar Moshe was bound to `FIRE_002_PHASE_3/d9d13b7fa04d43daac246470b8051969`. The same Main Agent chat visibly returned a final `תמונת מצב כללית` response at 11:40, including the newly committed FIRE3 hazard report. This verifies final delivery to the real originating private conversation for that query. No approval button was pressed.

### Holds and remaining findings

* Michael’s grounded absence report remains durable with null availability bounds and an open event-data clarification hold. No interval was invented. The authoritative hold is correct; a distinct fresh user-facing clarification response remains to be independently verified.
* FIRE3’s existing `pending_approval`/`approval_hold` remains the truthful waiting state. The action was not executed; the approval prompt was visible in Telegram Web.
* SEC2 heavy-equipment and Gil reports remain persisted, but the planned-work and co-traveller questions were not observed receiving distinct authoritative final answers. These remain open.
* SEC2 step 7 remains a multi-task orchestration limitation: the commander request returned only a roster and is deferred to Operational Intelligence.
* FIRE terminology/free-text routing findings remain open; no membership was changed to make this verification pass.
* ACK-versus-terminal delivery is clarified for the real private Telegram query and for notification-log emission, but global delivery is not marked fixed until the remaining originating-conversation cases are independently observed.

### Task 76D dispositions

* SEC2 step 2 provider-timeout report loss: fixed and reproduced in a fresh browser/runtime run.
* FIRE3 step 1 provider-timeout report loss: fixed and reproduced in a fresh browser/runtime run.
* Real Telegram final response for an exact bound run: verified for the fresh FIRE3 query.
* Michael clarification delivery: still open; durable hold is verified.
* FIRE3 approval: verified as pending; action not executed.
* Mixed report/question answers: still failing/deferred, not marked fixed.

### Quality-gate update

After the browser verification, the focused regression set initially exposed two stale schema-contract failures because `camera_ids` had been made mandatory for single-camera reports. The contract was corrected so the multi-camera trusted path can still persist `camera_ids` while ordinary single-camera reports remain valid. The focused set then passed: `120 passed, 1 warning`. `python -m compileall -q agents api orchestrator profiles persistence` and `git diff --check` passed. A full `pytest -q` run was started but was interrupted before producing a summary; it is not claimed as a PASS. No commit or push was made.
