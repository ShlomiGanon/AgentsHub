"""English user-interface messages."""

MESSAGES = {
    "status.thinking": "The model is thinking...",
    "status.async_ack": "Got it — your request is queued.\nTask ID: {task_id}",
    "error.request_failed": "Request failed: {reason}",
    "debug.llm_call": (
        "LLM {provider}/{model} completed in {latency_ms} ms; "
        "tokens: {tokens}."
    ),
    "debug.api_received": "Trace: the request reached the API and Main Agent.",
    "debug.intent": "Trace: Main Agent classified the message as {intent}.",
    "debug.report": "Trace: report {event_id} was recorded and queued.",
    "debug.request": "Trace: action request {event_id} was recorded and queued.",
    "debug.extraction": "Trace: event data resolved to classification={classification}, area={area}.",
    "debug.risk": "Trace: risk assessment returned {risk_level}.",
    "debug.protocol": "Trace: protocol selection status={status}, protocol={protocol}.",
    "debug.hold_created": "Trace: {hold_kind} hold was created for event {event_id}.",
    "debug.hold_resolved": "Trace: {hold_kind} hold was resolved for event {event_id}.",
    "debug.queue": "Trace: queued work started after {wait_ms} ms.",
    "debug.stage": "Trace: stage {stage} finished with status {status} in {latency_ms} ms.",
    "debug.step_start": "Trace: step {step_index} was routed to {agent}.",
    "debug.step_result": "Trace: step {step_index} from {agent} finished with status {status}.",
    "debug.step_retry": "Trace: {agent} step is retrying (attempt {attempt}).",
    "debug.step_failed": "Trace: {agent} step failed on attempt {attempt}.",
    "debug.waiting_data": "Trace: protocol work is waiting for event fields: {fields}.",
    "debug.tool": "Trace: {agent} tool {tool} finished with status {status}.",
    "debug.tool_blocked": "Trace: blocked unauthorized tool {tool} for {agent}.",
    "debug.provider": "Trace: LLM provider={provider}, model={model} finished in {latency_ms} ms; tokens: {tokens}.",
    "debug.provider_failed": "Trace: LLM provider={provider}, model={model} failed after {latency_ms} ms; tokens: {tokens}.",
    "debug.insight": "Trace: Insights Agent returned its assessment for {protocol}.",
    "debug.judgment": "Trace: Judgment returned verdict {verdict}.",
    "debug.outcome": "Trace: event {event_id} reached outcome {outcome}.",
    "debug.queue_failed": "Trace: queued work failed.",
    "debug.tokens_breakdown": "input={input}, output={output}, cache={cache}",
    "debug.tokens_unavailable": "unavailable",
    "header.clarification_needed": "[CLARIFICATION NEEDED — please reply]",
    "header.approval_needed": "[APPROVAL NEEDED — please reply]",
    "header.precedent_closure": "[NOTICE — closed on precedent — no reply needed]",
    "header.uncertain_verdict": "[NOTICE — uncertain verdict — no reply needed]",
    "header.no_match": "[NOTICE — no protocol available — no reply needed]",
    "header.result": "[RESULT]",
    "header.failed": "[RUN FAILED]",
    "header.declined": "[DECLINED]",
    "header.event_data_needed": "[MORE EVENT DETAILS NEEDED]",
    "result.verdict": "Verdict: {outcome}",
    "result.what_was_done": "What was done:",
    "result.insight": "Insight:",
    "failure.failed_step": "Failed step: {agent}",
    "failure.reason": "Reason: {reason}",
    "failure.completed_before": "Completed before the failure:",
    "failure.nothing_completed": "Nothing completed before the failure.",
    "common.unknown": "(unknown)",
    "common.none": "(none)",
    "common.no_reason": "(no reason given)",
    "auth.unregistered": (
        "You are not a registered user of this system (identity: {identity}). "
        "An administrator must add you before you can use this bot."
    ),
    "auth.operation_refused": (
        "Refused: '{operation}' requires commander level; your account "
        "({identity}) is registered as {level}."
    ),
    "profile.nothing_changed": (
        "Nothing has changed in the running system — this edit applies from the next start."
    ),
    "profile.name": "Profile: {profile_name}",
    "profile.agents": "Agents:",
    "profile.protocols": "Protocols:",
    "profile.protocol_requires_approval": "requires approval",
    "profile.protocol_no_approval": "no approval required",
    "profile.protocol_line": (
        "- {name} (criticality: {criticality}, {approval}): {description}"
    ),
    "profile.event_types": "Event types: {event_types}",
    "profile.areas": "Areas: {areas}",
    "profile.restart_pending": (
        "The profile file on disk differs from what is running. A restart is pending to pick up the change."
    ),
    "profile.restart_not_pending": (
        "The profile file on disk matches what is running. No restart is pending."
    ),
    "protocol.approval_flag_required": (
        "Refused: 'approval_flag' must be given explicitly as true or false — it is never defaulted."
    ),
    "common.rejected": "Rejected: {message}",
    "settings.view": (
        "Retry count: {retry_count}\nRisk threshold: {risk_threshold}\n"
        "Lookback window (days): {lookback_window_days}"
    ),
    "settings.retry_whole": "Refused: 'retry_count' must be a whole number, got {value}.",
    "settings.retry_nonnegative": "Refused: 'retry_count' cannot be negative.",
    "settings.risk_number": "Refused: 'risk_threshold' must be a number, got {value}.",
    "settings.risk_range": "Refused: 'risk_threshold' must be between 0.0 and 1.0.",
    "settings.lookback_whole": (
        "Refused: 'lookback_window_days' must be a whole number, got {value}."
    ),
    "settings.lookback_positive": (
        "Refused: 'lookback_window_days' must be at least 1 — a zero-length window is a configuration error."
    ),
    "settings.unknown": (
        "Refused: unknown setting {field}. Only retry_count, risk_threshold, and "
        "lookback_window_days may be changed."
    ),
    "settings.saved": (
        "{message}\n\nThis took effect immediately and has been saved — unlike a profile edit, no restart is needed."
    ),
    "approval.risk": "Risk: {risk_level} ({risk_reason})",
    "approval.flagged": (
        "{header}\n\nProtocol flagged for approval: {protocol_name}\n{risk}\n\nShould this run?"
    ),
    "approval.ambiguous": (
        "{header}\n\nMultiple protocols fit equally well:\n{candidates}\n{risk}\n\nWhich should run?"
    ),
    "approval.approve": "Approve",
    "approval.reject": "Reject",
    "approval.resumed": "Recorded — the protocol has been resumed.",
    "approval.rejected": "Recorded — declined; the event will not run.",
    "approval.already_answered": "This approval was already answered{who}. {message}",
    "clarification.prompt": (
        "{header}\n\nRaw report:\n{raw_text}\n\nCould not resolve: {field}.\n"
        "Choose the correct classification below."
    ),
    "clarification.resumed": "Recorded — the flow has resumed with your choice.",
    "clarification.already_resolved": "This clarification was already resolved{who}. {message}",
    "common.by_identity": " by {identity}",
    "notice.uncertain": (
        "{header}\n\nEvent {event_id} finished with an uncertain verdict.\n\nInsight:\n{insight}"
    ),
    "notice.no_match": (
        "{header}\n\nNo existing protocol can fulfill this request.\nRaw text: {raw_text}\n"
        "{reason}\nRisk: {risk_level} ({risk_reason})"
    ),
    "notice.precedent": (
        "{header}\n\nEvent: {raw_text}\n\nClosed against precedent {precedent_id}, "
        "which ended: {ending}"
    ),
    "bot.not_available": "This isn't available yet: {reason}",
    "bot.handler_error": "Something went wrong handling that. It has been logged.",
    "bot.no_answer": "(no answer was returned)",
    "bot.refused": "Refused: {message}",
    "bot.taken_as": "Got it — taken as a {kind}.",
    "bot.waiting_approval": "It is now waiting for a commander's approval.",
    "bot.job_queued": "Job ID: {job_id}. You'll hear back here once it's done.",
    "protocol.expected_fields": (
        "Refused: expected 7 pipe-separated fields — name | description | "
        "participating_agents (comma-separated) | approved_tools (comma-separated) | "
        "expected_success_output | criticality | approval_flag (true/false)."
    ),
    "protocol.flag_boolean": "Refused: 'approval_flag' must be exactly 'true' or 'false'.",
    "command.profile_usage": "Usage: /profile view | diff | add ... | edit ... | remove <name>",
    "command.settings_usage": (
        "Usage: /settings view | set <retry_count|risk_threshold|lookback_window_days> <value>"
    ),
    "api.internal_error": "an internal error occurred",
    "api.identity_required": "No identity was supplied.",
    "api.identity_unregistered": "'{identity}' is not a registered identity.",
    "api.operation_forbidden": "Permission level {level} may not {operation}.",
    "api.field_required": "'{field}' is required.",
    "api.conversation_id_invalid": (
        "'conversation_id' must be a non-empty string of at most 200 characters."
    ),
    "api.queue_full": "The event queue is full; retry later.",
    "api.queue_full_event_detail": "The event queue is full; retry the event detail later.",
    "api.event_detail_again": "Please provide the missing event details again.",
    "api.clarify_check_record_do": "Could you clarify what you want me to check, record, or do?",
    "api.clarify_action": "Could you clarify what you want me to do?",
    "api.queued_report": "Queued report. Task ID: {task_id}.",
    "api.queued_request": "Queued request. Task ID: {task_id}.",
    "api.missing_required_field": "Missing required field: {field}.",
    "api.malformed_protocol": "The protocol body is malformed: {reason}",
    "api.profile_field_restart": (
        "'{field}' belongs to the profile and takes effect only after a restart."
    ),
    "api.retry_nonnegative_integer": "'retry_count' must be a non-negative integer.",
    "api.risk_threshold_range": "'risk_threshold' must be a number between 0.0 and 1.0.",
    "api.lookback_positive_integer": "'lookback_window_days' must be a positive integer.",
    "api.other_identity_forbidden": "A viewer may not view another identity's registration.",
    "api.job_not_found": "No such task: '{task_id}'.",
    "api.hold_not_found": "No {kind} hold was created for event '{event_id}'.",
    "api.hold_resolved": "Already resolved by '{identity}' at {resolved_at}.",
    "api.decision_required": (
        "'decision' is required: 'approved', 'rejected', or a candidate protocol name."
    ),
    "api.cursor_invalid": "'since' must be a non-negative integer cursor.",
    "api.wait_invalid": "'wait_seconds' must be an integer between 0 and 30.",
    "api.trace_id_invalid": "The trace ID is invalid.",
    "api.deep_debug_disabled": "Deep Debug is not enabled on this server.",
    "terminal.mode_prompt": "\nMode — [m]essage, [e]vent, or [q]uit? ",
    "terminal.mode_invalid": "Please type 'm', 'e', or 'q'.",
    "terminal.sample_events": "\nSample sensor events:",
    "terminal.sample_fire": "Fire report — north sector",
    "terminal.sample_medical": "Medical report — south sector",
    "terminal.sample_unknown": "Unclassifiable reading (drives a clarification hold)",
    "terminal.sample_custom": "Custom — type your own text",
    "terminal.back": "  [q] back to mode selection",
    "terminal.choose_prompt": "choose> ",
    "terminal.invalid_choice": "Invalid choice.",
    "terminal.event_text": "event text> ",
    "terminal.event_text_default": "event text [{default}]> ",
    "terminal.sender_default": "sender identity [{default}]> ",
    "terminal.message_prompt": "\nmessage> ",
    "terminal.request_failed": "(request failed: {reason})",
    "terminal.submission_refused": "submission refused ({status}): {reason}",
    "terminal.submitted": "submitted: event_id={event_id} status={status}",
    "terminal.waiting": "\n(waiting for a result — Ctrl+C to stop waiting and return to the prompt)",
    "terminal.poll_failed": "(polling failed: {reason}; retrying)",
    "terminal.profile": "Profile:  {profile}",
    "terminal.database": "Database: {database}",
    "terminal.api": "API:      {base_url}  (make sure `{command}` is already running)",
    "terminal.background": (
        "(background polling starts immediately; type /holds at the message prompt any time to review open holds)"
    ),
    "terminal.goodbye": "\nGoodbye.",
    "terminal.skip_existing": (
        "(skipping {count} pre-existing notification(s) already in this deployment's history, "
        "from before this session started)"
    ),
    "terminal.first_run_skip": (
        "(first run for identity {identity} — skipping {count} pre-existing notification(s) "
        "already in this deployment's history; every run after this one resumes from here, "
        "the same way the real bot does)"
    ),
    "terminal.poll_background_error": "(background polling hit an error and is retrying: {reason})",
    "terminal.new_notifications": "--- {count} new notification(s) since your last turn ---",
    "terminal.holds_need_answer": "({count} of those need an answer — type /holds to review)",
    "terminal.clarification_hold": "Clarification hold — event {event_id}",
    "terminal.choose_classification": "Choose the correct classification:",
    "terminal.skip_hold": "  [s] Skip for now (leave this hold open)",
    "terminal.skipped_hold": "(skipped — this hold is still open; use /holds to come back to it)",
    "terminal.your_choice": "your choice> ",
    "terminal.invalid_hold_choice": "Invalid choice — pick one of the numbers above, or 's' to skip.",
    "terminal.choose": "Choose:",
    "terminal.no_holds": "No pending holds right now.",
    "terminal.identity_exists": "{level}-level identity already present: {identity}.",
    "terminal.provision_identity": "Provisioning {level}-level identity via `cli.user_admin`: {identity}",
    "terminal.provision_service": (
        "Provisioning the bot's own service identity via `cli.user_admin`: {identity}"
    ),
}
