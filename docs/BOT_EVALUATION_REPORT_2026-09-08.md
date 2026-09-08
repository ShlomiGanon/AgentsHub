# AgentsHub Bot Evaluation Report

Date: 2026-09-08  
Profile: `profiles.unified_test`  
Mode: isolated automated evaluation; no production Telegram actions and no fixes applied during this audit.

## Executive summary

The repository has broad deterministic coverage and the core stateful workflows are substantially healthier than the live conversational examples alone suggest. The complete suite produced **1,189 passed / 4 failed** (99.66% pass rate). A focused suite covering agents, orchestration, permissions, approvals, context, and load produced **217 passed / 1 failed**. A separate restart/notification/idempotency/E2E integration suite produced **165 passed / 0 failed**.

Release recommendation: **not yet a clean release candidate**. One functional History fallback failure remains, and three repository quality gates fail. The exact natural-language conversation matrix and the full “golden test” are not yet represented as stable automated sequence tests, so the high pass rate must not be interpreted as proof that every listed Hebrew prompt works with the live model.

## Runs performed

| Run | Scope | Result | Duration |
|---|---|---:|---:|
| Full pytest suite | Entire repository | 1,189 PASS / 4 FAIL | 117.70s |
| Agent/orchestrator focus | Surveillance, TeamStatus, History, FriendlyForces, routing, context, RBAC, approvals, load | 217 PASS / 1 FAIL | 9.72s |
| Integration focus | E2E flow, restart/hold recovery, retries, notifications, jobs, idempotency, persistence | 165 PASS / 0 FAIL | 28.05s |

All automated runs used temporary test state and dummy model-tier credentials. They did not dispatch real resources or write through the live Telegram bot.

## Failures

### F-01 — History failure fallback does not satisfy the answer contract

- Severity: Medium
- Test: `tests/test_question_answering.py::test_history_query_error_does_not_crash_the_whole_answer`
- Expected: a stable “no usable answer” fallback when the History service raises an error.
- Actual: `I don't have a way to answer that. history_agent doesn't have a way to help with this question.`
- Impact: History outages do not crash the request, but the response is generic, in English, and loses the expected failure semantics. This is directly relevant to prompts such as “מה קרה אתמול?” and “היו אירועים דומים?”.

### F-02 — Package architecture boundary violation

- Severity: Medium (maintainability)
- Test: `tests/test_architecture.py::test_no_cross_package_imports_outside_entry_points`
- Actual: `api/routes.py` imports `orchestrator.reasoning` directly; only `orchestrator.flows` is declared as an allowed entry point.
- Impact: API and orchestration internals are more tightly coupled than the repository contract permits.

### F-03 — File catalog is stale

- Severity: Low
- Test: `tests/test_file_catalog.py::test_file_catalog_is_complete_and_has_no_stale_paths`
- Missing catalog entries: `run_stack.py`, `benchmark_optimized.json`, `benchmark_baseline.json`.
- Impact: repository inventory/documentation does not describe every tracked artifact.

### F-04 — Hebrew literals outside the message catalog

- Severity: Medium (localization/consistency)
- Test: `tests/test_hebrew_leakage.py::test_no_hebrew_literal_outside_the_message_catalog`
- Offenders include four source files, with `api/routes.py` among them.
- Impact: some user-visible Hebrew cannot be centrally translated, reviewed, or kept consistent.

## Capability assessment

| Area | Evidence from automated tests | Status |
|---|---|---|
| Surveillance tools/state | Camera lookup, fleet state, dispatch, active missions, recall-one/all, invalid state | PASS |
| TeamStatus tools/state | Roster, availability, non-availability reasons, pending members, attendance and identity binding | PASS |
| History normal queries | Structured lookup, filtering, precedent and ownership scoping | PASS |
| History provider/error fallback | Explicit error-path assertion | FAIL (F-01) |
| Friendly Forces | Tool metadata, dispatch interfaces and permission boundaries | PASS |
| Multi-agent composition | Multiple agents composed into one answer; parallel result isolation | PASS at deterministic layer |
| Read-only vs side effect | Side-effecting tools excluded from question paths | PASS |
| RBAC | Viewer dispatch/recall/forces blocked server-side; identity spoofing rejected | PASS |
| Approval | Commander-only decisions, duplicate/conflicting answers, invalid choices, restart persistence | PASS |
| Hallucination resistance | Unknown roster member/placeholder and structured-history absence paths | PARTIAL PASS |
| Context | Conversation context reaches routing; explicit event reply targets are isolated | PARTIAL PASS |
| Restart/idempotency | Hold restart, retries, duplicate outcomes and persistence | PASS |
| Concurrency/load | Serial processing and parallel specialist isolation | PASS in test conditions |

## Coverage against the proposed prompt matrix

### Strongly covered

- Surveillance state is enforced by tools rather than free-form model claims.
- Team roster names and reasons originate from persistence; placeholder names are not invented.
- Viewer cannot gain commander rights through wording such as a direct operational command.
- Questions receive read-only tools even when phrased with action-like language.
- Multiple agent outputs can be synthesized into one response.
- Approval holds survive restart and cannot be successfully resolved twice.
- A new unrelated message does not automatically fill an old event-data hold.
- History ownership filtering prevents a Viewer from reading another reporter’s events.

### Partially covered

- Pronouns such as “אותו”, “שלו”, “שם”: context transport is tested, but the complete five-turn Hebrew drone sequence is not a single regression test.
- “תן לי את השמות” after TeamStatus context: roster views are tested, but every proposed paraphrase is not parameterized.
- Unknown Drone/Camera/Event identifiers: individual persistence/tool behavior is covered, but all requested Hebrew prompts are not tested end-to-end through routing.
- Multi-agent requests: composition mechanics are tested; exact agent-selection assertions for each proposed Hebrew sentence are incomplete.
- Recommendation flow: read-only tool restriction is covered, but “מה אתה ממליץ?” following the golden prompt is not a complete sequence test.

### Not proven by the current automated suite

- The exact golden three-turn conversation:
  1. full read-only multi-agent picture,
  2. recommendation without action,
  3. “שלח את הרחפן המתאים” followed by approval and exactly one dispatch.
- Every listed Hebrew prompt executed sequentially against the configured live LLM.
- A quantitative production latency SLA under real provider latency.
- Hundreds of simultaneous Telegram users or horizontal multi-instance operation.

These items are marked as coverage gaps rather than PASS or FAIL. Running them against the live deployment would send Telegram notifications and could mutate operational test data; that was intentionally excluded from this audit.

## Observability findings

- The complete run emitted repeated logging errors: `ValueError: I/O operation on closed file` while handling a History error test.
- These logging errors did not add a pytest failure, but they can obscure the original exception and should be treated as a reliability finding.
- Model-provider deprecation warnings were also emitted by CrewAI integration tests; they are non-blocking today but indicate future upgrade work.

## Risk-ranked conclusion

1. **Functional risk:** History degradation returns the wrong fallback contract and language.
2. **Evaluation risk:** exact Hebrew multi-turn and golden scenarios are not fully automated, so routing regressions may escape despite a 99%+ suite pass rate.
3. **Operational risk:** real-provider latency and high concurrent Telegram load remain unproven.
4. **Maintainability risk:** architecture and localization quality gates currently fail.
5. **Documentation risk:** the repository file catalog is incomplete.

No production behavior was changed as part of this audit. The next recommended activity is to convert the supplied prompt matrix into data-driven, isolated conversation tests with explicit expected agents, forbidden tools, state diffs, and answer assertions before fixing the reported failures.
