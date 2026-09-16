# Task 4 — Intent classification for attendance reports

## Investigation

The live trace `709a697847ed4a55bb10607fb8e51816` made two provider calls in
the `intent_classification` stage. Both provider calls reported `status=success`
and `termination_reason=stop`; the first produced 373 output tokens and the
second 188. Therefore the retry was not a provider failure or timeout: the
first response was rejected by the application parser and the existing
two-attempt loop sent a repair prompt. The exact first raw response and parser
exception cannot be recovered because the run used `DEEP_DEBUG=false`, which
deliberately does not persist model I/O. The persisted telemetry contains only
token counts/status and the second accepted decision.

The second response was schema-valid and was accepted as
`needs_clarification`, with reason “Message text is garbled and unreadable;
content inferred from conversation pattern.” Since it parsed successfully,
the retry loop correctly did not retry it. The old parser also treated any
non-null `ambiguity_reason` (and any `is_followup_without_context`) as an
intent-layer clarification, even when the model had selected an operational
intent. That conflated missing business fields with missing intent.

## Fix

- The intent prompt now states that one clear availability/unavailability
  statement is a REPORT even when reason, duration, or location is absent.
- A schema-valid operational primary intent is preserved; `ambiguity_reason`
  is no longer enough to convert it into `needs_clarification`.
- A narrow first-person attendance fallback recovers clear Hebrew/English
  availability statements if a model nevertheless returns a valid but
  inappropriate clarification decision. It does not infer or fill any
  business field.
- Parser rejection is now logged with attempt number and reason (without
  persisting the raw response), making future retries diagnosable.

The Team Status tool remains responsible for domain validation. For example,
an unavailable response without a reason still returns a reason clarification
after the event reaches that flow.

## Tests

`pytest -q tests/test_intent_attendance.py tests/test_orchestrator_holds.py
tests/test_orchestrator_reasoning.py tests/test_team_status_agent.py
tests/test_api_messages.py` → **124 passed** (one existing deprecation warning).

The full repository command `pytest -q` exceeded the 180-second command
budget without producing a test result; no live application run was started
for this task.
