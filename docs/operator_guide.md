# Operator Guide (work_plan.md §9.22)

For a commander or deployment operator running a live instance of this
system. This document doesn't restate what other docs already cover in
full — it's the entry point that tells you where to go, plus the pieces
that don't live anywhere else yet. `docs/PRODUCTION_READY.md` covers production
hardening (secrets, TLS, backups, monitoring); this document assumes a
single localhost deployment, per `work_plan.md` §9.21's own scope.

## Writing a profile from scratch

A profile is a plain Python module — see `docs/profile_spec.md` for the
complete, authoritative list of every name the loader expects
(`AGENTS`, `PROTOCOLS`, `EVENT_TYPES`, `AREAS`, `DB_PATH`, `API_PORT`,
`RETRY_COUNT`, `RISK_THRESHOLD`, `LOOKBACK_WINDOW_DAYS`, `BOT_TOKEN_ENV`,
`MODEL_CREDENTIAL_ENVS`) and exactly what each one means. `profiles/demo.py`
is a complete, working example to copy from. Two rules worth stating
plainly here since they're easy to get wrong:

- A profile file holds no secret values — only the *names* of the
  environment variables that hold them (`BOT_TOKEN_ENV`,
  `MODEL_CREDENTIAL_ENVS`). It's meant to be safe to commit and to send
  to another team in full.
- `DB_PATH` and `API_PORT` must be unique per deployment — two profiles
  sharing either will collide the moment both run at once.

## Adding an agent

See `docs/agent_authoring.md` for the full walkthrough — `agents/standard_agents.py`
(`ReferenceAgent`) is the working example it points at, a complete,
minimal agent with one read-only tool and one side-effecting one. Add the
new agent's class to your profile's `AGENTS` list, constructed with
whichever model that deployment should use for it; nothing else needs to
change for the agent to be loadable.

## Adding a protocol, and what the approval flag does

Every protocol in `PROTOCOLS` needs: a name, a description written for
the Main Agent to select by (not a human), which agents participate,
which of their tools are approved, what a successful run's output looks
like, a criticality level (`LOW`/`MEDIUM`/`HIGH` — breaks ties between
equally-good protocol matches, nothing else), and an **approval flag**.

The approval flag is the only thing in this system that stops a
protocol from running. `approval_flag=True` means: even when the Main
Agent is confident this is the right protocol for the event, a
commander must explicitly approve it (via `/Approve` in the bot) before
it executes — the run holds and waits. `approval_flag=False` means it
runs the moment it's selected, no human in the loop. There is no default
— every protocol must set this explicitly, and the loader refuses to
start otherwise (`docs/profile_spec.md`'s own failure-behavior section).
A commander's own request bypasses this flag for that one request (their
authority already answers the question the flag asks); it never bypasses
a clarification or ambiguous-selection hold, which ask a different kind
of question entirely.

Protocol edits made through the bot (`/profile add`/`edit`/`remove`)
write to the profile *file* on disk — the running system never changes
mid-run. `/profile diff` reports whether the file now differs from what's
running; the edit takes effect on the next restart.

## Adding a user

The only way to add, change, or remove a user is `cli.user_admin`, run
from the shell on the machine hosting the deployment — never through the
API or the bot:

```
python -m cli.user_admin --profile <profile_module> add --telegram-id <id> --level viewer|commander
python -m cli.user_admin --profile <profile_module> update --telegram-id <id> --level viewer|commander
python -m cli.user_admin --profile <profile_module> remove --telegram-id <id>
python -m cli.user_admin --profile <profile_module> list
```

A fresh deployment has no users at all — the first commander is added
this same way, against an empty database; there is no separate
bootstrap step. `<id>` is the person's own Telegram identity (the numeric
ID or handle their account resolves to).

## The bot's own service identity (required — the deployment will not work without this)

Beyond the human users above, **one more identity must be registered
before the bot can do anything**: its own service identity,
`bot.api_client.BOT_SERVICE_IDENTITY` (currently the literal string
`"bot-service"`). The bot makes real HTTP calls to the API on every
interaction — for the calls that aren't tied to a specific person asking
(polling for notifications, reading the commander roster, reading the
profile/settings/job status), it authenticates as this identity, at
**commander** level:

```
python -m cli.user_admin --profile <profile_module> add --telegram-id bot-service --level commander
```

Skip this step and the deployment will *appear* to start correctly —
the bot connects to Telegram, the API serves — and then every single
bot-to-API call fails authentication the moment anyone uses it. This is
easy to miss because nothing about startup itself fails; do this at the
same time you add the first human commander, not as an afterthought. See
`docs/api_spec.md`'s "Service identity" section for the full reasoning,
including exactly which calls use this identity versus the real caller's
own.

## The three things that arrive unprompted

Three kinds of message reach a commander's chat without them asking for
anything, distinct from a reply to something they submitted:

- **`[CLARIFICATION NEEDED — please reply]`** — an incoming report didn't
  match any known event type. Pick one of the offered buttons; the
  system resumes processing that event from where it stopped. If another
  commander already answered it, you'll be told so and by whom, rather
  than having your answer silently accepted a second time.
- **`[APPROVAL NEEDED — please reply]`** — either a flagged protocol is
  about to run and needs a yes/no, or two protocols matched equally well
  and need you to pick which. Same "already answered" protection as
  above applies.
- **`[NOTICE — closed on precedent — no reply needed]`** /
  **`[NOTICE — uncertain verdict — no reply needed]`** — purely
  informational. A closure notice names the event and the precedent it
  matched; an uncertain-verdict notice means a run finished but the
  Insights Agent's own judgment wasn't confident either way. Neither is a
  question — there's nothing to answer.

A delivered job result (`[RESULT]`, or `[RUN FAILED]`/`[DECLINED]` for
the other two outcomes) is different from all three of the above: it's
the answer to something *you* submitted, not an unprompted push, even
though it also arrives without you asking again.

## Changing the three live settings

`/settings view` shows the current retry count, risk threshold, and
precedent lookback window (in days). `/settings set <field> <value>`
changes one — commander level only. All three take effect **immediately**,
on the very next event, and are saved to disk at once — the opposite of
a profile edit, worth remembering since a commander using both commands
will otherwise not know which is which. Every other profile value (which
agents exist, which protocols, event types, areas, ports, paths) needs a
restart; these three specifically never do.

## Reading the run logs

Every log record is one JSON object per line (`tools/observability.py`),
carrying `timestamp`, `level`, `logger`, `message`, `profile_name`, and
`trace_id`. One trace ID is generated the moment an event or message
enters the system (`POST /Event`, `POST /Msg`, or a hold's resumption via
`POST /Approve`/`POST /Clarify` — each of these gets its own fresh ID,
not a continuation of the original event's) and is attached to every
record produced while handling it, all the way through extraction, every
agent call, every tool call, and the final write. The same full-detail
record also lands in the deployment's own database (`log_entries`), and
a short, human-readable one-line summary of it prints to the console
separately — see below for how to see only that last one.

**Console output — two streams, on by default.** The full JSON stream
above prints to stdout; a condensed, human-readable line per record
(`[HH:MM:SS] LEVEL  <trace_id[:8]>  <summary>`) prints separately to
stderr. In a normal terminal both are visible and interleaved. Set
**`LOG_CONSOLE_JSON=false`** (or `0`) in the process environment before
starting the API or the bot to stop the JSON stream from printing to the
console, leaving only the human-readable lines — useful for interactive,
manual testing. This affects the console only: the JSON formatter and
the database log sink are both completely unaffected either way, so
nothing about what's recorded or queryable ever changes, only what
prints to the terminal. Unset, empty, or any value other than an
explicit `false`/`0` means the JSON stream stays on (today's default).

**Always on, at INFO** — what the system decided about an event and why.
This is on in normal operation, with no configuration needed: `intent_classified`
(message ingestion only), `extraction_result` (naming which fields came
back empty), `hold_created` (clarification or approval, with which),
`risk_assessed`, `protocol_selection`, `precedent_closure` (whether a
match closed the event), `step_start`/`step_result` (one pair per step,
with the task text and the result), `tool_blocked`, `step_retry` (and its
cause), `insight_generated`, `final_verdict`, and `event_outcome` on
every branch a run can end on (closed on precedent, declined, failed,
succeeded, uncertain) — so a run can be reassembled by querying rather
than reading. To follow one event from start to finish: find any log
line mentioning the event (most of the events above carry `event_id`
directly; a `step_start`/`step_result` record carries the agent and step
instead, but shares the same run's `trace_id`), read its `trace_id`, then
filter the whole log stream to that one value — every record with it
belongs to that one run, in order, regardless of which stage or agent
produced it.

**Off by default, behind `DEBUG_VERBOSE_LOGGING`** — internal detail
that's noise in normal operation but useful when diagnosing a live run,
most valuably the first time this runs against a real model rather than
a mock (a parse failure or an unexpected response shape is the likeliest
problem, and the INFO log alone can't show what produced it):

- `model_io` — the **full prompt sent to the model and the full raw
  response received, before any parsing**, for every single model call,
  tagged with which agent and which stage (`intent_classification`,
  `extraction`, `risk_assessment`, `protocol_selection`,
  `task_formulation`, `task_rewrite`, `step_execution`,
  `insight_generation`, `success_judgment`, `question_routing`,
  `question_subagent`, `question_history_query`,
  `question_composition`) it belongs to, alongside the same `trace_id` as
  everything else in that run.
- Successful (non-blocked) individual tool calls (`tool_call`) —
  `tool_blocked` stays at INFO above; this is only the ordinary,
  permitted case.
- The precedent search's own internal detail (`precedent_lookup`: the
  exact window boundaries searched, the raw candidate list) — the
  outcome an operator actually needs ("did anything match, did it close
  the event") is `precedent_closure`, at INFO, above.

**Set `DEBUG_VERBOSE_LOGGING=true` in the process environment before
starting the API or the bot** to turn this on (`false`, `0`, unset, or
any other value is off — an explicit falsy value is never mistaken for
"set"; read once at startup, not reconfigurable while running). **Its
output is sensitive** — `model_io` records can contain the full original
event or message text verbatim, prompt structure, and model output. This
is a diagnostic mode for investigating a specific problem, not something
to leave on in normal operation or route to a shared log collector
without the same care given to the raw event text itself.
