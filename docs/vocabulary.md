# Domain Vocabulary

The reference every later task builds against, per work_plan.md §1.2. When a
later task and this document disagree, this document is wrong and must be
corrected first — not worked around.

Field types use plain Python-ish names (`str`, `int`, `float`, `bool`,
`datetime`, `list[...]`). "Optional" means the field may be empty/`None`;
every other field is required.

## Profile

What the system *is* on a given run. A Python module, named as a launch
argument.

| Field | Type | Optional |
|---|---|---|
| `agents` | `list[Agent]` (constructed instances) | no |
| `protocols` | `list[Protocol]` | no |
| `event_types` | `list[str]` | no |
| `areas` | `list[str]` | no |
| `db_path` | `str` | no |
| `api_port` | `int` | no |
| `retry_count` | `int` (starting value only) | no |
| `risk_threshold` | `float` (starting value only) | no |
| `lookback_window_days` | `int` (starting value only) | no |
| `bot_token_env` | `str` (env var *name*, never the value) | no |
| `model_credential_envs` | `list[str]` (env var *names*) | no |

## Event

One operational occurrence, sensor- or Telegram-originated. See
`persistence/schema.py`'s `EVENTS_TABLE_DDL`/`EVENT_STEPS_TABLE_DDL` for
the authoritative column-level shape (work_plan.md §2.3, implemented in
Mission 2); this entry defines the concept, not the exact column names —
where the two differ in a nullability/naming detail, `persistence/schema.py`
is correct (e.g. `occurred_at` is nullable there, unlike the table below,
because §6.11 writes a Telegram-sourced event before extraction determines
it).

| Field | Type | Optional |
|---|---|---|
| `event_id` | `str` | no |
| `received_at` | `datetime` | no |
| `occurred_at` | `datetime` | no |
| `occurred_at_is_fallback` | `bool` | no |
| `source` | `str` (`"sensor"` \| `"telegram"`) | no |
| `sender_identity` | `str` | no |
| `raw_text` | `str` | no |
| `classification` | `str` | yes — empty is what a clarification hold resolves |
| `area` | `str` | yes |
| `entities` | `list[str]` | yes |
| `description` | `str` | yes |
| `severity` | `str` | yes |
| `risk_level` | `str` | yes — set at risk assessment |
| `risk_reason` | `str` | yes |
| `selected_protocol` | `str` | yes |
| `protocol_reason` | `str` | yes |
| `clarification_hold` | see **Hold states** below | — |
| `approval_hold` | see **Hold states** below | — |
| `precedent_result` | `list[Precedent]` | yes |
| `steps` | `list[Step]` (with results) | yes |
| `insight` | `str` | yes |
| `outcome` | `str` (`succeeded` \| `failed` \| `uncertain` \| `closed_on_precedent` \| `declined`) | yes until the run ends |

## Message

Free text a person sends through Telegram, before intent classification
resolves it into a question, a report, or a request.

| Field | Type | Optional |
|---|---|---|
| `text` | `str` | no |
| `sender_identity` | `str` | no |
| `received_at` | `datetime` | no |

## Protocol

A named playbook. Not keyed to event types — chosen by the Main Agent
reading descriptions.

| Field | Type | Optional |
|---|---|---|
| `name` | `str` | no |
| `description` | `str` | no — must state both when it applies and when it doesn't |
| `participating_agents` | `list[str]` (agent names) | no |
| `approved_tools` | `list[str]` (tool names) | no |
| `expected_success_output` | `str` | no |
| `criticality` | `str`/`int` | no — breaks ties between candidates only |
| `approval_flag` | `bool` | no — must be explicitly set, never defaulted |

## Agent

A specialist, constructed by a profile (or, for the three core agents, by
the base configuration).

| Field | Type | Optional |
|---|---|---|
| `name` | `str` | no |
| `role` | `str` | no |
| `system_prompt` | `str` | no |
| `model` | `str` | no |
| `tools` | `list[Tool]` | yes (may own zero tools) |

## Tool

One capability an agent exposes.

| Field | Type | Optional |
|---|---|---|
| `name` | `str` | no |
| `description` | `str` | no — written for a model to act on |
| `side_effecting` | `bool` | no — never defaulted |
| `idempotent` | `bool` | required *only if* `side_effecting` is `True`; meaningless otherwise |

## Run

One execution of a protocol against one event. Not a separate stored
entity — it is the `steps` + `insight` + `outcome` portion of an Event
record.

## Step

The contract between the Main Agent and the executor. Precisely three
fields, nothing else:

| Field | Type | Optional |
|---|---|---|
| `agent_name` | `str` | no |
| `task_text` | `str` | no — exactly what the Main Agent produced, unmodified |
| `allowed_tools` | `list[str]` | no |

A **result** is attached to an executed step (not part of the Step contract
itself, but stored alongside it): `result_text: str`, `attempt_count: int`.

## Precedent

A prior event with the same classification and area, whose occurrence
timestamp falls inside the lookback window, together with how it was
handled and how it ended.

| Field | Type | Optional |
|---|---|---|
| `event_id` | `str` | no |
| `protocol_used` | `str` | no |
| `agent_actions` | `list[str]` | yes |
| `resolved` | `bool` | no |
| `ending` | `str` | required if `resolved` is `True` |

A precedent record without an ending is unusable: closure requires knowing
the prior event was resolved.

## Insight

The Insights Agent's single conclusion about a run, covering both the
current run and the historical comparison.

| Field | Type | Optional |
|---|---|---|
| `text` | `str` | no |
| `based_on_steps` | `list[Step]` (task + result pairs) | no |
| `based_on_precedents` | `list[Precedent]` | yes |

## Summary

A rolled-up period record (daily, monthly, or yearly — same shape in all
three tables). See `persistence/schema.py`'s `SUMMARY_TABLE_NAMES` and
`_summary_table_ddl` (work_plan.md §2.6, implemented in Mission 2) for the
authoritative shape — each table also carries `UNIQUE(period_start,
period_end)`, which is both the §2.8 period index and what lets writing a
summary for an already-summarized period overwrite rather than duplicate.

| Field | Type | Optional |
|---|---|---|
| `summary_text` | `str` | no |
| `period_start` | `datetime` | no |
| `period_end` | `datetime` | no |
| `generated_at` | `datetime` | no |
| `event_index` | `list[dict]` | no after Mission 5 regeneration; legacy rows may temporarily be empty/`None` |

Summary periods are half-open (`[period_start, period_end)`). `event_index`
retains each covered event's ID, classification, area, occurrence time,
outcome, and deterministic resolved flag so precedent search can identify
candidate periods before reading raw events.

## User

| Field | Type | Optional |
|---|---|---|
| `telegram_identity` | `str` | no |
| `permission_level` | `str` (`"viewer"` \| `"commander"`) | no |

No global/shared user: a person working two deployments is two rows in two
databases.

## Hold states

An event may be in **at most one** of these at a time. Clarification always
precedes approval, so an event cannot be in both.

**Held for clarification**
| Field | Type | Optional |
|---|---|---|
| `held` | `bool` | no |
| `unresolved_field` | `str` | required if `held` |
| `resolved_by` | `str` (commander identity) | yes until resolved |
| `chosen_classification` | `str` | yes until resolved |

**Held for approval**
| Field | Type | Optional |
|---|---|---|
| `held` | `bool` | no |
| `reason` | `str` (`"flagged_protocol"` \| `"ambiguous_selection"`) | required if `held` |
| `answered_by` | `str` (commander identity) | yes until answered |
| `answered_at` | `datetime` | yes until answered |

## Human activation

A built-in event type, present in every deployment, marking an event that
came from a person requesting an action rather than from anything observed
in the field. It is the only classification not drawn from the profile —
the event-type registry (§2.1) adds it on every run and rejects a profile
that tries to declare it itself as a duplicate.
