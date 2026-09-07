Work Plan: Hold/Notification Rendering Consistency Audit & Refactor Options

No code changes made. This is a plan for review.

---

1. Audit — every place structured data becomes human-readable text

Location: tools/observability.py, render_deep_debug_entry (DEEP_DEBUG trace stream)
Reads: Log record event field, ~20 values
Switches on: Big if/elif on event, each delegating to a catalog.text("debug.xxx", **raw_fields) template
Fallback behavior: Safe. Returns None for an unrecognized event (caller skips it); for hold_created/hold_resolved specifically,
passes hold_kind straight into the template ("Trace: {hold_kind} hold was created...") — no sub-switch, no mislabeling possible.
────────────────────────────────────────
Location: tools/observability.py, _EVENT_RENDERERS dispatch table + _HumanReadableFormatter (the console log line you saw)
Reads: Same event field
Switches on: _EVENT_RENDERERS.get(event, _render_default) — dict dispatch, safe at this level
Fallback behavior: Falls back to _render_default (prints the raw log message) for an unrecognized event — safe.
────────────────────────────────────────
Location: ↳ _render_hold_created (line 374)
Reads: hold_kind (values in use: "approval", "clarification", "event_data")
Switches on: if hold_kind == "clarification": ... else: <assume "approval">
Fallback behavior: The known bug. "event_data" (and any future kind) silently renders as "approval hold created → reason: ?" — a
fabricated field (reason) that event_data holds don't even have.
────────────────────────────────────────
Location: ↳ _render_hold_resolved (line 380)
Reads: Same hold_kind vocabulary
Switches on: Identical two-way branch
Fallback behavior: Same defect, currently dormant — no code path logs hold_resolved with hold_kind="event_data" today (confirmed:
only "clarification" and "approval" resolutions are ever logged), so it doesn't currently misfire, but it would the moment one
is added, with zero warning.
────────────────────────────────────────
Location: ↳ _render_protocol_selection, _render_event_outcome, etc.
Reads: status/outcome fields
Switches on: if/elif with a final return f"... → {status} (...)" catch-all
Fallback behavior: Safe — the catch-all prints the real, raw value rather than a wrong hardcoded label. This is the "transparent
fallback" pattern; contrast with _render_hold_created's "confident wrong label" fallback.
────────────────────────────────────────
Location: bot/background_services.py, dispatch_notification (line 37)
Reads: BotNotification.kind, a Literal of 9 values (bot/contracts.py:225)
Switches on: 9 explicit if notification.kind == "...": ... return branches
Fallback behavior: Exhaustive and fail-loud — ends in raise ValueError(f"unknown notification kind: {notification.kind!r}"). This
is the best-designed instance found; nothing to fix here.
────────────────────────────────────────
Location: bot/interactions.py, format_header
Reads: MessageKind, a Literal of 10 values
Switches on: _HEADER_KEYS[kind] — plain dict indexing
Fallback behavior: Fail-loud — an unhandled kind raises KeyError immediately. Verified _HEADER_KEYS (10/10) and both locale
catalogs' header.* keys (10/10 each) currently match exactly.
────────────────────────────────────────
Location: bot/interactions.py, _outcome_word / _risk_level_word
Reads: outcome (VALID_OUTCOMES, 6 values) / risk_level ("high"/"low")
Switches on: Dynamic catalog-key lookup: catalog.text(f"outcome.{outcome}")
Fallback behavior: Safe by design — except MessageCatalogError: return outcome on a miss shows the raw value verbatim, never a
wrong label. Verified both locale catalogs currently have all 6/2 keys. This is the other strong reference pattern found.
────────────────────────────────────────
Location: bot/interactions.py, format_approval_prompt
Reads: HeldApprovalNotice.reason ("flagged_protocol" / "ambiguous_selection")
Switches on: if/elif
Fallback behavior: Fail-loud — ends in raise ValueError(f"unrecognized approval hold reason: {notice.reason!r}").
────────────────────────────────────────
Location: bot/interactions.py, _describe_outcome / _describe_clarification_outcome
Reads: HoldAnswerResult.status (5 values for approval, 4 for clarification — enumerated directly from orchestrator/holds.py)
Switches on: if/elif, catch-all for the one remaining status ("not_found")
Fallback behavior: Correct today, verified against the actual status values answer_approval_hold/answer_clarification_hold can
return — every value is handled, though _describe_outcome's catch-all tuple also lists "invalid_classification", a status that
structurally can't occur for an approval answer (harmless dead code, mildly confusing).
────────────────────────────────────────
Location: tools/terminal_client_commander.py, _handle_holds_command (line 198)
Reads: note.kind, but only ever "clarification_hold" or "approval_hold" by construction (filtered earlier by the literal tuple
_HOLD_KINDS, line 40)
Switches on: if note.kind == "clarification_hold": ... else: <assume "approval_hold">
Fallback behavior: Same shape as the known bug, currently safe only because of an upstream filter in the same file — nothing stops
a future third "needs a prompt" hold kind from being added to _HOLD_KINDS without this else being updated to match; it would
silently mis-render exactly like _render_hold_created did.
────────────────────────────────────────
Location: tools/terminal_client_viewer.py
Reads: note.kind, but only to detect job completion (in ("job_finished", "job_failed", "event_data_hold"))
Switches on: Membership check, not a rendering switch — actual text comes from dispatch_notification
Fallback behavior: Not applicable — no independent kind→text mapping here.
────────────────────────────────────────
Location: api/routes.py, job_status()
Reads: event["outcome"], three held-event kinds
Switches on: Additive if/elif (adds an optional "detail" field); three explicit, separate hold-kind checks (approval,
clarification, event_data), each producing its own correctly-labeled status string
Fallback behavior: Safe — status is always set to the true value first; elif branches only enrich, and the three hold checks are
each named explicitly with no shared fallback between them.
────────────────────────────────────────
Location: api/admin.py
Reads: N/A
Switches on: —
Fallback behavior: No hold/outcome/kind rendering at all (user-management panel only). Out of scope.
────────────────────────────────────────
Location: messages/en.py / messages/he.py
Reads: Fixed strings only
Switches on: —
Fallback behavior: Not a switch site themselves; audited as the targets of the lookups above. All catalogs checked (outcome.*,
risk.*, header.*, debug.*) are currently complete and matched 1:1 between locales.

---

2. Systematic pass for the same failure pattern

Every hardcoded kind/status/type switch found, with an explicit verdict:

1. tools/observability.py::_render_hold_created — BUG, live. hold_kind has 3 real values (approval, clarification, event_data); only 1 is checked; the other 2 (including the one you hit) fall into a mislabeled branch.
2. tools/observability.py::_render_hold_resolved — Same-shaped defect, currently dormant (no event_data hold_resolved log call exists yet — but nothing prevents one being added later without anyone noticing this renderer needs updating).
3. tools/terminal_client_commander.py::_handle_holds_command — Same-shaped defect, currently safe only by an upstream filter (_HOLD_KINDS) that isn't re-checked at the point of use; a silent trap for a future third interactive hold kind.
4. bot/interactions.py::_describe_outcome's dead branch — not a bug (all live values handled correctly), but lists an impossible status ("invalid_classification") alongside real ones — a minor precision issue, not a rendering-correctness one.

Everything else audited in (1) — dispatch_notification, format_header, format_approval_prompt, _outcome_word/_risk_level_word, job_status, render_deep_debug_entry — is either exhaustive-and-fail-loud or transparently-falls-back-to-the-real-value. No other live mislabeling found.

---

3. Root-cause analysis

Why this happened: there is no single, shared definition of "what are the valid hold_kind values" anywhere in the codebase. Concretely:

- orchestrator/holds.py writes the three hold-kind strings ("approval", "clarification", "event_data") as bare literals at three separate call sites (store_held_event(...), and the matching logger.info(..., extra={"hold_kind": "..."}) calls in orchestrator/flows.py).
- tools/observability.py's renderers re-derive their own idea of "the kinds" from scratch, independently, with no import, no shared constant, no reference to orchestrator/holds.py at all.
- bot/contracts.py's BotNotificationKind is a different, only superficially similar vocabulary ("approval_hold"/"clarification_hold"/"event_data_hold" — note the suffix), covering notifications, not the underlying hold-storage kind string. Two related concepts, two different string sets, neither centralizing the other.

Is this a one-off gap or a structural weakness? Structural, but unevenly so — this is the important nuance. The codebase already has, in the very same files, both the right pattern and the wrong one, side by side:

- Right pattern, already present: bot/contracts.py's BotNotificationKind (Literal[...], one place, 9 values) + dispatch_notification's exhaustive if/elif-with-raise. bot/interactions.py's MessageKind + _HEADER_KEYS dict (a KeyError on miss). _outcome_word/_risk_level_word's dynamic-catalog-lookup-with-transparent-fallback. This is directly comparable to EVENT_DATA_FIELDS/ViewerAllowedAction-style centralization elsewhere in the codebase — the convention exists and is well-established.
- Wrong pattern: hold_kind itself was never given the same treatment. No HoldKind = Literal["approval", "clarification", "event_data"] exists anywhere for it to be checked against. tools/observability.py's renderers were evidently added incrementally, one event type at a time, by someone reasoning "the two kinds I know about are clarification and approval" — without a definition to consult that would have shown a third value existed.

So: the codebase's engineering culture already knows how to do this correctly (three separate proofs of it exist), but that discipline wasn't applied to hold_kind specifically — nobody ever defined it as a first-class vocabulary the way BotNotificationKind/MessageKind/VALID_OUTCOMES were. That gap is exactly what let a renderer silently "cover the other case with the more common label" instead of being forced to enumerate.

Same shape as the _persist_step_outcomes bug from the earlier session, worth naming explicitly: that was also a "renderer/consumer papers over an unhandled case with a plausible-looking fallback, dormant until a new combination of inputs actually occurs" bug, in a completely different subsystem (persistence-matching, not logging). Two independent instances of the same failure shape in one codebase is a meaningful signal that "silent fallback instead of enumerated/fail-loud handling" is a recurring blind spot worth addressing as a pattern, not just patching each instance as found.

---

4. Refactor options

Option A — a single HoldKind enum/Literal, exported from orchestrator/holds.py, imported everywhere a hold kind is read.
- Where it lives: orchestrator/holds.py (the module that already owns store_held_event's kind strings and the "hold_created"/"hold_resolved" log calls).
- Touch: small. Define HoldKind = Literal["approval", "clarification", "event_data"]; use it as the type annotation for hold_kind in the two logger.info(extra={"hold_kind": ...}) call sites and in _render_hold_created/_render_hold_resolved's parameter types (tools/observability.py). Rewrite those two renderers as explicit 3-way (or dict-dispatch) branches ending in a raise/safe-generic-fallback, matching dispatch_notification's existing shape.
- Fit: matches the codebase's existing convention exactly (BotNotificationKind, MessageKind) — this is filling in a gap in an established pattern, not inventing a new one.
- Risk: very low, purely additive/typing; a static type checker (if one is run in CI — worth confirming) would then also flag any future if hold_kind == "clarification": ... else: as suspicious against a 3-member Literal, though Python doesn't enforce that at runtime on its own.

Option B — a shared rendering registry/dispatch table for "kind → renderer," reused across tools/observability.py, bot/interactions.py/background_services.py, and the terminal clients, instead of each file re-implementing its own branch logic.
- Where it lives: a new small module, e.g. tools/hold_rendering.py or extending orchestrator/holds.py itself, exporting one dict {HoldKind_value: render_fn} (or reusing dispatch_notification's existing dict-like shape as the template).
- Touch: larger — every one of tools/observability.py's two hold renderers, tools/terminal_client_commander.py's _handle_holds_command, and (for consistency) dispatch_notification itself would import from this one place rather than each maintaining its own branch. Reduces duplication, but touches more call sites.
- Fit: this generalizes the existing dispatch_notification/_EVENT_RENDERERS dict-dispatch style codebase-wide rather than leaving it as one good example among several ad hoc ones.
- Risk: medium — touches more files, and unlike Option A, changes how several currently-working functions are structured, not just adds a type. More surface area to accidentally regress a working notification path.

Option C — exhaustiveness enforced by a test, not by runtime code shape.
- Where it lives: a new test (see §6) that walks every hold_kind value actually produced anywhere in orchestrator/holds.py/orchestrator/flows.py (via a helper that reads the source, or — cleaner — a single exported constant, per Option A) and asserts each one renders through _render_hold_created/_render_hold_resolved/_handle_holds_command's branch logic to something that actually mentions that real kind (not a fixed string belonging to another kind).
- Touch: test-only, zero production code changes.
- Fit: lowest-risk of the three, catches the symptom (a wrong render) without touching the cause (no shared vocabulary) — on its own it would have caught this bug, but doesn't structurally prevent the next one in a not-yet-existing subsystem.
- Risk: minimal, but weakest guarantee — good as a complement to A, not a substitute for it.

My recommendation: A now, C alongside it, B only if you want the broader consolidation. A directly fixes the root cause with a small, well-precedented change; C locks the fix in with near-zero risk; B is a real improvement but is the kind of larger, multi-file restructuring that deserves its own separate review rather than riding along with a bug fix.

---

5. Prioritized fix list

Immediate, low-risk (do now):
- Fix _render_hold_created to handle all 3 hold_kind values explicitly (the originally-reported bug).
- Fix _render_hold_resolved identically, even though currently dormant — same file, same fix shape, essentially free to do together.
- Define HoldKind (Option A) and use it at the hold-kind producer/consumer sites listed above.
- Remove the dead "invalid_classification" entry from _describe_outcome's tuple (or add a one-line comment explaining why it's harmlessly listed) — trivial precision cleanup, not a functional bug.

Should decide on, moderate effort:
- tools/terminal_client_commander.py::_handle_holds_command's implicit two-way assumption — either make it check note.kind == "approval_hold" explicitly with its own else: raise, or derive it from the same HoldKind/BotNotificationKind source, so _HOLD_KINDS's membership and this function's branch can't drift apart silently.

Larger, defer/separate decision:
- Option B's full rendering-registry consolidation across tools/observability.py + bot/interactions.py/background_services.py + terminal clients — real value, but multi-file, changes working code shape, and deserves its own scoped review rather than bundling into "fix the mislabeling."

---

6. Test plan

1. Direct regression tests for the two known renderers — for each of _render_hold_created/_render_hold_resolved, one test per real hold_kind value (approval, clarification, event_data) asserting the rendered string names that kind correctly and never claims a different one (e.g. asserts "event_data" doesn't produce the substring "approval").
2. An exhaustiveness test tying the renderer to the actual vocabulary — once HoldKind exists (Option A), a test that iterates every value in HoldKind.__args__ (or an explicit tuple mirroring it) and asserts each one is handled by a real branch in _render_hold_created/_render_hold_resolved, not the silent fallback — this is the test that would have caught the original bug before it shipped, and catches a future new kind automatically without needing someone to remember to write a new per-value test.
3. Same exhaustiveness shape for tools/terminal_client_commander.py::_handle_holds_command, over _HOLD_KINDS's current and any future members.
4. A cross-locale catalog completeness test, generalizing what I verified manually this session: for outcome.* against VALID_OUTCOMES, risk.* against RiskAssessment.level's two values, header.* against MessageKind, and debug.* against every event name render_deep_debug_entry dispatches on — one test, run for both en and he catalogs, asserting no key is missing on either side. (Currently all pass by manual inspection; nothing currently enforces they keep passing as the vocab grows.)
5. Keep dispatch_notification's existing test (tests/test_bot_background_services.py) as the reference/positive-control example that this class of test is meant to generalize from.

---

7. Risks and scope-creep warnings

- Notification cursor/delivery-once guarantees (bot/background_services.py's NotificationCursorStore, the poll-loop cursor advancement): none of the proposed fixes touch cursor logic, delivery ordering, or poll_pending_notifications — this work is entirely about what text gets produced from an already-correctly-delivered notification, never about whether/when/how-many-times it's delivered. Flagging explicitly so implementation doesn't accidentally wander into that adjacent, more sensitive area.
- DEEP_DEBUG trace rendering (render_deep_debug_entry): audited and found not to have this bug — resist the temptation to "fix" it as part of this work; it's already safe and any refactor there is unrelated scope.
- Option B, if chosen: touches bot/interactions.py and tools/terminal_client_commander.py, both of which have real, currently-working Telegram/terminal delivery paths with their own tests — a consolidation refactor there should get its own dedicated review pass (and its own full test-suite run) separate from the immediate mislabeling fix, not be bundled into the same change.
- Locale catalog completeness test (§6.4): if it's added and immediately fails on some pre-existing, unrelated gap (the way test_file_catalog.py did last session), that's useful signal, not a reason to hold up the narrower rendering fix — report it separately rather than silently fixing catalogs to make the new test pass.
- hold_kind vs. BotNotificationKind naming: these are two distinct vocabularies today ("approval" vs. "approval_hold", etc.). Introducing HoldKind (Option A) should not be an opportunity to quietly merge or rename either vocabulary — that would touch persisted data (held_events.kind column values already on disk) and every existing log line's shape. Keep them separate and merely each explicit, unless you decide otherwise.

No code was written or modified, and no git commit was made, per your standing instruction.