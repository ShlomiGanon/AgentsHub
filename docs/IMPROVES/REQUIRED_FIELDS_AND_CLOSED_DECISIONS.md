# AgentsHub — Required-Event-Fields Mechanism + Two Closed UX Decisions

| Part | Status | Date |
|---|---|---|
| 1 — Required-event-fields mechanism | done | 2026-09-04 |
| 2 — #8 uncertain-outcome visibility | done | 2026-09-04 |
| 3 — #9 protocol/reason suffix | done | 2026-09-04 |

**Context:** This closes out the three "needs your call" items from
`docs/IMPROVES/CRITICAL_FIXES_PLAN.MD` (#6, #8, #9). #6 required design
discussion first and is specified in detail below. #8 and #9 were
already decided and are specified precisely so there's no ambiguity left
for you to resolve.

---

## HARD RULE — read this before touching anything below

**No Hebrew string, anywhere, in any source file that is not a
translation/message-catalog file.** Not as a field name, not as a default
value, not as a fallback string, not as a dictionary/enum key, not as a
code comment, not as test data unless the test is specifically testing
Hebrew-language output through the existing catalog mechanism. Every
internal identifier (field names, event-type keys, schema keys) stays in
English, exactly like every existing identifier in this codebase
(`occurred_at`, `classification`, `area`, etc.). Every piece of Hebrew
text the user ever sees must go through the existing message-catalog
mechanism (`messages/he.py` or equivalent) — the same mechanism already
used for everything else in this system. If you find yourself about to
write a Hebrew literal anywhere else, stop and route it through the
catalog instead.

This rule exists because the investigation series already found two
separate live bugs (in `docs/IMPROVES/UX_PREDICTABILITY_PLAN.MD` C.3 and
`docs/IMPROVES/TELEGRAM_UX_FINDINGS.MD` C.2) caused by exactly this kind
of untranslated-value leakage. Do not introduce a third instance while
building something new.

---

## Part 1 — Required-event-fields mechanism

**Status: done — see `docs/progress.md` entry 2026-09-04.** The corrected
design below (unclassified as a real built-in event type, an early gate
before protocol selection) is implemented exactly as specified. One
implementation note: the "unclassified" required-field lookup is fixed
directly in `EventTypeRegistry.required_fields_for` (always returns
`("area",)` for that type) rather than being injected into the
`required_fields` dict at `profiles.loader.build_event_type_registry`
construction time — this makes the behavior hold for every
`EventTypeRegistry`, including the many test fixtures across this codebase
that construct one directly rather than through the profile loader.

**Important: this supersedes an earlier, incorrect assumption.** The
first version of this document treated `human_activation` as a distinct
event type with its own required field (`area`). That was wrong, and the
correct model — worked out with the user directly — is below. Do not
implement the old `human_activation`-has-its-own-required-fields version
even if you find it referenced anywhere else (chat history, an earlier
draft, etc.) — this document is the authority.

**Note on the earlier attempt at this item:** a previous pass correctly
stopped and flagged back instead of guessing, reporting that the existing
`waiting_for_event_data` mechanism only discovers missing fields
per-protocol-step, mid-run, with no upfront per-event-type required-field
list, and that no profile declares such a list today. That flag was
correct and is now resolved: the sections below give you the settled
design (an early, unified, pre-protocol-selection gate) and the reasoning
for why it must work this way. You do not need to re-raise that flag —
proceed with implementation using the design below.

**The corrected model of what an "event type" actually is:**

- **`human_activation` is not an event type.** It is a **source label**
  layered on top of whatever real event type the report resolves to. A
  person saying "I see fire" is a **fire** event (same event type a
  sensor would produce), just with its origin marked as human rather
  than sensor. `human_activation` therefore carries **no required
  fields of its own** — it has nothing to declare, because it doesn't
  describe *what happened*, only *how the report arrived*.
- **There are now two real kinds of event type**, both of which need
  required-field declarations and both go through the same enforcement
  mechanism:
  1. **Profile-defined event types** (e.g. "fire", "injury") — required
     fields declared in the profile, alongside everything else the
     profile already defines for that type.
  2. **A new built-in event type: "unclassified"** — this is the
     fallback when a report doesn't match any event type the active
     profile knows about. It is not defined by a profile (it's the
     catch-all for when profile matching fails), so, like
     `human_activation` was originally assumed to be, it is declared in
     **core code**, since it exists in every deployment regardless of
     profile. **The user has decided its required field: `area`.** ("if
     the system doesn't recognize an event, classify it as unclassified,
     and it should still ask where the problem is before saving it.")
  `human_activation` remains a label that can sit on top of either kind
  (a human can report a known event type, or a human can report
  something the system fails to classify, which becomes
  "unclassified + human-sourced").

**Where the required-field gate must sit — settled, with reasoning:**

The user identified the deciding case directly, so implement exactly
this, don't re-derive it:

- **Unclassified events have no protocol to attach to at all.** Per the
  user: an unclassified event is deliberately left with no protocol,
  sitting until a commander decides what to do with it. There is no
  "already-selected protocol" to inject required fields into for this
  case — so a late, per-protocol-step check (the existing
  `waiting_for_event_data` mechanism, which only fires once execution
  reaches a step inside an *already-selected* protocol) cannot work for
  unclassified events. This alone requires a gate that runs **before**
  protocol selection.
- **The same must apply to profile-defined event types too, not just
  unclassified ones** — and this is the more important reasoning to
  preserve, since it wasn't obvious at first: protocol selection is
  itself informed by the event's fields (risk assessment and protocol
  matching read whatever fields extraction produced). If a required
  field is missing at protocol-selection time, the protocol gets chosen
  based on incomplete information — and if the field's real answer
  (once asked) would have pointed to a *different* protocol, the system
  is now stuck with a protocol selection that was made on bad
  information, with no clean way to undo it. The user's own example:
  imagine the missing `area` turns out to mean "open field," not
  "populated area" — the heavier protocol that got selected might never
  have been the right one if the system had known that up front. This
  is a correctness problem, not just a tidiness one, and it's why a
  late "inject into the already-chosen protocol's first step" design
  (previously called "option B" in this document's drafting) was
  rejected — it can silently commit to the wrong protocol.
- **Conclusion: a single, early, unified gate, checked before protocol
  selection, for every event type** (profile-defined and unclassified
  alike). This is not an optimization or a style preference — it is
  required for correctness given how protocol selection depends on
  event fields.

### Concrete behavior to implement

- Immediately after a report/request resolves to an event type (whether
  a profile-defined type or the new "unclassified" fallback), and
  **before** risk assessment or protocol selection run, check that
  type's declared required fields against what extraction produced.
- If any are missing, the event enters `waiting_for_event_data` (reusing
  the existing hold state and its existing "ask the original sender, in
  their language, for all currently-missing details in one combined
  question" behavior — do not build a second, separate question-asking
  path). Risk assessment and protocol selection do not run yet.
- Once all required fields for that event type are resolved (in one
  reply or across a few, per the existing multi-field resolution
  behavior), risk assessment and protocol selection proceed normally,
  now working from complete required-field data.
- For "unclassified" events specifically: once `area` is resolved, the
  event is saved in the no-protocol/awaiting-commander state the user
  already described — this document is not proposing any change to what
  happens *after* that; only that `area` is collected first rather than
  the event being saved without it.
- Every required field still needs two things: an internal English field
  key (e.g. `area`), and a display label routed through the message
  catalog (`messages/en.py` / `messages/he.py`) — do not hardcode
  question text per field directly in orchestration code.

### Relationship to the existing `waiting_for_event_data` / per-step
  mechanism

This new event-type-level gate is **additional to, not a replacement
for**, the existing per-protocol-step required-field check discovered
during the earlier investigation (`protocols/executor.py` /
`orchestrator/flows.py`). A protocol step can still legitimately need a
field that has nothing to do with the event type itself (e.g. a step
deep in a specific protocol needing an operational detail no event type
would ever require up front). Keep both:

- **Event-type-level required fields** — checked once, early, before
  protocol selection (this document's new mechanism).
- **Per-protocol-step required fields** — checked as today, when
  execution reaches that step (unchanged, existing mechanism).

Both should share the same underlying "ask the sender, hold, resume"
plumbing rather than becoming two independently-built hold systems —
reuse the existing hold infrastructure for both, just trigger the new
one at an earlier point in the flow.

### Telegram mockup — full example in Hebrew, no English anywhere in the
  user-visible text (per the hard rule above)

**Case 1 — profile-defined event type, e.g. "fire," missing a required
field before protocol selection:**

Sender:
> יש עשן ליד המחסן

System (event type resolved to "fire," required field `area` missing —
asked before any protocol is selected):
> [נדרש פירוט]
>
> קיבלתי את הדיווח שלך על עשן ליד המחסן.
> באיזה אזור זה קורה?

Sender:
> באזור הצפוני

System proceeds to risk assessment and protocol selection only now, with
`area` already known.

**Case 2 — report doesn't match any known event type, resolves to
"unclassified":**

Sender:
> יש פה בעיה

System (event type resolved to "unclassified," required field `area`
missing):
> [נדרש פירוט]
>
> קיבלתי את הדיווח שלך.
> באיזה אזור יש בעיה?

Sender:
> ליד השער הראשי

System saves the event as unclassified, with `area` now known, awaiting
a commander's decision — no protocol runs automatically.

### Suggested regression tests

- A profile-defined event type (e.g. a test fixture "fire" type) with
  two required fields, one resolvable from the message text and one
  not: assert the system asks only for the missing one, **before** risk
  assessment/protocol selection ever run (assert those functions are not
  called until the field is resolved), in the sender's language, and
  that both the internal field key and the display label route through
  the correct mechanism.
- A message that doesn't match any profile event type: assert it
  resolves to "unclassified," is asked for `area` before being saved,
  and after resolution is saved with no protocol selected/executed —
  exactly as described in "Concrete behavior."
- Directly test the "wrong protocol would have been selected" risk
  scenario is now prevented: construct a case where risk assessment
  would produce a different result depending on the required field's
  value, and assert protocol selection never runs until that field is
  known (regression test that the gate genuinely blocks, not just that
  it asks a question).
- A message that answers only one of two missing fields: assert the
  event stays in `waiting_for_event_data` and the follow-up question
  asks only for the field that's still missing.
- Confirm the existing per-protocol-step mechanism (from
  `protocols/executor.py` / `orchestrator/flows.py`) is untouched and
  still fires correctly for step-level required fields unrelated to the
  event type gate — regression-test that both mechanisms coexist without
  interfering with each other.
- A grep-based regression test scanning new source files for Hebrew
  Unicode range characters outside `messages/he.py` (or the project's
  equivalent catalog file/module) — this makes the hard rule above
  mechanically enforced, not just a request you have to remember.

### Before implementing — flag back to the user if:

- Any existing profile or test fixture would break because it doesn't
  declare required fields for its event types yet — default to "no
  required fields" (backward compatible) for any event type that
  doesn't declare any, rather than failing validation, unless told
  otherwise.
- The event-type resolution step (the point where a report is matched to
  a profile event type or falls through to "unclassified") turns out not
  to be a single, identifiable point in the code you can cleanly gate —
  if it's smeared across multiple places, describe the actual shape
  found before proposing where to insert the check.

---

## Part 2 — Item #8 (closed decision): "uncertain" outcome visibility

**Status: done — see `docs/progress.md` entry 2026-09-04.**

**Decision:** In addition to the existing commander-only detailed
`uncertain_verdict` notice, the **original reporter** (regardless of
role — viewer or commander) now also receives a short, generic notice
with no internal/raw insight text, when their event resolves to
`"uncertain"`.

- **File(s) to change:** wherever the existing `uncertain_verdict`
  notification is dispatched (per `docs/IMPROVES/UX_PREDICTABILITY_PLAN.MD`
  B.2's finding — `persistence/sqlite_store.py`'s
  `_OUTCOME_NOTIFICATION_KINDS` and the corresponding bot-side handler).
  Add a second, distinct notification target/kind for the original
  sender, rather than modifying the existing commander-only message.
- **Message content for the reporter (via the message catalog, both
  languages):** short and generic — no risk level, no insight text, no
  internal reasoning. English: "Your reported event is still being
  reviewed. We'll update you if there's more to share." Hebrew (via the
  catalog, not hardcoded): matches the mockup below.
- **Telegram mockup — full example in Hebrew:**
  > [עדכון]
  >
  > האירוע שדיווחת עליו עדיין נבדק.
  > נעדכן אותך כשיהיה מידע נוסף.
- **Suggested regression test:** trigger an `"uncertain"` outcome for an
  event submitted by a viewer identity; assert the viewer receives the
  short generic notice (exact catalog key, not raw insight text) and that
  commanders still separately receive the existing detailed notice
  unchanged.
- **Priority:** as previously assessed in the source finding — medium,
  now unblocked since the open question is resolved.

---

## Part 3 — Item #9 (closed decision): always-on protocol/reason suffix

**Status: done — see `docs/progress.md` entry 2026-09-04.** Implementation
note: "risk/selection reason" was resolved as the protocol **selection**
reason (`protocol_reason`, i.e. why this protocol was chosen), not the risk
assessment's own reason — matches "the protocol that ran and a brief reason
[it ran]" most directly; `risk_reason` was left unused here since the
`risk_level` word alone already appears in the suffix. Flag if this reading
should instead have been the risk reason.

**Decision:** Every completed report/request result message includes a
short, fixed suffix naming the protocol that ran and a brief reason —
**always on**, not on-demand. This applies to protocol-driven
report/request outcomes; it does **not** apply to plain question/
conversation replies, which have no protocol to name.

- **File(s) to change:** `format_job_result` (or wherever the final
  result message is composed, per `docs/IMPROVES/UX_PREDICTABILITY_PLAN.MD`
  A.1's finding that this function currently omits protocol/risk
  information entirely). Add the suffix there, sourced from data already
  computed during the run (protocol name, risk level, risk/selection
  reason) — this should not require a new model call, since the
  reasoning already happens today and is just not surfaced.
- **Message shape (via the message catalog, both languages):** the
  existing result message, unchanged, plus exactly one short trailing
  line — not a paragraph, not a bullet list of reasoning.
- **Telegram mockup — full example in Hebrew:**
  > [תוצאה]
  >
  > הדיווח טופל בהצלחה.
  >
  > פרוטוקול: תגובת שריפה (סיכון גבוה, אין תקדים דומה)
- **Suggested regression test:** submit a report that triggers a known
  protocol; assert the final message includes exactly one trailing
  protocol/reason line, correctly localized, and assert a plain question/
  conversation reply does **not** gain this suffix (scope check).
- **Priority:** as previously assessed — medium, now unblocked.

---

## Process (applies to all three parts above)

- Implement and test Part 1, Part 2, and Part 3 separately — they touch
  different code paths and should be reviewable independently, same as
  the earlier undisputed-fixes batch.
- **Mark each part as completed the moment it's done, so nothing gets
  redone or re-reviewed by mistake.** Concretely: at the top of this
  file, right under this file's title, add a small status table with one
  row per part —

  ```
  | Part | Status | Date |
  |---|---|---|
  | 1 — Required-event-fields mechanism | done / in progress / not started | <date> |
  | 2 — #8 uncertain-outcome visibility | done / in progress / not started | <date> |
  | 3 — #9 protocol/reason suffix | done / in progress / not started | <date> |
  ```

  Update this table in place as you go — don't wait until everything is
  finished to fill it in, since the user may check in partway through.
  Also add a one-line "Completed" note directly under each part's own
  heading (e.g. `**Status: done — see docs/progress.md entry
  2026-09-XX.**`) once that part is fully implemented and tested, so
  the completion is visible both in the summary table and at the part
  itself.
- Update `docs/progress.md` with one entry per part, matching this
  project's existing append-only convention.
- Do not touch anything still on hold from the earlier authorization
  (item #10, the situation-picture item, or anything in the "also worth
  knowing about" list) as a side effect of this work.
- When done, report back a short summary per part: what changed, which
  file(s), what test was added (including the Hebrew-leakage grep test
  for Part 1), and confirm the full test suite still passes.
