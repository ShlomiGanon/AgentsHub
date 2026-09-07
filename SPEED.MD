# AgentsHub Server Latency Investigation

**Investigation date:** 2026-08-29  
**Repository:** `C:\Projects\AgentsHub`  
**Locally installed runtime:** Python 3.13.14, CrewAI 1.15.17  
**Scope:** source-code review, safe local measurements, inspection of existing deployment traces in `demo_profile.db`, and research against official CrewAI documentation and source. No paid model calls were made for this investigation.

## Executive conclusion

The server is slow primarily because one user request is expanded into several sequential outer agent invocations, while every invocation constructs a new `crewai.LLM` object. Tool-using CrewAI agents then expand one outer invocation into several additional provider round trips of their own.

The active `profiles.demo` configuration uses the legacy, separate, serial modes. Therefore, optimizations already represented by configuration types are not active. Some of the advertised per-stage optimization configuration is not connected to the runtime at all.

The strongest findings, in order, are:

1. **A request causes many sequential provider calls.** A simple conversational response uses two outer model calls. A normal question usually uses four or five provider calls. A full Telegram report can use approximately eleven provider calls, and an observed schema repair increased one real run to twelve.
2. **`crewai.LLM` is rebuilt for every outer call.** Local construction took approximately 1.14-1.17 seconds at the median for both OpenAI and OpenRouter model identifiers, without making a model request. This can add roughly 2.3 seconds to a two-call conversation and roughly 9 seconds to an eight-invocation report.
3. **The first request pays a large lazy-import cost.** Importing CrewAI took 9.6 and 13.2 seconds in two cold-process measurements. `_import_crewai()` is lazy, so the first user request, rather than server startup, pays this cost.
4. **CrewAI tool loops multiply calls.** One observed `reference_agent` execution with two available tools generated four OpenRouter requests inside a single `Agent.process()` call. The logical stage count therefore materially understates actual provider traffic.
5. **The active profile leaves call-reduction features off.** `profiles.demo` selects `legacy` planning, separate risk/selection, separate insight/judgment, disabled structured output, and a serial event queue.
6. **Most legacy calls have no output-token budget, explicit provider timeout, or explicit CrewAI iteration limit.** The application passes an outer `max_execution_time`, but it does not pass `timeout`, `max_retries`, or `max_iter` to the relevant CrewAI objects.
7. **Prompts grow and are repeatedly resent.** Conversation history, protocol descriptions, agent descriptors, history vocabulary, and CrewAI's agent prompt wrapper are sent across multiple stages. The inspected OpenRouter logs reported zero cached prompt tokens.

SQLite, local HTTP, and Python orchestration are not the primary cause for a single request. They can become secondary bottlenecks under concurrency, but the evidence shows that framework setup plus sequential provider traffic dominates current latency.

## Evidence levels

This report uses three evidence labels:

- **Proven:** directly established by current source code, installed package source, or existing real deployment logs.
- **Measured locally:** timed on this machine without a paid model request. These figures are machine- and provider-adapter-specific.
- **Inference:** a likely effect derived from the proven design; it must be confirmed with a controlled live trace before becoming an SLO or rollout decision.

## What a request actually does

The relevant latency equation is:

```text
request latency
  = queue wait
  + sum(outer Agent.process setup)
  + sum(all provider round trips, including CrewAI tool-loop turns)
  + tool time
  + persistence/logging time
  + delivery time
```

The important detail is that one `Agent.process()` is not necessarily one provider request. With tools, CrewAI repeatedly calls the model, executes a selected tool, appends the result, and calls the model again until it produces a final answer or reaches its iteration limit.

### Active profile behavior

`profiles/demo.py:79` sets:

```python
OPTIMIZATION_POLICY = OptimizationPolicy()
```

The defaults in `profiles/contracts.py:40-55` are:

| Setting | Active value | Latency consequence |
|---|---:|---|
| `planner_mode` | `legacy` | Intent classification and question routing remain separate calls. |
| `operational_decision_mode` | `separate` | Risk assessment and protocol selection are separate calls. |
| `final_assessment_mode` | `separate` | Insight generation and success judgment are separate calls. |
| `structured_output_mode` | `off` | Provider-side strict structured output is not used. |
| `event_queue_mode` | `serial` | Only one queued event job executes at a time. |
| `streaming_enabled` | `False` | The user receives no early final-answer tokens. |
| `stage_model_policies` | empty | No stage-specific token, timeout, reasoning, or tier choice is active. |

### Conversational messages

The legacy conversational path is sequential:

1. `classify_intent()` calls the Main Agent (`orchestrator/reasoning.py:470-495`).
2. `answer_conversationally()` calls the Main Agent again (`orchestrator/reasoning.py:530-541`).

Result: normally **two outer model invocations** for a greeting, identity question, or capability question.

An existing deployment trace, `fe2260f1a2a3494f93cda36d89633fc5`, showed:

- two OpenRouter `chat/completions` requests;
- 2,140 reported total tokens across those requests;
- 13.220 seconds between the first persisted CrewAI configuration log and the final local `POST /Msg` response log.

This is not a synthetic estimate. It is a real trace already present in the demo database. It also does not necessarily include time before the first persisted log.

### Questions

The legacy question path performs:

1. intent classification;
2. model-based classification of whether this is the narrow "most recent event" fast path (`orchestrator/reasoning.py:1499-1504`);
3. if it is not that narrow shape, model-based agent/history routing (`orchestrator/reasoning.py:1514-1522`);
4. a History Agent or specialist Agent call;
5. for multiple specialist answers, another Main Agent composition call.

The direct-lookup classifier is itself a model request. It saves a routing call only for the narrow case it recognizes and adds a call to every other question.

Observed real traces:

| Trace | Shape visible in logs | OpenRouter requests | Reported tokens | Persisted span |
|---|---|---:|---:|---:|
| `9fa77b4849614e039f88d666d2499d55` | history question | 4 | 5,078 | at least 15.998 s |
| `885510f86b47474dbf1bca4e4750f827` | current-state specialist question | 5 | 6,642 | at least 15.227 s |

These spans begin at the first persisted provider-response log, not at HTTP request acceptance, so they are lower bounds for full client-observed latency.

The five-call current-state trace is consistent with:

1. intent classification;
2. direct-lookup classification;
3. specialist routing;
4. the specialist selecting and calling `check_status`;
5. the specialist producing its final answer after the tool result.

### Reports and action requests

The normal full sensor path has these outer invocations:

1. extraction;
2. risk assessment;
3. protocol selection;
4. task formulation;
5. one specialist `Agent.process()` per executed step;
6. insight generation;
7. success judgment.

That is **seven outer invocations** for the one-step fixture path. A Telegram report adds intent classification, giving **eight outer invocations**. `docs/cost_latency_review.md` currently says "six" for the fixture table even though its own listed rows sum to seven when specialist execution is included. The later statement that legacy can use seven calls and Telegram eight is the correct outer-call accounting.

Actual provider traffic can be higher because the specialist invocation contains a CrewAI tool loop.

For the successful demo event `11cd89632be8414e939097ad90711de2`:

- the initial report-to-approval-hold trace made five OpenRouter requests in 18.029 seconds of persisted span;
- two of those requests were intent attempts, demonstrating a real schema-repair call;
- after commander approval, the continuation made seven OpenRouter requests in 27.743 seconds;
- the continuation consisted of task formulation, one tool-using specialist execution, insight generation, and judgment;
- the specialist portion expanded into four provider requests;
- processing visible in the two traces totals about 45.8 seconds, excluding the human approval wait and excluding any unlogged time before the first provider-response record;
- the two traces contain twelve provider requests in total.

A similar report without the extra intent repair would have used approximately eleven provider requests in this execution shape.

## Root causes, ranked

### P0 - Sequential provider-call multiplication

**Evidence: proven by source and real traces.**

The orchestration flow decomposes one request into many model judgments, and most of those judgments depend on the prior result. They therefore execute sequentially. Network time and inference time accumulate rather than overlap.

The existing mocked benchmark cannot represent this. The focused offline benchmark completed the full sensor path in 301.0 ms on this run, while the real approved event required about 45.8 seconds of persisted processing span. The mocked result proves that the local business logic is comparatively cheap; it is not a live-provider latency estimate.

The strongest opportunities are to remove unnecessary calls, combine safe decisions after quality gates, and avoid using an agent loop where deterministic application code can execute an already validated plan.

### P0 - A new `crewai.LLM` is constructed on every outer invocation

**Evidence: proven by source and measured locally.**

`agents/runtime.py:218-257` builds `llm_options` and then executes:

```python
llm = crewai_module.LLM(**llm_options)
```

This occurs inside `invoke()`, which is called by every `Agent.process()`. There is no LLM or provider-client cache in the current file. Only the CrewAI import and dynamic tool classes are cached.

Local construction-only measurements, using a dummy key and making no model request, produced:

| Model identifier | Samples | Median | Minimum | Maximum |
|---|---:|---:|---:|---:|
| `openai/gpt-4o-mini` | 6 | 1,169.6 ms | 1,100.0 ms | 1,210.4 ms |
| `openrouter/openai/gpt-4o-mini` | 6 | 1,138.8 ms | 1,097.8 ms | 1,197.2 ms |

A separate 25-sample OpenAI measurement produced a 1,133.3 ms median. In contrast, constructing the CrewAI `Agent` after the LLM existed took a 0.347 ms median.

Inference: on this machine, recreating the LLM can add about 2.3 seconds to a normal conversational response and roughly 9 seconds to an eight-invocation Telegram report. Exact production savings require a controlled before/after trace, because provider adapters and package caches differ.

### P0 - Lazy CrewAI import makes the first request exceptionally slow

**Evidence: proven by source and measured locally.**

`agents/runtime.py:150-166` uses an `@lru_cache`, but `_import_crewai()` is called lazily from `invoke()`. It is not warmed during `build_context()` or application startup.

Two cold-process import measurements took:

- 13.223 seconds;
- 9.597 seconds.

This cost is paid once per server process, but it is paid by the first model-backed request. It also occurs before the adapter can emit its normal `model_invocation_finished` telemetry, so a trace may make the first request look shorter than the user actually experienced.

### P0 - CrewAI tool execution is an iterative model loop

**Evidence: proven by installed source, official source, and real traces.**

`agents/runtime.py:281-299` creates a CrewAI `Agent` and calls `crewai_agent.kickoff(text)`. The runtime does not set `max_iter`.

The installed CrewAI 1.15.17 executor loops until it obtains an `AgentFinish` or reaches `max_iter`. The official CrewAI agent documentation describes `max_iter`, `max_retry_limit`, and `max_execution_time`; its documented `max_iter` default is 20 and `max_retry_limit` default is 2. The installed 1.15.17 wheel on this machine actually defines `max_iter=25`. This documentation/package discrepancy is another reason to set the value explicitly rather than inherit a version-dependent default.

The observed approved event showed four OpenRouter requests during one `reference_agent` execution. This behavior is expected for an agent that must select tools, receive tool results, possibly select another tool, and then formulate a final answer. It means the system's old "one model call per execution step" accounting is not valid at the provider boundary.

Official references:

- [CrewAI 1.15.17 Agent attributes](https://docs.crewai.com/v1.15.17/en/concepts/agents)
- [CrewAI executor source showing the iterative tool loop](https://github.com/crewAIInc/crewAI/blob/main/lib/crewai/src/crewai/agents/crew_agent_executor.py)
- [CrewAI Agent source and retry behavior](https://github.com/crewAIInc/crewAI/blob/main/lib/crewai/src/crewai/agent/core.py)

### P0 - Existing optimization controls are inactive or not wired

**Evidence: proven by source.**

The active demo profile uses all legacy-safe defaults. Therefore:

- the merged message planner is not active;
- risk and protocol selection remain two calls;
- insight and judgment remain two calls;
- strict structured output is off;
- queued event jobs remain serial.

More importantly, `stage_model_policies` is defined and validated in `profiles/contracts.py` and `profiles/loader.py`, but no production caller reads it. Repository-wide call-site inspection found no runtime consumer. It currently cannot change stage model tier, output tokens, timeout, or reasoning effort despite documentation describing it as an available control.

Only four merged/newer call sites construct an `InvocationPolicy` directly in `orchestrator/reasoning.py`. Most active legacy stages call `main_agent.process(..., [])` without one.

### P1 - Legacy calls do not enforce concise output or provider request settings

**Evidence: proven by source.**

`agents/runtime.py` passes `max_tokens` and `reasoning_effort` only when an `InvocationPolicy` is supplied. It never passes the policy timeout to `crewai.LLM(timeout=...)`; instead, it maps the value only to the Agent's `max_execution_time`.

Consequences:

- most active legacy stages have no explicit output-token ceiling;
- the provider request has no application-selected `timeout` or `max_retries` value;
- output verbosity can directly increase completion time;
- a stalled provider call relies on CrewAI's outer timeout implementation rather than a transport-level timeout chosen by AgentsHub.

CrewAI's official LLM documentation explicitly supports `timeout`, token limits, streaming, response formats, reasoning controls, and provider retry limits. It also notes that larger contexts may be slower and that model/provider choice trades accuracy against speed and cost.

Official reference: [CrewAI 1.15.17 LLM configuration](https://docs.crewai.com/v1.15.17/en/concepts/llms).

There is a further timeout caveat in the installed CrewAI source. `_execute_with_timeout()` runs the task in a `ThreadPoolExecutor`, calls `future.result(timeout=...)`, then calls `future.cancel()` on timeout. Because this happens inside a normal executor context manager, leaving the context can still wait for an already-running worker. A provider call that cannot be cancelled may therefore continue to hold the AgentsHub provider semaphore after the nominal timeout. This must be tested with a deliberately stalled transport; the current code does not prove hard cancellation.

The direct `/Msg` path installs a 75-second invocation deadline in a `ContextVar`, but queued event work runs on a different worker thread and does not install that same invocation deadline. Queued jobs have a persisted deadline and check it between stages, while each individual model call still receives the Agent's normal timeout. This prevents some new stages from starting after the job budget is exhausted, but it is not a hard 180-second wall-clock bound on a provider call already in progress.

### P1 - Prompt size and repeated context are material

**Evidence: proven by source and real token logs.**

The system repeatedly serializes dynamic and static context:

- all available protocol names and descriptions during intent and selection;
- conversation history during intent, conversational replies, direct lookup, and routing;
- agent descriptors and history vocabulary during question routing;
- event history views during narrative answers;
- protocol, steps, outcomes, and precedent material during final assessment;
- CrewAI role, goal, backstory, tool schemas, and its own execution instructions around every call.

The demo profile retains six conversation turns. Existing traces show intent prompts growing from approximately 1,084 prompt tokens to approximately 1,953 prompt tokens in a longer conversation. The inspected provider usage records reported `cached_prompt_tokens: 0`.

This does not mean conversation memory should be removed. It means the same reference context should not be resent to multiple independent classification calls when one validated plan can carry the decision forward.

### P1 - Repair and retry layers create long-tail latency

**Evidence: proven by source; one repair observed in a real trace.**

The code contains several independent retry or repair mechanisms:

- intent classification makes up to two attempts (`orchestrator/reasoning.py:476-495`);
- structured calls make one repair attempt (`orchestrator/reasoning.py:342-367`);
- duplicate question routing can add a repair call (`orchestrator/reasoning.py:1524-1545`);
- success judgment is attempted twice (`orchestrator/flows.py:843-855`);
- protocol steps can attempt up to the live `RETRY_COUNT`, which is 3 in the demo profile, with a one-second sleep between attempts (`protocols/executor.py:45-112`);
- an unclear task can invoke the Main Agent to rewrite the task before another specialist attempt;
- CrewAI itself has agent-level error retries and provider-specific retry behavior.

The successful report trace showed two intent provider requests before one intent result, so this is not merely a theoretical branch.

Retries are necessary for some transient failures, but layered retries need one shared deadline and explicit accounting. Otherwise a single malformed response or tool issue can multiply both framework construction time and model latency.

### P1 under load - Serial event queue

**Evidence: proven by source.**

`profiles.demo` selects `event_queue_mode="serial"`. `SerialEventQueue` has one worker and processes jobs one at a time (`orchestrator/event_queue.py:35-89`). A queued report waits for all earlier reports, including all their model turns and retries.

This is not the primary cause of a single request on an idle server. It is a direct cause of rising p95/p99 latency when multiple reports arrive close together. Four workers and reservation/locking logic already exist in `PolicyAwareEventQueue`, but the profile does not enable them.

### P2 - Streaming does not currently reduce time to first token

**Evidence: proven by source.**

Streaming is disabled in the active profile. Even if enabled, `api/routes.py:433-455` first executes:

```python
completed_response = make_response(post_msg())
```

Only after `post_msg()` has completed does the endpoint build SSE events from the finished answer. This is post-completion chunking, not live model streaming. It can change transport shape but cannot improve model time to first token.

CrewAI has real streaming surfaces for LLM, Flow, and Crew execution. AgentsHub is not consuming them in this route. Official reference: [CrewAI streaming model](https://docs.crewai.com/v1.15.17/en/concepts/streaming).

### P2 - Synchronous durable logging adds many small commits

**Evidence: proven by source and measured locally.**

The root logger writes non-telemetry records synchronously into SQLite through `_PersistenceLogHandler` (`tools/observability.py:399-431`). `SQLitePersistence._submit_write()` blocks on `future.result()`, and each log entry commits its own transaction (`persistence/sqlite_store.py:289-300`, `610-625`). Third-party `httpx` and provider usage INFO logs are also captured.

A 200-write temporary-database measurement produced:

- median commit: 1.509 ms;
- p95: 2.153 ms;
- maximum: 18.417 ms.

This is small compared with seconds of LLM time, so it is not the leading single-request problem. Under concurrent traffic, however, business updates and log writes share one SQLite writer and can create contention. Batching or asynchronously draining non-critical logs could reduce that secondary load.

### P2 - Development server and long-poll thread usage

**Evidence: proven by source, not observed as the current dominant cause.**

`python -m api.app profiles.demo` defaults to Flask's development server. Waitress is used only with `--server waitress`; it defaults to 16 threads (`api/app.py:183-212`). Long-poll notification requests hold an HTTP worker while waiting.

This can limit production concurrency when thread capacity is too small. It does not explain the measured 13-16 second latency of a single local question: those traces are dominated by provider calls and per-call LLM construction.

### P2 - CrewAI is not version-pinned

**Evidence: proven by configuration.**

`requirements.txt` declares `crewai>=1.15` while its comment records 1.15.17 as the verified installation. A fresh installation can therefore resolve to a newer version with different defaults, provider initialization cost, executor behavior, or streaming semantics. The documented-versus-installed `max_iter` discrepancy already demonstrates why inherited defaults are not a stable performance contract. Pinning and deliberately upgrading CrewAI will make latency measurements reproducible.

## Components that are not the main current cause

The investigation found several areas that are already reasonably optimized:

- The bot uses a lifecycle-managed `httpx.AsyncClient`, explicit connect/pool/write/read timeouts, and GET-only retries (`bot/transports.py:47-120`).
- Notification long polling waits on a condition signalled after commit; the normal 20-second wait is intentional and is not added to a message's model execution time.
- SQLite uses WAL, one writer, thread-local read connections, and batched step attachment for multi-event reads.
- CrewAI import and tool classes are cached after first use.
- Tool allowlists use a `ContextVar`, so caching a safe provider client would not require caching authorization state inside the client.
- The CrewAI `Agent` object itself is cheap to construct in the local measurement. The expensive construction was `crewai.LLM`.
- The focused mocked full-path benchmark completed in 301.0 ms. This confirms that local control flow is not responsible for tens of seconds on an idle machine.

## Observability gaps

The application already emits useful `model_invocation_finished` fields, including model, provider, stage, token usage, setup sub-timings, and kickoff time (`agents/runtime.py:337-359`). However, that record is marked `telemetry_only`, and `_PersistenceLogHandler` deliberately skips telemetry-only records.

Therefore the demo database does not durably retain the clean per-stage model measurements. The analysis above had to infer provider counts from third-party `HTTP Request` logs and usage records. Those logs do not reliably contain the configured model name, request start time, TTFT, or a complete usage record for every turn inside a tool loop.

The next controlled live measurement should record, per trace and per actual provider request:

- route and stage;
- outer invocation number;
- inner CrewAI iteration number;
- model and provider;
- LLM-construction time;
- provider queue/semaphore wait;
- request start, TTFT, and completion time;
- prompt, completion, reasoning, and cached tokens;
- tool start/end and tool name;
- repair/retry reason;
- queue wait and end-to-client delivery time.

CrewAI's own observability guidance recommends tracking execution time, token usage, API latency, success rate, and cost. Official reference: [CrewAI observability overview](https://docs.crewai.com/v1.15.17/en/observability/overview).

## Recommended remediation order

These are recommendations, not changes made by this investigation.

### 1. Cache and warm the framework runtime

- Import CrewAI during server startup, not on the first request.
- Construct reusable, thread-safe LLM/provider clients once per model/configuration key.
- If response format, token budget, or reasoning settings make an LLM object immutable, cache a bounded set keyed by those safe settings rather than rebuilding it every time.
- Never include an API-key value in logs or a human-readable cache key.
- Measure cold startup, first request, and warm request separately.

Expected impact on this machine: roughly 10 seconds removed from the first request and approximately 1.1 seconds removed per outer invocation, subject to controlled confirmation.

### 2. Count actual provider requests, not only outer stages

- Instrument each CrewAI/LiteLLM provider request and agent iteration.
- Update `docs/cost_latency_review.md`; its current fixture total is arithmetically inconsistent and its one-call-per-step abstraction misses tool-loop turns.
- Define latency budgets in provider round trips and tokens, not only `Agent.process()` calls.

### 3. Bound CrewAI autonomy explicitly

- Set `max_iter` explicitly per agent type.
- Give no-tool classification/formulation calls the smallest safe iteration limit.
- Give tool agents only enough iterations for the protocol's validated tool plan and final response.
- Set `max_retry_limit`, provider `max_retries`, and provider `timeout` explicitly.
- Verify stalled-request cancellation; do not assume `max_execution_time` stops an in-flight HTTP request.

Quality, tool completion, and side-effect safety tests must determine the exact limits. They should not be guessed from latency alone.

### 4. Make stage policies real

- Connect `OptimizationPolicy.stage_model_policies` to every invocation.
- Apply output-token, timeout, reasoning, and model-tier policies to legacy and merged stages.
- Use fast models for validated routine classification/formatting stages only after held-out evaluation.
- Reserve stronger models for ambiguity, high risk, synthesis, and policy-sensitive judgment.

CrewAI's official selection guide recommends task-specific model choice and notes that efficient models fit routine processing and function-calling workloads, while reasoning models trade speed and cost for capability. Official reference: [CrewAI Strategic LLM Selection Guide](https://docs.crewai.com/v1.15.18/en/learn/llm-selection-guide).

### 5. Reduce sequential decision calls behind existing quality gates

- Move from legacy to merged message planning only after the existing authorization, safety, and held-out gates pass.
- Evaluate merged risk/protocol selection.
- Evaluate merged low-risk final assessment.
- Replace the model-based direct-lookup pre-classifier with either a strictly measured deterministic shape or one unified question-routing decision.
- Do not use shadow mode when measuring user latency; it intentionally performs both paths.

### 6. Reduce tool-loop round trips

- Prefer deterministic execution of a validated structured protocol plan when application code already knows the allowed tool sequence and parameters.
- If CrewAI remains responsible for tool selection, use native function calling, explicit iteration limits, and a task shape that permits the minimum required turns.
- Keep side-effect serialization, idempotency, authorization, and approval rules unchanged.

This is likely the largest remaining provider-call reduction for action protocols because one logical specialist step currently creates several model requests.

### 7. Reduce repeated context and enable usable caching

- Keep stable instructions, schemas, agent descriptors, and tool definitions in a stable prefix.
- Send only the conversation turns needed to resolve the current reference.
- Avoid giving the same protocol and history vocabulary to several sequential classifiers.
- Measure provider prompt-cache hits. The inspected traces had none.
- Preserve the rule that conversation memory is not an authority for operational facts.

### 8. Enable concurrency only after single-request waste is reduced

- Enable the policy-aware queue after its safety/load gates pass.
- Parallelize only independent read-only specialist tasks.
- Keep sender ordering and side-effect locks.
- Treat provider concurrency as a capacity control, not a single-request speed fix.

Increasing workers before reducing per-job provider traffic can increase rate limits and tail latency.

### 9. Implement true final-answer streaming for perceived latency

- Consume CrewAI/provider streaming events while generation is happening.
- Stream only validated final-answer text, never decisions, tool arguments, or hidden reasoning.
- Keep in mind that streaming improves time to first visible token, not final completion time.

Official CrewAI async guidance recommends native async execution for high-concurrency workloads; thread-based async only moves synchronous work to a thread. Official reference: [CrewAI asynchronous kickoff](https://docs.crewai.com/v1.15.17/en/learn/kickoff-async).

## Verification plan

Before changing production defaults, run a controlled live matrix with at least 100 representative examples per major route:

1. conversational identity/capability answer;
2. current-state question with one tool;
3. history count/aggregate answer without narrative generation;
4. history narrative answer;
5. low-risk one-step report;
6. high-risk approval and continuation;
7. missing-event-data wait and resume;
8. malformed structured response requiring repair;
9. tool failure and retry;
10. concurrent queued reports from several senders.

For each, compare:

- cold and warm p50/p95/p99;
- actual provider-request count;
- LLM-construction time;
- input/output/cache tokens;
- queue wait;
- first-token and final-response time;
- quality, authorization, side-effect, and safety outcomes.

Suggested experiment order:

1. baseline current demo policy;
2. warm import only;
3. cached LLM/provider object only;
4. explicit iteration/token/timeout limits;
5. merged planner;
6. merged operational decision;
7. low-risk merged final assessment;
8. policy-aware queue under load;
9. true streaming for eligible final answers.

Change one factor at a time before combining them. This will identify whether a gain comes from fewer calls, faster setup, a faster model, shorter output, or concurrency.

## Limitations

- No new paid model calls were made. The real-provider evidence comes from pre-existing deployment logs in the demo database.
- Persisted trace spans are incomplete lower bounds unless the first record coincides with request acceptance. Clean `stage_finished` and `model_invocation_finished` telemetry is not stored in SQLite.
- The configured OpenRouter model name was not present in durable logs and the relevant model environment variables were not set in this investigation shell. This report cannot attribute inference latency to one exact model.
- Local import and constructor measurements apply to this Windows/Python/CrewAI installation. Production must be measured separately.
- The mocked 301.0 ms benchmark is useful for structural comparison only. It excludes live provider inference and does not exercise the full internal CrewAI tool loop in the same way as a real model.
- Official CrewAI documentation currently describes a default `max_iter` of 20, while the installed 1.15.17 source defines 25. The report relies on the installed source for local behavior and recommends explicit configuration to remove this ambiguity.

## Final assessment

The server is not slow because Flask needs several seconds, because long polling adds 20 seconds to every answer, or because SQLite requires seconds per transaction. The evidence points to a request-amplification problem:

```text
many sequential orchestration decisions
  x approximately 1.1 s repeated LLM construction per outer call
  + provider inference/network time
  + extra CrewAI tool-loop turns
  + occasional repair/retry turns
```

The first request also carries an additional approximately 10-13 second lazy CrewAI import penalty. Under load, the serial event queue adds head-of-line blocking on top of those per-job costs.

The highest-confidence first fixes are framework warm-up, safe LLM/provider reuse, exact provider-call telemetry, and explicit iteration/token/timeout controls. The highest-potential structural fixes are reducing legacy decision calls and replacing unnecessary agentic tool loops with validated deterministic execution, subject to the project's existing quality and safety gates.
