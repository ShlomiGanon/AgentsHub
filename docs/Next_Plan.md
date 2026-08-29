# AgentsHub Deferred Optimization Plan

## 1. Purpose

This document is the authoritative plan for performance and orchestration changes that were explicitly deferred until the current work in `SPEED_PLAN.MD` has been implemented and proven successful.

It replaces the previous contents of this file. Historical implementation records for the previous plan remain append-only in `docs/progress.md` and `docs/work_plan.md`.

This plan is not authorization to implement any deferred feature. Each stage requires a new user-approved design after the current speed work passes its quality gates. The user owns every behavior-changing decision.

## 2. Entry Criteria

No stage in this document may begin until all of the following are true:

1. Every stage in `SPEED_PLAN.MD` is complete.
2. Focused tests and the complete offline suite pass.
3. Startup warmup, provider timeout, `max_iter`, LLM telemetry, Deep Debug, localization, Telegram status editing, and CLI parity are working in the target profiles.
4. A post-change latency baseline has been recorded for direct conversation, direct question, history question, current-state question, report-to-hold, hold continuation, and one-step request routes.
5. No authorization, disclosure, persistence, ordering, or side-effect regression is open.
6. The user has reviewed the measured results and explicitly authorized preparation of the next implementation stage.

## 3. Decisions Already Locked for Future Design

The following decisions were made while preparing `SPEED_PLAN.MD`:

- The current legacy planner remains active during the present work. A merged planner is future work.
- Risk assessment and protocol selection remain separate during the present work. Their possible merge is future work.
- `Insights Agent` must remain a distinct registered agent. In the future design, that agent will perform both insight generation and judgment, preferably in one validated invocation when the safety gate permits it.
- The serial event queue remains active during the present work. A policy-aware worker pool is future work.
- CrewAI's current tool-selection loop remains active during the present work. A deterministic validated tool plan is future work.
- `STAGE_MODEL_POLICIES` will not be connected to runtime during the present work. Runtime activation and per-stage model selection are future work.
- Word-by-word streaming is removed from scope. It must not be reintroduced by this plan. Telegram and CLI use a single status message followed by one edit, as specified in `SPEED_PLAN.MD`.
- SQLite remains the persistence engine. No external message broker is introduced by this plan.
- Commander authorization remains unrestricted. Viewer authorization and disclosure remain derived exclusively from the viewer action enum.
- Conversation history may resolve references, but never becomes authoritative for facts, permissions, protocols, or outcomes.
- No performance improvement may weaken schema validation, authorization, side-effect rules, idempotency, or high-risk safeguards.

## 4. Required Evidence Package

Before proposing a future stage for implementation, create a versioned evidence package containing:

- route-level p50, p95, and p99 latency;
- provider-request counts per route and stage;
- input, output, and cache token counts;
- schema failures, semantic failures, repairs, and retries;
- tool-selection and tool-execution accuracy;
- high-risk false-negative results;
- protocol-selection validity;
- authorization and disclosure results;
- queue wait and execution time;
- SQLite write latency and query plans where relevant;
- a development corpus and a held-out corpus in Hebrew and English;
- at least 100 representative live or controlled examples for every route being optimized.

All comparisons must use the post-`SPEED_PLAN.MD` system as their baseline. Old mocked numbers are useful for regression detection but are not a production SLO.

## 5. Future Stage 1: Merged Message Planner Evaluation

### Objective

Evaluate whether one strict `MessagePlan` invocation can replace the current multi-call planning path without reducing intent, routing, authorization, clarification, or safety quality.

### Required design work

1. Freeze the current legacy planner outputs for the evaluation corpus.
2. Define the exact `MessagePlan` schema and application invariants.
3. Define which fields are authoritative application data and which are model suggestions.
4. Define validation for intent evidence, clarification, history operations, agent names, tool names, protocol names, filters, and conversational replies.
5. Define deterministic rejection behavior for malformed, ambiguous, or unauthorized plans.
6. Define the one-repair maximum and the remaining-deadline requirement for repair.
7. Define prompt layout with stable instructions and schemas before dynamic message and history content.
8. Define how prior conversation turns may resolve references without supplying operational facts.

### Files expected to change

- `orchestrator/reasoning.py`: planner schema, prompt, parsing, semantic validation, and repair classification.
- `api/routes.py`: planner-mode dispatch and role-aware application enforcement.
- `agents/contracts.py`: any final `MessagePlan` contract refinements.
- `profiles/contracts.py`, `profiles/loader.py`, `profiles/demo.py`, and `profiles/template.py`: planner mode after explicit rollout approval.
- `fixtures/`: versioned planner evaluation corpus.
- `tools/evaluate_response_pipeline.py`: shadow comparison and confidence-interval reporting.
- `tests/test_orchestrator_reasoning.py` and `tests/test_api_messages.py`: deterministic validation and authorization coverage.
- `docs/progress.md`: append-only stage results.

### Sequential execution

1. Add evaluation-only instrumentation; keep legacy behavior authoritative.
2. Run the merged planner in offline scripted tests.
3. Run it in `shadow` mode against the development corpus.
4. Compare disagreements by category, not only aggregate accuracy.
5. Repair schema or application validation using development data only.
6. Freeze the prompt and evaluate against held-out data.
7. Ask the user whether to enable a canary. Do not enable it automatically.
8. If approved, roll out to one canary profile with restart-based rollback.

### Success gate

- No high-risk or authorization regression.
- No protected viewer disclosure.
- No unknown agent, protocol, tool, enum, or filter reaches execution.
- Intent, routing, history, and clarification non-inferiority within two percentage points with a 95% confidence interval.
- Direct-question p50 improves by at least 30% against the post-`SPEED_PLAN.MD` baseline.
- Rollback to `legacy` requires only a profile change and restart.

## 6. Future Stage 2: Combined Risk and Protocol Selection

### Objective

Evaluate a single validated operational decision that returns both risk assessment and protocol selection while application code retains all authority and safety enforcement.

### Required design work

1. Define the combined `OperationalDecision` schema.
2. Keep risk thresholds, high/low derivation, protocol registry membership, ambiguity handling, approval requirements, and commander rules in application code.
3. Specify escalation to the current separate path for schema failure, missing evidence, ambiguity, high-risk uncertainty, or unknown protocol output.
4. Build a safety corpus containing fires, medical incidents, negation, understated severity, prompt injection, missing locations, and conflicting protocol descriptions.
5. Measure the separate and combined paths under identical inputs.

### Files expected to change

- `orchestrator/reasoning.py`: combined contract, prompt, validation, and classified failure modes.
- `orchestrator/flows.py`: shadow comparison, escalation, and application-owned enforcement.
- `profiles/contracts.py` and `profiles/loader.py`: future mode activation only after approval.
- `fixtures/`: operational-decision safety corpus.
- `tools/evaluate_response_pipeline.py`: comparison reports.
- `tests/test_orchestrator_flows.py` and `tests/test_orchestrator_reasoning.py`: safety and fallback tests.

### Sequential execution

1. Add a shadow-only combined decision.
2. Record disagreement details under the shared trace.
3. Run the development and held-out safety corpora.
4. Review every high-risk disagreement manually.
5. Ask the user whether to enable a canary.
6. If approved, enable only for low-risk canary traffic first.
7. Retain the separate path as rollback and escalation.

### Success gate

- Zero high-risk false negatives.
- Zero selection of nonexistent protocols.
- Zero bypass of approvals or authorization.
- Overall decision accuracy regression no greater than two percentage points.
- One provider request removed from eligible event routes.

## 7. Future Stage 3: Insights Agent Performs Insight and Judgment

### Objective

Keep `Insights Agent` as a distinct agent while allowing it to generate the operational insight and the final judgment in one validated assessment for eligible runs.

### Required design work

1. Define a strict output containing separate `insight`, `verdict`, `reasoning`, `evidence`, and `limitations` fields.
2. Define eligible routes. High-risk, uncertain, policy-sensitive, partial-failure, and side-effect-sensitive runs remain on the current multi-verifier path until separately approved.
3. Ensure the application derives the persisted outcome from validated enum values only.
4. Preserve all original step outcomes and evidence in the prompt.
5. Define automatic fallback to separate insight and judgment on schema or evidence failure.

### Files expected to change

- `orchestrator/reasoning.py`: combined Insights Agent assessment.
- `orchestrator/flows.py`: eligibility, fallback, persistence, and telemetry.
- `agents/`: no removal or renaming of `Insights Agent`.
- `profiles/contracts.py`: future activation policy after approval.
- `fixtures/`: verifier and critical-safety corpus.
- `tests/`: verifier disagreement, fallback, and persistence coverage.

### Sequential execution

1. Implement shadow output from `Insights Agent` without changing the stored verdict.
2. Compare it against the existing judgment path.
3. Review critical disagreements manually.
4. Restrict eligibility until no critical error is caught only by the old verifier.
5. Ask the user whether to activate the combined path.

### Success gate

- If the existing second verifier catches even one critical error missed by the combined assessment, the relevant route remains separate.
- Persisted final outcome exactly matches the validated assessment.
- Eligible runs save one provider request with no safety regression.

## 8. Future Stage 4: Activate Stage Model Policies

### Objective

Connect the already-defined stage model policy contracts to runtime only after the present runtime and telemetry changes are stable.

### Required design work

1. Inventory every model boundary and assign a stable stage name.
2. Define how a stage resolves core or sub model tiers.
3. Define precedence between agent defaults, profile policy, the 30-second provider timeout, shared deadlines, and structured-output requirements.
4. Ensure a policy cannot weaken mandatory validation or authorization.
5. Define fallback and escalation to a stronger model.
6. Validate thread-safety and cache keys for every selectable provider/model pair.

### Files expected to change

- `agents/runtime.py`: policy resolution at invocation time.
- `agents/contracts.py`: final policy contract if needed.
- `orchestrator/reasoning.py`, `history/query.py`, and `orchestrator/flows.py`: stable stage assignment.
- `profiles/contracts.py` and `profiles/loader.py`: required validation.
- `profiles/demo.py` and `profiles/template.py`: explicit policies after user approval.
- `tests/test_agent_runtime.py` and profile-loader tests.

### Sequential execution

1. Add a read-only report showing which policy would apply to each call.
2. Validate the report against real traces.
3. Enable output-token and timeout policy consumption without changing model tier.
4. Evaluate candidate models per stage.
5. Ask the user to approve every stage-to-model mapping.
6. Roll out one stage at a time.

### Success gate

- Every invocation reports its resolved policy.
- No silent fallback to an unspecified model.
- Quality gates pass per stage before a faster model is selected.
- The fastest passing model is selected; cost breaks quality and speed ties only.

## 9. Future Stage 5: Policy-Aware Worker Pool

### Objective

Replace the serial queue only after the single-request path is stable and measured.

### Required design work

1. Confirm one API process remains the production topology.
2. Define worker count, total capacity, reserved continuation capacity, sender serialization, event serialization, and side-effect concurrency keys.
3. Preserve the single SQLite writer.
4. Reserve capacity before event creation.
5. Define `503` and `Retry-After` behavior for full capacity.
6. Keep all steps inside one event ordered unless a later DAG stage explicitly permits parallel execution.
7. Ensure retries cannot duplicate non-idempotent side effects.

### Files expected to change

- `orchestrator/event_queue.py`: admission, workers, locks, deadlines, and shutdown.
- `api/routes.py`: reservation-before-event and overload responses.
- `orchestrator/flows.py`: continuation priority and trace propagation.
- `persistence/sqlite_store.py`: no additional writer; read-path verification only.
- `profiles/contracts.py`, `profiles/loader.py`, and target profiles: settings after approval.
- concurrency and load tests under `tests/` and `tools/`.

### Sequential execution

1. Build deterministic concurrency tests against the existing policy queue implementation.
2. Verify sender and side-effect locking.
3. Test full-queue admission before enabling workers.
4. Run load tests with read-only workloads.
5. Add mixed read/write workloads and injected failures.
6. Ask the user to choose worker and capacity values.
7. Canary one profile with serial rollback available.

### Success gate

- No event reordering.
- No duplicate side effects.
- No partial event created after rejected admission.
- Hold continuations remain serviceable under load.
- Tail latency improves under concurrent traffic without degrading single-request latency materially.

## 10. Future Stage 6: Deterministic Validated Tool Plans

### Objective

Evaluate replacing open-ended CrewAI tool selection loops with an application-validated plan while preserving agent expertise and natural-language synthesis.

### Required design work

1. Define a strict tool-plan schema with agent, tool, arguments, dependencies, read-only/idempotent metadata, and expected evidence.
2. Validate every agent, tool, argument, enum, area, event type, and protocol against runtime registries.
3. Keep side effects serial and application-controlled.
4. Ensure viewers cannot plan or discover commander-only tools.
5. Define clarification rather than guessing when required data is missing.
6. Decide whether the plan is generated by the Main Agent or by the relevant specialist; the user must approve this decision.
7. Keep natural answer formulation separate from tool authorization.

### Files expected to change

- `protocols/contracts.py` or the current protocol contract module: validated tool-plan types.
- `orchestrator/reasoning.py`: planning prompt and parser.
- `protocols/executor.py`: deterministic execution.
- `agents/runtime.py`: direct validated tool invocation path while retaining the current path for rollback.
- `auth/permissions.py` and `orchestrator/capabilities.py`: disclosure and execution enforcement.
- `fixtures/` and `tests/`: tool selection, injection, authorization, and side-effect cases.

### Sequential execution

1. Build the schema and offline validator.
2. Run it in shadow beside CrewAI's current loop.
3. Compare selected tools, arguments, provider-call counts, and results.
4. Test disjoint allowlists and prompt injection.
5. Test every side-effecting tool for exactly-once behavior.
6. Ask the user whether to activate the deterministic path for read-only tasks.
7. Consider side-effecting tasks only in a later separately approved rollout.

### Success gate

- No tool outside the validated allowlist executes.
- No unknown or malformed argument reaches a tool.
- No duplicate side effect.
- Read-only routes reduce provider round trips without quality regression.

## 11. Future Stage 7: Independent Specialist and Step Parallelism

### Objective

Parallelize only independent read-only or explicitly idempotent work after the worker pool and deterministic dependency model are proven.

### Required design work

1. Extend or confirm `step_id` and `depends_on` semantics.
2. Build a DAG validator that rejects cycles, unknown dependencies, duplicate IDs, and forbidden parallel side effects.
3. Set separate fan-out and provider-concurrency limits.
4. Preserve output ordering by original plan index.
5. Block dependents after failure without forcibly cancelling a non-idempotent tool already in progress.
6. Define deadline behavior before launching each branch.

### Files expected to change

- protocol contracts and `protocols/executor.py`.
- `orchestrator/flows.py`.
- `agents/runtime.py` provider concurrency controls.
- profile optimization contracts after user-approved values.
- race, cancellation, failure, and ordering tests.

### Sequential execution

1. Validate DAGs without parallel execution.
2. Enable parallelism only for read-only steps.
3. Inject branch failures and deadline exhaustion.
4. Confirm deterministic persisted order.
5. Ask the user before allowing any idempotent write step to run concurrently.

### Success gate

- Deterministic output and persistence order.
- No cross-call tool allowlist leakage.
- No forced cancellation of an active non-idempotent tool.
- Measured p95 improvement for eligible multi-specialist workloads.

## 12. Rollout Order for Deferred Work

Deferred capabilities must be evaluated and rolled out in this order unless the user explicitly changes it:

1. Merged message planner.
2. Combined risk and protocol selection.
3. Insights Agent combined insight and judgment.
4. Runtime stage model policies.
5. Policy-aware worker pool.
6. Deterministic validated tool plans.
7. Independent specialist and DAG parallelism.

Each stage follows this rollout sequence:

1. Offline deterministic tests.
2. Development corpus.
3. Held-out corpus.
4. Shadow mode where applicable.
5. User review and explicit activation decision.
6. Staging.
7. One canary profile.
8. Ten percent traffic.
9. Fifty percent traffic.
10. Full rollout.

Each live step lasts at least one business day or 100 representative requests. A profile change and restart must restore the previous behavior.

## 13. Explicit Exclusions

The following are not part of this future plan:

- word-by-word or token-by-token streaming;
- reintroducing `POST /Msg/Stream`;
- replacing SQLite;
- introducing a message broker;
- weakening commander/viewer authorization;
- using conversation memory as operational truth;
- exposing prompts, chain-of-thought, protocols, sub-agents, or tools to viewers;
- changing assertions or safety rules merely to pass a performance target.

## 14. Definition of Done

This future plan is complete only when every activated stage:

- was separately approved by the user;
- passed its focused tests and the complete offline suite;
- passed its development and held-out evaluation gates;
- has route- and stage-level latency evidence;
- has no high-risk, authorization, disclosure, ordering, or side-effect regression;
- has a documented canary rollout and restart-based rollback;
- has an append-only completion entry in `docs/progress.md`;
- has updated operator documentation;
- leaves every unapproved stage disabled.
