# Cost and latency review (work_plan.md §9.20)

A one-time review, not a standing report — reproducible by running
`pytest tests/test_integration_cost_and_latency_review.py -s`, which is
the real instrumentation behind every number below (a wrapper counting
calls to the main agent by matching each prompt's own distinguishing
text, not a hand-typed estimate). Re-run it after any change to the
decision chain's shape to re-check these figures rather than trusting
them as fixed.

## Model calls per event, by stage

Measured for a sensor-sourced event (`POST /Event`) against
`fixtures.profiles.minimal_profile`'s single-agent, single-step
protocols, with every model call mocked at the crewai boundary (so these
are exact call *counts*, not latencies — see the latency section below
for that).

| Stage | Full run | Closed on precedent |
|---|---|---|
| intent | 0† | 0† |
| extraction | 1 | 1 |
| risk assessment | 1 | 1 |
| protocol selection | 1 | 1 |
| precedent lookup | 0‡ | 0‡ |
| task formulation | 1 | 0 |
| execution (per participating agent, per step) | 1§ | 0 |
| insights | 1 | 0 |
| judgment | 1 | 0 |
| **total** | **6** | **3** |

† `POST /Event` (sensor path) never runs intent classification at all —
that stage only exists on the `POST /Msg` (Telegram) path, per §7.5's
own split. A Telegram-sourced report adds exactly one more call here.

‡ `orchestrator.precedent.look_up_precedent`/`history.query
.HistoryQueryService.search_precedents` reads persistence directly — no
model call of its own.

§ One call per participating agent per step; `status_check`/
`dispatch_response` are both single-agent, single-step protocols in the
fixture profile, so this is 1. A multi-agent protocol would add one call
per additional agent, only on the full-run path — closure skips
execution entirely.

## Precedent closure's savings

Closure skips task formulation, execution, insights, and judgment
entirely: **2 fewer main-agent calls (a 40% reduction) plus the one
execution call and the one insights-agent call it also avoids** — the
insights agent is a separate agent object from the main agent, so its
saved call doesn't show in the "main-agent calls" count above but is
real. Precedent lookup itself is the one stage that runs identically on
both paths and costs nothing in model calls to check.

## Wall-clock latency

With every model call mocked (no real network or inference time), one
full run measured ~1.7 seconds submission-to-outcome-written in this
review's own test environment — dominated by real work this system
already does (serialized SQLite writes, `time.sleep`-based retry backoff
where triggered, Flask/werkzeug request handling), not by anything
resembling real model latency. This number's value is structural, not
absolute: with real models, wall-clock time will be dominated by
inference time per call, and the *call count* table above is what scales
that estimate — six real model calls at whatever the deployed model's
own p50 latency is, roughly in sequence (this system's flow does not
parallelize independent calls today), is the honest estimate to build
from, not this review's own mocked-model number.

## Calls that could be merged or reused

`orchestrator.precedent.look_up_precedent` and `orchestrator.insights
.build_insight` each read comparable history independently (both via
`HistoryQueryService`) for the same event — confirmed by reading both
call sites directly, not assumed. They cover overlapping ground: both
are asking "what does the historical record say about events like this
one." Merging them into one shared read the Insights Agent's own
comparison could reuse afterward is the clearest remaining reduction
this review found. Not implemented here — it is a genuine change to
`orchestrator/flows.py`'s own call ordering and data-passing shape
(precedent lookup happens *before* the approval-hold check, well before
task formulation/execution/insights run; sharing its result forward
means either widening `FlowResult`/the flow's own internal state to carry
it, or restructuring insights to accept an already-fetched history
argument), which is real implementation work belonging to its own,
separately-approved pass — out of scope for a review subtask.

**Correction (2026-08-27):** the paragraph above is stale — this merge has
since been implemented. `orchestrator/flows.py::_run_protocol` now passes
the same `precedent_matches` tuple `continue_from_risk_assessment` already
computed straight into `build_insight(..., comparable_history=precedent_matches)`
— confirmed by direct reading, not assumed. There is only one history read
per event today, not two; the reduction this section proposed is real.
