# Investigation Summary — Conversational Path & Performance

Condensed action items from the full report. Full report retained separately for detail/citations.

---

## Part 1 — Conversational Back-and-Forth

**Current state:** No conversation exists. A "question" is a single stateless LLM call — no memory, no session, nothing persisted (`orchestrator/question_flow.py`). The Main Agent's system prompt explicitly forbids cross-call memory by design (`main_agent.py:34-39`).

**Verdict: Moderate change, not a rework.** The architecture already isolates the question path from the protocol/risk/approval machinery (no event write, no holds, no `FlowDeps` access) — a new conversation path can inherit that same safety property by construction rather than needing new guards.

### To build a real conversation path
- [ ] Add a 4th intent category (`conversation`/`clarifying_followup`) distinct from `question` — reuse `classify_intent`'s existing single fork point (`orchestrator/intent.py`).
- [ ] Add a new persistence record: conversation/thread keyed by sender identity, storing prior turns (text+answer), open/closed state. Nothing like this exists today — new surface, not a repurposing of `held_events`.
- [ ] New flow module `orchestrator/conversation_flow.py`, mirroring `question_flow.py`'s shape (read-only tools, no access to event-writing functions or `orchestrator.holds`).
- [ ] Structural guard (not just a prompt instruction): conversation flow must never receive `FlowDeps`/event-writing functions in its call signature — same pattern `answer_question` already uses.
- [ ] Escape hatch: mid-conversation, if the need turns out to require action, re-classify and hand off to the existing `request` → `human_activation` path — never act directly from conversation.

### Avoid
- Giving the conversation flow direct access to `create_approval_hold`/`determine_clarification_hold`/protocol execution "for convenience."
- Collapsing the new intent into the existing `question` category (overloads its semantics).

---

## Part 1b — "Activate protocol now, keep gathering details after"

**Current state: Not supported at all**, even partially. `_run_protocol` is one synchronous top-to-bottom call chain with no re-entry point once started. Existing hold mechanisms (clarification, approval) both **block the run and remove the event from the queue** — the opposite of what's needed here.

**Verdict: Moderate, with one caveat** (see below).

### To build this
- [ ] New, **non-blocking** mechanism — "open enrichment questions for event X" — separate from `held_events`, never removes the event from the serial queue.
- [ ] Populate candidate questions from `ExtractionResult.missing_fields` (already computed in `history/extraction.py:139-151`), filtered by Main Agent judgment of relevance.
- [ ] Reuse the existing bot-side answer-delivery pattern (`bot/clarification.py`) for asking/receiving answers.
- [ ] Late answers should land on the event record and reach the **Insights Agent's synthesis** (`orchestrator/insights.py`) — cheapest, safest option; does not change what already-dispatched agents did with the original (thinner) picture.

### The one caveat — do NOT attempt this (structural rework, not moderate)
- Rewriting an already-dispatched step's task, or steering a not-yet-run step with new facts mid-execution. Requires a re-entrant executor — explicitly out of scope per `protocols/executor.py`'s own "no task-based execution mode yet" seam (§4.8).

### Keep separate, do not conflate
- Clarification hold (blocks — for genuinely unclassifiable events) and this new enrichment mechanism (non-blocking — for classifiable-but-thin events) must stay two distinct mechanisms. Do not widen the clarification hold to "sometimes block, sometimes not."

---

## Part 2 — Speed & Quality

### Safe speed wins (no quality cost)
1. **Parallelize per-agent step execution** in `protocols/executor.py` for multi-agent protocols — steps' tasks are already all written in one call before any run (`orchestrator/formulation.py`), so there's no data dependency blocking this. 
   ⚠️ Requires deliberately reworking the retry policy's "already acted" / stop-on-first-failure semantics (`protocols/retry.py`) — not a one-line change, but self-contained.
2. **Split the single "core" model tier into per-role tiers.** Main Agent, History Agent, and Insights Agent all currently run on the *same* model tier — a direct departure from `work_plan.md §1.3`'s original intent (three separately-tuned models). 
   - Keep Main Agent on the strongest model (its judgment calls are the one place quality actually depends on model strength).
   - Move History Agent (bounded, mechanical summarization) to a faster/cheaper tier.
   - Move Insights Agent (bounded synthesis) to a mid tier.
   - This is additive to `config/base.py`'s existing `TierModel` machinery — not a rework.
3. **Investigate CrewAI/provider-side structured output** to remove today's fragile regex-parsing of Main Agent responses (`orchestrator/intent.py`, `selection.py`, `formulation.py` all rely on an "unverified prompt convention" parsed by regex, with retries on parse failure). Plausibly reduces latency (fewer retries) *and* improves reliability at the same time.
4. **Doc-only fix:** `docs/cost_latency_review.md` incorrectly claims precedent-lookup and Insights-Agent history reads aren't merged — they already are (`orchestrator/flows.py:647,676`). Update the doc, no code change needed.

### Not a bottleneck today, but note for later
- Full protocol descriptions are resent on every risk/selection/formulation call — negligible now (small demo profile), will matter if the protocol count grows into the dozens.

### ⚠️ Flagged — genuine speed/quality trade-off, do NOT do silently
- **Do not downgrade the Main Agent's own model tier** for cost/speed reasons. Its judgment calls (risk-threshold proximity, ambiguous protocol tie-breaks) are exactly the case where a weaker model gives a confident-but-wrong answer with no downstream check catching it.

### Worth a quick verification (not blocking)
- An open CrewAI GitHub issue reports `LiteAgent.kickoff()` (the exact path `agents/adapter.py` uses) may not correctly support tool calling in some versions. Project is pinned to `crewai>=1.15` (installed: 1.15.17). Recommend a real-model check against a tool-using agent — `tests/sanity_check_real_model_call.py` already exists and could be extended to cover this.

---

## Bottom line priority order
1. Model-tier split (Main vs. History/Insights) — clear win, low risk, restores original design intent.
2. Parallelize multi-agent step execution — clear win, but touch the retry-policy semantics carefully.
3. Structured-output investigation — likely free win on both speed and reliability.
4. Conversation path — moderate build, no rush, but architecture is ready whenever prioritized.
5. Non-blocking enrichment mechanism — moderate build, addresses a real gap (currently: incomplete-but-classifiable reports proceed with silently-narrowed precedent search and no follow-up).
