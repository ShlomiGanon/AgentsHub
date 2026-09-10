"""English user-interface messages."""

MESSAGES = {
    "status.thinking": "The model is thinking...",
    "status.async_ack": "Got it — your request is queued.\nTask ID: {task_id}\nYou'll hear back here once it's done.",
    "error.request_failed": "Request failed: {reason}",
    "error.run_failure_generic": "Couldn't process that — try rephrasing, or contact a commander.",
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
    "header.uncertain_reporter": "[UPDATE]",
    "header.no_match": "[NOTICE — no protocol available — no reply needed]",
    "header.result": "[RESULT]",
    "header.failed": "[RUN FAILED]",
    "header.declined": "[DECLINED]",
    "header.event_data_needed": "[MORE EVENT DETAILS NEEDED]",
    "result.verdict": "Verdict: {outcome}",
    "result.job_id": "Job ID: {job_id}",
    "outcome.succeeded": "succeeded",
    "outcome.failed": "failed",
    "outcome.uncertain": "uncertain",
    "outcome.closed_on_precedent": "closed on precedent",
    "outcome.declined": "declined",
    "outcome.no_match_protocol": "no matching protocol",
    "risk.high": "high",
    "risk.low": "low",
    "result.what_was_done": "What was done:",
    "result.insight": "Insight:",
    "result.protocol_suffix": "Protocol: {protocol_name} ({risk_level}, {reason})",
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
    "notice.uncertain_reporter": (
        "{header}\n\nYour reported event is still being reviewed. "
        "We'll update you if there's more to share."
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
    "bot.welcome": (
        "Hi — this is {profile_name}. Report something, ask a question, or "
        "request an action — just type it."
    ),
    "command.menu_start": "Get started",
    "command.menu_profile": "View or edit the active profile",
    "command.menu_settings": "View or change live settings",
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
    "api.sender_identity_mismatch": "The sender identity does not match the authenticated identity.",
    "api.event_data_event_id_invalid": "The event-data continuation ID is invalid.",
    "api.event_data_reply_not_pending": "That event-data request is not pending for this user and conversation.",
    "api.identity_unregistered": "'{identity}' is not a registered identity.",
    "api.operation_forbidden": "Permission level {level} may not {operation}.",
    "api.field_required": "'{field}' is required.",
    "api.conversation_id_invalid": (
        "'conversation_id' must be a non-empty string of at most 200 characters."
    ),
    "api.queue_full": "The event queue is full; retry later.",
    "api.queue_full_event_detail": "The event queue is full; retry the event detail later.",
    "api.event_detail_again": "Please provide the missing event details again.",
    "api.event_detail_ambiguous": (
        "You have {count} reports waiting for missing details right now, so I "
        "can't tell which one this reply is about. A commander should resolve "
        "the older one first, then reply again."
    ),
    "api.clarify_check_record_do": "Could you clarify what you want me to check, record, or do?",
    "api.clarify_action": "Could you clarify what you want me to do?",
    "api.drone_selection_invalid": "No matching drone was identified. Choose a name or ID from this list:\n{choices}\nYou can also reply: all",
    "api.drone_recall_none": "There are no active drone missions; no state was changed.",
    "api.drone_recall_all_done": "Returned to base: {names}. Their missions were closed.",
    "api.drone_recall_one_done": "{callsign} returned to base. Mission {mission_id} was closed.",
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
    "api.group_not_registered": "Telegram group '{chat_id}' is not registered for routing.",
    "api.protocol_out_of_group_scope": "Protocol '{protocol}' is not available in this group (routed to {agent}).",
    "api.group_agent_invalid": "'{agent}' is not a routable agent. Allowed: {allowed}.",
    "api.attendance_agent_unavailable": "No attendance specialist is registered in this deployment.",
    "bot.group_added_hint": (
        "This group (chat ID {chat_id}) is not yet registered. A commander must bind it to an agent "
        "in the admin panel or with the group-admin command before messages here are handled."
    ),
    "bot.unavailability_prompt_group": "{name}, please reply to this message with the reason for your unavailability and the estimated number of days (for example: 'ill for two days').",
    "attendance.group_prompt": (
        "Daily readiness-team attendance check. Reply within one hour (until {deadline}) with your availability. "
        "If you are unavailable, include the reason and number of days.\n\nMembers required to report:\n{members}"
    ),
    "attendance.group_prompt_nobody": "The daily attendance check is open. No members need to report today.",
    "attendance.button_available": "Available for duty",
    "attendance.button_unavailable": "Unavailable",
    "attendance.already_open": "Today's attendance cycle is already open.",
    "bot.unavailability_prompt": "Please provide the reason for your unavailability and the estimated number of days (for example: 'ill for two days').",
    "bot.unavailability_days_prompt": "The reason was saved. How many days will you be unavailable? Please send a number, for example: 2.",
    "bot.availability_report_available": "Readiness attendance report: user {identity} is available for duty.",
    "bot.availability_report_unavailable": "Readiness report: user {identity} is unavailable. Reason: {reason}. Duration: {days} days. Update their availability accordingly.",
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
    "admin.login_wrong_credentials": "Wrong username or password.",
    "admin.login_locked_out": "Too many failed attempts. Try again in {duration}.",
    "admin.lockout_less_than_a_minute": "less than a minute",
    "admin.lockout_one_minute": "about 1 minute",
    "admin.lockout_minutes": "about {minutes} minutes",
    "admin.lockout_one_hour": "about 1 hour",
    "admin.lockout_hours": "about {hours} hours",
    "admin.login_title": "Admin sign in",
    "admin.login_subtitle": "Bot control panel",
    "admin.username": "Username",
    "admin.password": "Password",
    "admin.sign_in": "Sign in",
    "admin.connected": "connected",
    "admin.log_out": "Log out",
    "admin.dashboard_title": "User administration",
    "admin.dashboard_subtitle": "Manage who can talk to the bot and what they're allowed to do.",
    "admin.nav_dashboard": "User administration",
    "admin.nav_simulator": "Scenario simulator",
    "admin.col_identity": "Telegram identity",
    "admin.col_level": "Level",
    "admin.save": "Save",
    "admin.remove": "Remove",
    "admin.add": "Add",
    "admin.tag_bot_service": "bot's own service identity",
    "admin.add_user": "Add a user",
    "admin.confirm_remove_user": "Remove {identity}?",
    "admin.groups_title": "Telegram groups",
    "admin.groups_subtitle": "Bind a group chat to the specialist its messages belong to. Groups not listed here are ignored by the bot.",
    "admin.col_chat_id": "Chat ID",
    "admin.col_label": "Label",
    "admin.col_routed_to": "Routed to",
    "admin.no_groups": "No groups registered yet.",
    "admin.confirm_remove_group": "Remove group {chat_id}?",
    "admin.add_group": "Add a group",
    "admin.add_group_help": (
        "The chat ID is the negative number the bot posts when it is added to an unregistered group. "
        "Choose {main_agent} for full routing, or one specialist to restrict the group to that agent's protocols."
    ),
    "admin.label_placeholder": "readiness team",
    "admin.bot_service_title": "Bot's own service identity",
    "admin.bot_service_help": (
        "Registers or re-registers {identity} at commander level. Required before the bot can poll "
        "notifications, read the commander roster, or check for profile changes."
    ),
    "admin.bot_service_button": "Register bot-service",
    "admin.session_expired": "Your session expired from inactivity — please sign in again.",
    "admin.csrf_failed": "That action could not be verified — please try again.",
    "admin.signed_out": "Signed out.",
    "admin.chat_id_required": "A Telegram chat ID is required.",
    "admin.group_agent_invalid": "'{agent}' is not a routable agent.",
    "admin.group_routed": "Group '{chat_id}' is now routed to '{agent}'.",
    "admin.group_not_found": "No such group: '{chat_id}'.",
    "admin.group_removed": "Group '{chat_id}' removed.",
    "admin.identity_required": "A Telegram identity is required.",
    "admin.level_invalid": "'{level}' is not a valid permission level.",
    "admin.user_written": "'{identity}' is now '{level}'.",
    "admin.user_not_found": "No such user: '{identity}'.",
    "admin.user_removed": "'{identity}' removed.",
    "admin.bot_service_provisioned": "'{identity}' is registered at commander level.",
    "admin.error_title": "Something went wrong",
    "admin.error_subtitle": "Try again, or check the server log.",
    "admin.simulator.title": "Scenario simulator",
    "admin.simulator.subtitle": "Load a scenario and feed its steps to the system exactly as the bot would, one chat at a time. Every step is sent to the live API under its own sender identity.",
    "admin.simulator.drop_zone": "Click to choose a scenario JSON file, or drop it here",
    "admin.simulator.paste_label": "Or paste scenario JSON",
    "admin.simulator.load_pasted": "Load pasted JSON",
    "admin.simulator.load_example": "Load example",
    "admin.simulator.send_next": "Send next step",
    "admin.simulator.reset_view": "Reset view",
    "admin.simulator.no_scenario": "No scenario loaded",
    "admin.simulator.untitled": "Untitled scenario",
    "admin.simulator.badge_id": "ID: {id}",
    "admin.simulator.badge_steps": "{count} steps",
    "admin.simulator.badge_chats": "{count} chats",
    "admin.simulator.loaded": "Scenario loaded: {title}",
    "admin.simulator.next_in_queue": "Next in queue",
    "admin.simulator.next_in_chat": "Next in this chat",
    "admin.simulator.step_label": "step {step}",
    "admin.simulator.send_this": "Send this step ({step})",
    "admin.simulator.wait_turn": "Wait for turn ({step})",
    "admin.simulator.no_more": "No more steps",
    "admin.simulator.all_sent": "Every step for this chat has been sent.",
    "admin.simulator.kind_message": "Telegram chat",
    "admin.simulator.kind_event": "Sensor",
    "admin.simulator.route_private": "Private chat - unscoped",
    "admin.simulator.route_bound": "Group routed to {agent}",
    "admin.simulator.route_unregistered": "Group not registered - the bot ignores it and the API refuses it",
    "admin.simulator.route_sensor": "Sensor events - POST /Event",
    "admin.simulator.warn_sender_unregistered": "Sender {identity} is not registered - the API will refuse this step",
    "admin.simulator.system_label": "System",
    "admin.simulator.sending": "Sending...",
    "admin.simulator.taken_as": "Taken as: {kind}",
    "admin.simulator.event_id": "Event {event_id}",
    "admin.simulator.duplicate": "Duplicate of an earlier submission",
    "admin.simulator.status_queued": "Queued",
    "admin.simulator.status_running": "Running",
    "admin.simulator.status_held_for_clarification": "Held for clarification ({field})",
    "admin.simulator.status_held_for_approval": "Held for commander approval ({reason})",
    "admin.simulator.status_waiting_for_event_data": "Waiting for event data ({fields})",
    "admin.simulator.status_succeeded": "Succeeded",
    "admin.simulator.status_failed": "Failed",
    "admin.simulator.status_uncertain": "Uncertain",
    "admin.simulator.status_closed_on_precedent": "Closed on precedent",
    "admin.simulator.status_declined": "Declined",
    "admin.simulator.status_other": "Status: {status}",
    "admin.simulator.steps_completed": "Steps completed:",
    "admin.simulator.poll_timeout": "Stopped polling after {minutes} minutes - check the job later.",
    "admin.simulator.request_failed": "Request failed ({status}): {message}",
    "admin.simulator.network_error": "Network error: {message}",
    "admin.simulator.err_parse": "Could not parse the JSON: {message}",
    "admin.simulator.err_chats_required": "'chats' must be a non-empty list",
    "admin.simulator.err_steps_required": "'steps' must be a non-empty list",
    "admin.simulator.err_chat_key": "Chat #{index} needs a unique string 'key'",
    "admin.simulator.err_chat_kind": "Chat '{key}' has an unknown kind '{kind}' (expected 'message' or 'event')",
    "admin.simulator.err_chat_type": "Chat '{key}' has an invalid telegram_chat_type '{type}' (expected private, group or supergroup)",
    "admin.simulator.err_chat_id_required": "Chat '{key}' is a group and needs a telegram_chat_id",
    "admin.simulator.err_step_number": "Step #{index} needs a unique numeric 'step'",
    "admin.simulator.err_step_chat": "Step {step} refers to an unknown chat '{chat}'",
    "admin.simulator.err_step_sender": "Step {step} needs a 'sender_identity'",
    "admin.simulator.err_step_text": "Step {step} needs a non-empty 'text'",
    "admin.simulator.example_title": "Example scenario for this deployment",
    "admin.simulator.example_description": "Built from the users and groups registered right now. Edit the texts and load it again.",
    "admin.simulator.example_private_label": "Private chat with a commander",
    "admin.simulator.example_group_label": "Registered group",
    "admin.simulator.example_sensor_label": "Fence sensors",
    "admin.simulator.example_text_private": "What is the situation right now?",
    "admin.simulator.example_text_group": "Good morning, I am available tonight.",
    "admin.simulator.example_text_event": "smoke observed at gate 3",
    "admin.simulator.example_needs_user": "Register at least one human user before loading the example.",
    "bot.queue_empty": "There are currently no requests awaiting your approval.",
    "bot.queue_header": "{count} request(s) awaiting your approval:",
    "bot.queue_card_approval": "Type: {action_type}\nDescription: {description}\nWaiting: {waiting_time}\nRequester: {requester}\nRisk: {risk_level}{risk_reason}",
    "bot.queue_card_clarification": "Type: {action_type}\nOriginal report: {description}\nWaiting: {waiting_time}\nReporter: {requester}\nMissing info: {unresolved_info}",
    "bot.btn_approve": "Approve",
    "bot.btn_reject": "Reject",
    "bot.btn_cancel": "Cancel",
    "bot.queue_approved": "Approved",
    "bot.queue_rejected": "Rejected",
    "bot.queue_resolved": "Clarified and updated",
    "bot.queue_button": "Approvals Queue",
    "bot.commander_only": "This action is available to commanders only.",
    "action.dispatch_drone_to_incident": "Tactical drone dispatch",
    "action.recall_drone_to_base": "Drone recall to base",
    "action.dispatch_emergency_forces": "Emergency forces dispatch",
    "action.clarification": "Report clarification",
    "action.generic": "Operational action",
    "action.unresolved_classification": "Event classification missing or ambiguous",
    "time.seconds_ago": "{seconds}s ago",
    "time.minutes_ago": "{minutes}m ago",
    "time.hours_ago": "{hours}h ago",
    "time.unknown": "unknown time",

    # --- profiles/unified_test.py — mechanically relocated from source so no
    # first-party module holds a Hebrew literal outside this catalog
    # (tests/test_hebrew_leakage.py). Keys namespaced "unified.*"/"seed.*".
    # This profile's DEFAULT_LANGUAGE is "he"; these English strings exist
    # only for catalog parity (messages/catalog.py's validate_catalogs) and
    # so the profile keeps working if DEFAULT_LANGUAGE is ever set to "en".
    "unified.profile_name": "Unified Command Hub",

    "unified.surveillance.role": (
        "Responsible for visual surveillance, the security camera network, and the tactical drone "
        "fleet. Provides drone and battery status, camera status, and drone dispatch or recall."
    ),
    "unified.surveillance.system_prompt": (
        "You are a specialist agent for visual surveillance and drones. "
        "You must answer only in short, precise, operational Hebrew (at most 4-5 lines). "
        "Never use English at all, except for exact identifiers (such as CAM-01, DRONE-01). "
        "To return a drone, always call return_drone_to_base(drone_or_mission_id='') immediately. "
        "When no specific drone is named, pass an empty string and the tool will automatically select the active drone based on fleet state. "
        "Do not attempt preliminary scans, do not invent identifiers, and never report that there are no drones or that the tool is unavailable without having called return_drone_to_base — always call the tool immediately! "
        "To dispatch a drone, call dispatch_drone_to_area immediately with only the target area (target_area). "
        "The specific_drone_id and dispatched_by fields are entirely optional and must never be requested - the system automatically selects a ready drone from the fleet. "
        "Never report that a mission is unclear or that details are missing when the target area is known — dispatch the drone immediately instead. "
        "Be concise, direct, and operational."
    ),
    "unified.surveillance.tool.fleet_status": "Returns operational status, battery levels, and locations for the drone fleet, in Hebrew.",
    "unified.surveillance.tool.active_missions": (
        "Returns every currently active airborne mission, including mission ID, drone, target area, and ETA, in Hebrew."
    ),
    "unified.surveillance.tool.camera_feeds": "Returns the status and picture of security cameras by area or camera ID, in Hebrew.",
    "unified.surveillance.tool.overview": "Combined surveillance and airborne picture: cameras, drones, and active missions, in Hebrew.",
    "unified.surveillance.tool.return_drone": "Returns an active drone to base in a controlled, safe manner, in Hebrew.",
    "unified.surveillance.tool.dispatch_drone": (
        "Dispatches a tactical drone to an area. Only the target_area parameter is required. Every other parameter is entirely optional and must not be requested."
    ),
    "unified.surveillance.tool.update_camera": "Updates a manual observation or status for a security camera, in Hebrew.",

    "unified.surveillance.no_drones": "No drones were found in the fleet.",
    "unified.surveillance.status.ready": "Ready {icon}",
    "unified.surveillance.status.in_flight": "Airborne on mission {icon}",
    "unified.surveillance.status.charging": "Charging {icon}",
    "unified.surveillance.status.maintenance": "In maintenance {icon}",
    "unified.surveillance.fleet_header": "{icon} Drone fleet status ({count} drones):",
    "unified.surveillance.fleet_line": (
        "• [{drone_id}] {callsign} ({model}): {status} | Battery: {battery}% | Area: {area}{mission_info}"
    ),
    "unified.surveillance.fleet_mission_info": " (on mission: {mission_id})",
    "unified.surveillance.fleet_summary": "Summary: {ready} ready | {in_flight} airborne | {charging} charging",

    "unified.surveillance.no_missions": "No active drone missions currently in flight.",
    "unified.surveillance.missions_header": "{icon} Active airborne drone missions ({count}):",
    "unified.surveillance.mission_line": (
        "• [{mission_id}] Drone {callsign} ({drone_id}) -> Area: {target_area} "
        "| Battery: {battery}% | ETA: {eta}s | Mission: {description}"
    ),

    "unified.surveillance.no_cameras": "No active cameras were found in the requested area.",
    "unified.surveillance.camera_status.active": "OK and active {icon}",
    "unified.surveillance.camera_status.offline": "Offline {icon}",
    "unified.surveillance.camera_status.maintenance": "In maintenance {icon}",
    "unified.surveillance.cameras_header": "{icon} Security camera status ({count} cameras):",
    "unified.surveillance.camera_line": "• [{camera_id}] {name} ({area}, {azimuth}°): {feed_summary} [{status}]",

    "unified.surveillance.overview_header": "{icon} Overall surveillance picture:",
    "unified.surveillance.overview_cameras_line": "• Security cameras: {active}/{total} active and healthy in the area.",
    "unified.surveillance.overview_drones_line": "• Drone fleet: {ready} ready for dispatch, {in_flight} airborne on mission.",
    "unified.surveillance.overview_missions_header": "• Airborne missions ({count}):",
    "unified.surveillance.overview_mission_line": "  - Drone {callsign} heading to {target_area} (estimated: {eta}s)",
    "unified.surveillance.overview_no_missions": "• Airborne missions: no active airborne missions right now.",

    "unified.surveillance.recall_none_active": "There are no active drones in flight to recall right now.",
    "unified.surveillance.recall_all_done": (
        "All drones returned successfully {icon}. All active drones ({count}) returned to base and are ready for duty."
    ),
    "unified.surveillance.recall_done": (
        "Drone returned to base successfully {icon}. Drone {callsign} ({drone_id}) returned to base and is ready for duty (drone fleet)."
    ),
    "unified.surveillance.recall_fallback_done": (
        "Recall command received: drone {callsign} ({drone_id}) is now returning to base to land {icon}."
    ),
    "unified.surveillance.recall_no_match_single": "No matching active drone was found to recall.",
    "unified.surveillance.recall_no_match_multi": "No matching active drone was found for '{requested}' out of {count} drones in flight.",
    "unified.surveillance.recall_selection_required": (
        "There are {count} active drones in flight. Please specify which drone to recall, or say 'recall all'."
    ),
    "unified.surveillance.recall_done_generic": "Drone returned to base successfully {icon}.",
    "unified.surveillance.recall_failed": "The drone recall failed: {error}",

    "unified.surveillance.default_incident_description": "Operational patrol and surveillance",
    "unified.surveillance.dispatch_area_required": "A target area must be specified to dispatch the drone.",
    "unified.surveillance.dispatch_done": (
        "Drone dispatch completed successfully {icon}\n"
        "• Drone: {callsign} ({drone_id})\n"
        "• Target area: {target_area}\n"
        "• Estimated arrival (ETA): ~{eta} seconds\n"
        "• Battery: {battery}% | Mission ID: {mission_id}"
    ),
    "unified.surveillance.dispatch_failed": "The drone dispatch failed: {error}",

    "unified.surveillance.camera_id_required": "A camera ID is required to update the observation.",
    "unified.surveillance.camera_update_done": "Camera observation {camera_id} ({name}) updated successfully {icon}: {feed_summary}",
    "unified.surveillance.camera_update_failed": "The camera observation update failed: {error}",

    "unified.team_status.role": (
        "Responsible for managing the readiness team's roster and attendance. Provides availability "
        "reports (who is available/unavailable), and records attendance reports from team members."
    ),
    "unified.team_status.system_prompt": (
        "You are a specialist agent for readiness-team status management. "
        "You must answer only in short, precise, operational Hebrew (at most 4-5 lines). "
        "Never use English at all. "
        "For questions about the readiness team's attendance status, call report_team_availability. "
        "Choose the right view: summary for the general picture, members for the team members' names, available for who is available, "
        "unavailable for who is unavailable, awaiting for who has not yet reported, count for the number available, and reason for the reason someone is unavailable; "
        "for a reason view, also pass member_query taken from the question. Do not invent names or reasons. "
        "To record an attendance report, call record_attendance_response. "
        "Be concise and clear."
    ),
    "unified.team_status.tool.report_availability": (
        "Returns the real roster data for the current cycle. view is summary, members, available, unavailable, "
        "awaiting, count, or reason; for reason, member_query must also be passed."
    ),
    "unified.team_status.tool.get_roster": (
        "Returns only the readiness team's roster picture and members' availability (read-only, no side effects), in Hebrew."
    ),
    "unified.team_status.tool.record_attendance": "Records a readiness-team member's attendance response, in Hebrew.",

    "unified.team_status.legacy_placeholder_name": "Readiness team member ({identity})",
    "unified.team_status.unnamed_member": "User {identity} (name not set)",

    # "|"-delimited keyword groups `_requested_roster_view` matches against
    # a free-text question to infer which roster view was meant — not
    # rendered to anyone, so the same bilingual keyword list is kept in
    # both catalogs rather than translated.
    "unified.team_status.keywords.reason": "למה|סיבת|reason|why",
    "unified.team_status.keywords.awaiting": "לא דיווח|טרם דיווח|ממתין|awaiting|pending",
    "unified.team_status.keywords.unavailable": "מי לא זמין|אינם זמינים|unavailable",
    "unified.team_status.keywords.count_number": "כמה|כמות|how many|count",
    "unified.team_status.keywords.count_available": "זמין|available",
    "unified.team_status.keywords.available": "מי זמין|זמינים בלבד|who is available",
    "unified.team_status.keywords.members": "מי חבר|חברי הכיתה|השמות|מי הם|members|names",

    "unified.team_status.none_now": "none right now",
    "unified.team_status.members_header": "{icon} Readiness team members ({count}): {names}",
    "unified.team_status.available_header": "{icon} Available for duty ({count}): {names}",
    "unified.team_status.unavailable_header": "{icon} Unavailable ({count}):",
    "unified.team_status.unavailable_line": "• {name} — {reason}",
    "unified.team_status.no_reason_saved": "no reason was saved",
    "unified.team_status.none_unavailable": "{icon} No team members are currently marked unavailable.",
    "unified.team_status.awaiting_header": "{icon} Not yet reported ({count}): {names}",
    "unified.team_status.count_summary": "{icon} {available} of {total} team members are currently available.",
    "unified.team_status.reason_unknown_member": "Could not confidently identify the requested team member from the roster.",
    "unified.team_status.reason_unavailable": "{name} is unavailable: {reason}{until}.",
    "unified.team_status.reason_until_suffix": " until {until}",
    "unified.team_status.reason_available": "{name} is marked available; there is no active unavailability reason.",
    "unified.team_status.reason_awaiting": "{name} has not yet reported this cycle; no unavailability reason was saved.",
    "unified.team_status.summary_header": "{icon} Readiness team status (total {count} members):",
    "unified.team_status.summary_available_line": "• Available for duty ({count}): {names}",
    "unified.team_status.summary_unavailable_line": "• Unavailable ({count}): {names}",
    "unified.team_status.summary_awaiting_line": "• Not yet reported ({count}): {names}",

    "unified.team_status.identity_unavailable": "The response was not recorded: the authenticated user identity is unavailable.",
    "unified.team_status.default_original_text": "Availability report: {availability}",
    "unified.team_status.not_approved": "The response was not recorded: the user is not an approved readiness-team member.",
    "unified.team_status.clarify_availability": "Clarification required: state whether you are available or unavailable.",
    "unified.team_status.clarify_reason": "Clarification required: a member who is unavailable must provide a reason.",
    "unified.team_status.clarify_days": "Clarification required: state how many days you will be unavailable.",
    "unified.team_status.record_failed": "The response was not recorded: {error}",
    "unified.team_status.pending_commander_approval": "The report was received and is awaiting commander approval before the readiness status changes.",
    "unified.team_status.marked_available": "{icon} Your availability was updated. You are marked as available for duty.",
    "unified.team_status.marked_unavailable": "{icon} Your availability was updated. You are marked as unavailable ({reason}).",

    "unified.friendly_forces.role": "Responsible for coordinating and dispatching security and emergency forces (police, EMS, firefighters, military).",
    "unified.friendly_forces.system_prompt": (
        "You are a specialist agent for coordinating and dispatching security and emergency forces (police, EMS, firefighters, military). "
        "You must answer only in short, precise Hebrew (at most 3 lines). "
        "Never use English at all. Always report exactly which force was dispatched and to which target."
    ),
    "unified.friendly_forces.tool.ambulance": "Records an EMS/medical force dispatch to the requested target.",
    "unified.friendly_forces.tool.police": "Records a police force dispatch to the requested target.",
    "unified.friendly_forces.tool.firefighters": "Records a firefighting/rescue force dispatch to the requested target.",
    "unified.friendly_forces.tool.military": "Records a military and security force dispatch to the requested target.",
    "unified.friendly_forces.log_ambulance": "EMS dispatched to '{location}': casualties={count}",
    "unified.friendly_forces.confirm_ambulance": "Medical/EMS team dispatch to '{location}' recorded successfully.",
    "unified.friendly_forces.log_police": "Police dispatched to '{location}': units={count}",
    "unified.friendly_forces.confirm_police": "Police force dispatch to '{location}' recorded successfully.",
    "unified.friendly_forces.log_firefighters": "Firefighters dispatched to '{location}': vehicles={count}",
    "unified.friendly_forces.confirm_firefighters": "Firefighting and rescue force dispatch to '{location}' recorded successfully.",
    "unified.friendly_forces.log_military": "Military force dispatched to '{location}': units={count}",
    "unified.friendly_forces.confirm_military": "Military and security force dispatch to '{location}' recorded successfully.",

    "unified.seed.primary_name": "Commander / Primary User",
    "unified.seed.commander_user_name": "Readiness Team Commander",
    "unified.seed.viewer_user_name": "Readiness Team Member",
    "unified.seed.member_1001": "Dan Levi",
    "unified.seed.member_1002": "Yossi Cohen",
    "unified.seed.member_1003": "Michal Avraham",

    "unified.protocol.overall_situational_picture.description": (
        "Overall sector situational picture (read-only, no data changes): combines surveillance (cameras and drones) with the readiness team's roster in the sector."
    ),
    "unified.protocol.overall_situational_picture.expected_output": (
        "A unified, operational sector picture combining surveillance and the readiness team, with no data changes."
    ),
    "unified.protocol.query_surveillance_overview.description": (
        "Overall surveillance picture: camera status, drones, and active airborne missions across all sectors."
    ),
    "unified.protocol.query_surveillance_overview.expected_output": "A consolidated tactical picture of the surveillance and drone assets.",
    "unified.protocol.query_drone_fleet_status.description": (
        "Checking drone fleet status: availability, battery levels, locations, and operational status of every drone."
    ),
    "unified.protocol.query_drone_fleet_status.expected_output": "A detailed report of drone status, batteries, and dispatch readiness.",
    "unified.protocol.query_active_drone_missions.description": (
        "Checking active airborne drone missions: targets, estimated arrival times, battery levels, and missions."
    ),
    "unified.protocol.query_active_drone_missions.expected_output": "A report of active airborne drone missions.",
    "unified.protocol.query_camera_status.description": "Checking the status and picture of security cameras by sector or a specific camera.",
    "unified.protocol.query_camera_status.expected_output": "A surveillance report of the security cameras in the requested sector.",
    "unified.protocol.dispatch_drone_to_incident.description": (
        "Dispatching a tactical drone to an incident or sector for surveillance or patrol. Commander-only action requiring approval."
    ),
    "unified.protocol.dispatch_drone_to_incident.expected_output": "Confirmation of a drone dispatch to the sector, including callsign and estimated arrival time.",
    "unified.protocol.recall_drone_to_base.description": (
        "Returning an active drone to base and closing an airborne mission. Calls return_drone_to_base immediately with no preliminary scan. "
        "Commander-only action requiring approval."
    ),
    "unified.protocol.recall_drone_to_base.expected_output": "Confirmation that the drone returned to base and its status was updated to ready.",
    "unified.protocol.report_team_availability.description": (
        "A readiness-team attendance and availability report: who is available, who is unavailable, reasons, and who has not yet reported."
    ),
    "unified.protocol.report_team_availability.expected_output": "A detailed, name-by-name picture of the readiness team.",
    "unified.protocol.record_attendance_response.description": (
        "Recording a readiness-team member's attendance report: available or unavailable status, with a reason."
    ),
    "unified.protocol.record_attendance_response.expected_output": "Confirmation that the team member's attendance report was recorded.",
    "unified.protocol.dispatch_emergency_forces.description": (
        "Dispatching and coordinating emergency and security forces: ambulance, police, firefighters, military. Commander-only action requiring approval."
    ),
    "unified.protocol.dispatch_emergency_forces.expected_output": "Confirmation that the emergency-forces dispatch to the target was recorded and coordinated.",
    "unified.protocol.query_historical_incidents.description": "Investigating past incidents and missions from the operational log and history.",
    "unified.protocol.query_historical_incidents.expected_output": "A concise, accurate summary of past events in the operational log.",

    "unified.keyboard.approvals_queue": "{icon} תור אישורים",
    "unified.keyboard.overall_picture": "{icon} תמונת מצב כללית",
    "unified.keyboard.camera_status": "{icon} מצב מצלמות",
    "unified.keyboard.drone_fleet_status": "{icon} מצב צי רחפנים",
    "unified.keyboard.dispatch_drone": "{icon} הזנקת רחפן",
    "unified.keyboard.recall_drone": "{icon} החזרת רחפן לבסיס",
    "unified.keyboard.team_status": "{icon} סטטוס כיתת כוננות",
    "unified.keyboard.dispatch_forces": "{icon} הזנקת כוחות",
    "unified.keyboard.event_history": "{icon} היסטוריית אירועים",
    "unified.keyboard.available": "{icon} אני זמין לכוננות",
    "unified.keyboard.unavailable": "{icon} איני זמין",
}
