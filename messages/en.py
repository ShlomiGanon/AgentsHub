"""English user-interface messages."""

MESSAGES = {
    "status.thinking": "The model is thinking...",
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
    "result.action_unverified": "Action status could not be verified; no execution receipt was recorded.",
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
    "settings.safe_mode_boolean": "Refused: 'safe_mode' must be true or false.",
    "settings.safe_mode_state": "Safe mode: {value}",
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
        "Refused: unknown setting {field}. Only retry_count, risk_threshold, "
        "lookback_window_days, and safe_mode may be changed."
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
    "bot.welcome": (
        "Hi — this is {profile_name}. Report something, ask a question, or "
        "request an action — just type it."
    ),
    "auth.safe_mode_blocked": "This Telegram account is awaiting administrator approval while safe mode is active.",
    "auth.safe_mode_group_blocked": "This Telegram group is awaiting administrator approval while safe mode is active.",
    "bot.full_name_prompt": "Before we continue, please enter your full name (at least two words).",
    "bot.full_name_invalid": "That is not a clear full name. Please enter at least two words.",
    "bot.full_name_saved": "Thanks, {name}. Your name was saved; continuing your previous request.",
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
    "api.telegram_admission_invalid": "The Telegram admission request is invalid.",
    "api.safe_mode_boolean": "'safe_mode' must be true or false.",
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
    "api.unsupported_response": "I cannot verify that response from the system's authorized sources.",
    "operational_button.state_unavailable": "Current operational state is unavailable.",
    "operational_button.history_empty": "No current scoped operational events were found.",
    "operational_button.history_header": "Operational event history ({count}):",
    "operational_button.history_line": "• {text}",
    "api.clarify_action": "Could you clarify what you want me to do?",
    "api.drone_selection_invalid": "No matching drone was identified. Choose a name or ID from this list:\n{choices}\nYou can also reply: all",
    "api.drone_recall_none": "There are no active drone missions; no state was changed.",
    "api.drone_recall_all_done": "Returned to base: {names}. Their missions were closed.",
    "api.drone_recall_one_done": "{callsign} returned to base. Mission {mission_id} was closed.",
    "api.queued_report": "Got it — I've logged this as a report. I'm working on it now and I'll follow up right here once it's done.",
    "api.queued_report_debug": "Got it — I've logged this as a report. I'm working on it now.\nTask ID: {task_id}\nI'll follow up right here once it's done.",
    "api.queued_request": "Got it — I've logged this as an action request. I'm working on it now and I'll follow up right here once it's done.",
    "api.queued_request_debug": "Got it — I've logged this as an action request. I'm working on it now.\nTask ID: {task_id}\nI'll follow up right here once it's done.",
    "api.missing_required_field": "Missing required field: {field}.",
    "api.followup.failed": "The action failed: {reason}",
    "api.followup.unknown_reason": "the persisted failure reason is unavailable",
    "api.followup.executed": "The action was executed according to the verified tool receipt.",
    "api.followup.pending_approval": "The action is waiting for authenticated commander approval.",
    "api.followup.approved": "Approval was recorded; the action has not executed yet.",
    "api.followup.executing": "The action is currently executing.",
    "api.followup.requested": "The action was requested and has not executed yet.",
    "api.followup.unverified": "The system cannot verify that the action executed; no execution receipt was recorded.",
    "api.followup.ambiguous": "More than one operational event could match this follow-up; please specify which one.",
    "api.followup.unknown": "No linked operational event was found for this follow-up.",
    "api.followup.context_failed": "The previous situational request did not complete, so there is no current result to explain. You can ask for the current picture again.",
    "api.followup.context_question": "The previous situational request returned the current operational picture. Ask what part you want clarified.",
    "api.malformed_protocol": "The protocol body is malformed: {reason}",
    "api.profile_field_restart": (
        "'{field}' belongs to the profile and takes effect only after a restart."
    ),
    "api.retry_nonnegative_integer": "'retry_count' must be a non-negative integer.",
    "api.risk_threshold_range": "'risk_threshold' must be a number between 0.0 and 1.0.",
    "api.lookback_positive_integer": "'lookback_window_days' must be a positive integer.",
    "api.other_identity_forbidden": "A viewer may not view another identity's registration.",
    "api.full_name_invalid": "'full_name' must contain at least two words and be at most 120 characters.",
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
    "api.protocol_out_of_profile_scope": "Protocol '{protocol}' is not available for operational profile '{profile}'.",
    "api.group_agent_invalid": "'{agent}' is not a routable agent. Allowed: {allowed}.",
    "api.attendance_agent_unavailable": "No attendance specialist is registered in this deployment.",
    "api.simulation_not_found": "No such simulation: '{simulation_key}'.",
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
    "admin.menu_title": "Administration",
    "admin.menu_subtitle": "Choose an area to manage.",
    "admin.menu_profiles": "Profiles",
    "admin.menu_protocols": "Protocols",
    "admin.menu_events": "Events",
    "admin.menu_users": "User management",
    "admin.menu_groups": "Group management",
    "admin.menu_simulator": "Scenario simulator",
    "admin.menu_server": "Server management",
    "admin.server_title": "Server management",
    "admin.server_subtitle": "Reset the active deployment or restart the stack with another local profile.",
    "admin.server_profile": "Deployment profile",
    "admin.server_active_profile": "Active profile: {profile}",
    "admin.server_load_profile": "Load and restart",
    "admin.server_restart_required": "Loading a profile automatically restarts both the server and the bot.",
    "admin.server_reset": "Reset databases",
    "admin.server_reset_help": "Deletes every database declared by this profile and restarts with empty databases. Telegram users and groups will need to be registered again.",
    "admin.server_reset_confirm": "Delete all databases declared by this profile and restart? This cannot be undone.",
    "admin.server_reset_button": "Reset and restart",
    "admin.server_unavailable": "This action is available only when the stack was started through run_stack.py.",
    "admin.server_profile_invalid": "The selected profile is not an available local profile.",
    "admin.server_reset_confirmation_missing": "Explicit reset confirmation is required.",
    "admin.server_restarting": "The server is restarting",
    "admin.server_restarting_help": "The server and bot are being restarted. This page will reconnect automatically.",
    "admin.server_retry_link": "Try reconnecting now",
    "admin.server_safe_on": "Safe mode is active",
    "admin.server_safe_off": "Open mode is active",
    "admin.server_safe_on_help": "The Telegram bot communicates only with manually approved users and groups.",
    "admin.server_safe_off_help": "The Telegram bot can communicate with everyone and records newcomers as viewers awaiting approval.",
    "admin.server_pending_users": "{count} users awaiting approval",
    "admin.server_pending_groups": "{count} groups awaiting approval",
    "admin.server_choose_open": "Open mode",
    "admin.server_choose_open_help": "Accept and register new Telegram users and groups.",
    "admin.server_choose_safe": "Safe mode",
    "admin.server_choose_safe_help": "Allow only users and groups that were explicitly approved.",
    "admin.server_safe_confirm": "Enable safe mode? {users} users and {groups} groups awaiting approval will be blocked immediately.",
    "admin.server_safe_changed_on": "Safe mode is now active. Unapproved Telegram users and groups are blocked immediately.",
    "admin.server_safe_changed_off": "Open mode is now active. New Telegram users and groups can communicate with the bot.",
    "admin.server_safe_invalid": "The requested access mode is invalid.",
    "admin.server_safe_changing": "Applying the new access mode…",
    "admin.server_safe_changed": "The server setting was updated.",
    "admin.server_safe_change_failed": "The setting could not be changed. Select a COMMANDER identity and try again.",
    "admin.nav_menu": "Administration menu",
    "admin.menu_units": "Operational units",
    "admin.units_title": "Unit settings",
    "admin.units_subtitle": "Configure the trusted LIVE unit and its operational profile.",
    "admin.unit_name": "Unit name",
    "admin.unit_type": "Unit type",
    "admin.unit_response_team": "Response team",
    "admin.unit_fire_station": "Fire and rescue",
    "admin.unit_status": "Status",
    "admin.unit_pending": "Pending configuration",
    "admin.unit_active": "Active",
    "admin.unit_save": "Save unit settings",
    "admin.unit_created": "Operational unit saved.",
    "admin.unit_invalid": "The unit configuration is invalid: {reason}",
    "admin.unit_none": "No LIVE unit has been configured yet.",
    "admin.unit_current": "Current LIVE unit",
    "admin.unit_roles": "Compatible roles",
    "admin.unit_members": "Unit memberships",
    "admin.membership_unit": "Operational unit",
    "admin.membership_role": "Role",
    "admin.membership_status": "Membership status",
    "admin.membership_pending": "Pending onboarding",
    "admin.membership_active": "Active",
    "admin.membership_assign": "Assign and approve",
    "admin.membership_required": "Choose a unit and compatible role before approval.",
    "admin.membership_assigned": "User {identity} was assigned to {unit} as {role}.",
    "admin.membership_no_simulation": "Simulation identities cannot be assigned to a LIVE unit.",
    "admin.users_title": "User management",
    "admin.users_subtitle": "Manage Telegram identities, names, and permission levels.",
    "admin.nav_dashboard": "User administration",
    "admin.nav_simulator": "Scenario simulator",
    "admin.api.identity_title": "Acting Telegram user",
    "admin.api.identity_label": "Registered Telegram identity",
    "admin.api.missing_name": "Name missing",
    "admin.api.identity_save": "Use this identity",
    "admin.api.no_identity": "Register a human Telegram user before performing operational actions.",
    "admin.api.identity_help": "Operational actions are performed as this user and follow the user's normal permissions.",
    "admin.api.identity_invalid": "The selected identity is not a registered human user.",
    "admin.api.identity_selected": "Operational actions will be performed as {identity}.",
    "admin.api.execute": "Execute request",
    "admin.api.refresh": "Refresh",
    "admin.api.loading": "Loading...",
    "admin.api.technical_details": "Technical response details",
    "admin.api.sending": "Sending request...",
    "admin.api.not_run": "The request has not been run yet.",
    "admin.api.network_error": "Network error",
    "admin.api.success": "The action completed successfully.",
    "admin.api.failed": "The action could not be completed.",
    "admin.api.result_status": "Status",
    "admin.api.result_event": "Event",
    "admin.api.result_type": "Handled as",
    "admin.profiles.title": "Profiles",
    "admin.profiles.subtitle": "Inspect the active profile and change its live settings.",
    "admin.profiles.get_help": "Returns the profile overview and the fields permitted for the selected identity.",
    "admin.profiles.put_help": "Only non-empty fields are sent. Profile-file fields still require a server restart.",
    "admin.profiles.active": "Active profile",
    "admin.profiles.agents_count": "{count} agents",
    "admin.profiles.protocols_count": "{count} protocols",
    "admin.profiles.operational_scope": "Operational scope",
    "admin.profiles.event_types": "Event types",
    "admin.profiles.areas": "Areas",
    "admin.profiles.settings_title": "Live settings",
    "admin.profiles.retry_count": "Retries after a failed step",
    "admin.profiles.risk_threshold": "Approval risk threshold",
    "admin.profiles.lookback_days": "History lookback (days)",
    "admin.profiles.safe_mode": "Telegram access mode",
    "admin.profiles.safe_mode_open": "Open - accept new people and groups",
    "admin.profiles.safe_mode_safe": "Safe - approved people and groups only",
    "admin.profiles.safe_mode_help": "Private conversations always use the main agent. New groups are also assigned to the main agent until edited.",
    "admin.profiles.safe_mode_confirm": "Enable safe mode? {users} automatically registered users and {groups} automatically registered groups will be blocked until approved.",
    "admin.profiles.pending_approvals": "Awaiting approval: {users} users and {groups} groups",
    "admin.profiles.components": "Profile components",
    "admin.profiles.agents": "Agents",
    "admin.profiles.protocols": "Protocols",
    "admin.protocols.title": "Protocols",
    "admin.protocols.subtitle": "View and edit the protocols defined by the active profile.",
    "admin.protocols.list_help": "Lists the protocols visible to the selected identity in the running process.",
    "admin.protocols.name": "Protocol name",
    "admin.protocols.criticality": "Criticality",
    "admin.protocols.description": "Description",
    "admin.protocols.agents": "Participating agents (comma-separated)",
    "admin.protocols.tools": "Approved tools (comma-separated)",
    "admin.protocols.success": "Expected success output",
    "admin.protocols.approval": "Requires approval",
    "admin.protocols.delete_confirm": "Delete this protocol from the profile source? The running process changes only after restart.",
    "admin.protocols.existing": "Existing protocols ({count})",
    "admin.protocols.restart_note": "Protocol changes are written to the profile source and become active after the server is restarted.",
    "admin.protocols.none": "No protocols are defined in the active profile.",
    "admin.protocols.add": "Add protocol",
    "admin.events.title": "Events",
    "admin.events.subtitle": "Submit messages and sensor events, inspect jobs and logs, and resolve pending operational decisions.",
    "admin.events.text": "Text",
    "admin.events.holds_help": "Lists unresolved approval and classification holds.",
    "admin.events.approval_help": "Use approved/rejected for an approval hold, or enter a protocol name when resolving an ambiguous selection hold.",
    "admin.events.recent": "Recent events ({count})",
    "admin.events.recent_help": "Up to the 50 most recent persisted events",
    "admin.events.received": "Received",
    "admin.events.description": "Description",
    "admin.events.sender": "Sender",
    "admin.events.classification": "Classification / area",
    "admin.events.status": "Status",
    "admin.events.check_status": "Check status",
    "admin.events.none": "No events have been recorded yet.",
    "admin.events.new_activity": "Submit new activity",
    "admin.events.pending_holds": "Pending decisions",
    "admin.events.notifications": "Notifications",
    "admin.events.no_holds": "There are no pending decisions.",
    "admin.events.resolve": "Resolve",
    "admin.events.approve": "Approve",
    "admin.events.reject": "Reject",
    "admin.events.no_notifications": "There are no new notifications from this cursor.",
    "admin.events.sensor_report": "Report a sensor event",
    "admin.events.sensor_report_help": "Enter the event description exactly as it was received from the sensor.",
    "admin.events.user_message": "Send a user message",
    "admin.events.user_message_help": "Send a message under the selected Telegram identity, optionally in a registered group context.",
    "admin.events.conversation": "Conversation identifier (optional)",
    "admin.events.source_message": "Original message identifier (optional)",
    "admin.events.chat_id": "Telegram chat identifier (optional)",
    "admin.events.chat_type": "Chat type",
    "admin.events.private_chat": "Private conversation",
    "admin.events.group_chat": "Group",
    "admin.events.supergroup_chat": "Supergroup",
    "admin.events.preferred_protocol": "Preferred protocol (optional)",
    "admin.events.related_event": "Related event identifier (optional)",
    "admin.events.find_job": "Find an event or task",
    "admin.events.find_job_help": "Enter an event identifier to retrieve its latest processing status.",
    "admin.events.event_id": "Event identifier",
    "admin.events.live_trace": "Live execution log",
    "admin.events.live_trace_help": "Available when deep diagnostics are enabled for the server.",
    "admin.events.trace_id": "Trace identifier",
    "admin.events.from_cursor": "Start from entry",
    "admin.events.wait_seconds": "Wait time in seconds",
    "admin.events.show_log": "Show log",
    "admin.events.attendance": "Attendance check",
    "admin.events.attendance_help": "Open the scheduled readiness-team attendance cycle.",
    "admin.events.check_time": "Check time (optional)",
    "admin.events.force_check": "Open a cycle even when it is not due",
    "admin.events.start_check": "Start attendance check",
    "admin.events.send_report": "Send report",
    "admin.events.send_message": "Send message",
    "admin.users.api_title": "User API operations",
    "admin.users.self_name_help": "The name endpoint permits an identity to update only its own name.",
    "admin.users.commanders_help": "Returns the commander roster visible to the selected identity.",
    "admin.groups.api_title": "Group API operations",
    "admin.groups.delete_confirm": "Remove this group through the live Groups API?",
    "admin.col_identity": "Telegram identity",
    "admin.col_full_name": "Full name",
    "admin.col_level": "Level",
    "admin.save": "Save",
    "admin.remove": "Remove",
    "admin.add": "Add",
    "admin.tag_bot_service": "bot's own service identity",
    "admin.add_user": "Add a user",
    "admin.confirm_remove_user": "Remove {identity}?",
    "admin.groups_title": "Telegram groups",
    "admin.groups_page_subtitle": "Bind real Telegram group IDs to an agent scope.",
    "admin.groups_subtitle": "Bind each group to its agent and approve groups that were collected automatically.",
    "admin.registration_automatic": "Automatic",
    "admin.registration_approved": "Approved",
    "admin.registration_blocked": "Blocked in safe mode",
    "admin.registration_active": "Active",
    "admin.approve_registration": "Approve",
    "admin.user_approved": "User {identity} was approved.",
    "admin.group_approved": "Group {chat_id} was approved.",
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
    "admin.rename_group": "Change ID",
    "admin.new_chat_id_placeholder": "-1009876543210",
    "admin.group_rename_help": (
        "Replace this group's Telegram chat ID — for example, once a real Telegram group exists "
        "to receive traffic for a simulation group that was provisioned at a placeholder ID."
    ),
    "admin.new_chat_id_required": "A new Telegram chat ID is required.",
    "admin.new_chat_id_invalid": "The new chat ID must be a negative number, like a real Telegram group ID.",
    "admin.group_chat_id_taken": "'{chat_id}' is already used by another group.",
    "admin.group_renamed": "Group '{old_chat_id}' is now '{new_chat_id}'.",
    "admin.identity_required": "A Telegram identity is required.",
    "admin.full_name_invalid": "A full name must be empty or contain at least two words (maximum 120 characters).",
    "admin.level_invalid": "'{level}' is not a valid permission level.",
    "admin.user_written": "'{identity}' is now '{level}'.",
    "admin.user_not_found": "No such user: '{identity}'.",
    "admin.user_removed": "'{identity}' removed.",
    "admin.bot_service_provisioned": "'{identity}' is registered at commander level.",
    "admin.error_title": "Something went wrong",
    "admin.error_subtitle": "Try again, or check the server log.",
    "admin.simulator.title": "Scenario simulator",
    "admin.simulator.subtitle": "Load a scenario and feed its steps to the live system exactly as the bot would, one chat at a time and under each sender's identity.",
    "admin.simulator.drop_zone": "Click to choose a scenario JSON file, or drop it here",
    "admin.simulator.paste_label": "Or paste scenario JSON",
    "admin.simulator.load_pasted": "Load pasted JSON",
    "admin.simulator.mapping_title": "Map real Telegram identities",
    "admin.simulator.mapping_help": "Map every character and group for this load. Registered server values are suggested, but the exact entered IDs are used by the live system and are not saved for the next load.",
    "admin.simulator.apply_mapping": "Load with this mapping",
    "admin.simulator.telegram_id_placeholder": "Positive Telegram ID",
    "admin.simulator.chat_id_placeholder": "Negative group chat ID",
    "admin.simulator.map_person": "{persona} — Telegram ID",
    "admin.simulator.map_group": "{group} — group chat ID",
    "admin.simulator.err_positive_identity": "{persona} must map to a positive Telegram ID.",
    "admin.simulator.err_negative_group": "{group} must map to a negative group chat ID.",
    "admin.simulator.missing_name": "Name missing",
    "admin.simulator.send_next": "Send next step",
    "admin.simulator.reset_view": "Reset view",
    "admin.simulator.no_scenario": "No scenario loaded",
    "admin.simulator.untitled": "Untitled scenario",
    "admin.simulator.badge_id": "ID: {id}",
    "admin.simulator.badge_steps": "{count} steps",
    "admin.simulator.badge_chats": "{count} chats",
    "admin.simulator.badge_expected": "{count} expected actions (display only)",
    "admin.simulator.expected_actions_title": "Expected agent actions (display only)",
    "admin.simulator.expected_action": "After step {step}: {description}",
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
    "admin.simulator.route_unregistered": "Group not registered - the bot ignores it and the server refuses it",
    "admin.simulator.route_sensor": "Sensor event channel",
    "admin.simulator.warn_sender_unregistered": "Sender {identity} is not registered - the server will refuse this step",
    "admin.simulator.system_label": "System",
    "admin.simulator.sending": "Sending...",
    "admin.simulator.event_id": "Event {event_id}",
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
    "admin.simulator.invalid_response": "Invalid simulator response",
    "admin.simulator.network_error": "Network error: {message}",
    "admin.simulator.err_parse": "Could not parse the JSON: {message}",
    "admin.simulator.err_chats_required": "'chats' must be a non-empty list",
    "admin.simulator.err_steps_required": "'steps' must be a non-empty list",
    "admin.simulator.err_chat_key": "Chat #{index} needs a unique string 'key'",
    "admin.simulator.err_chat_kind": "Chat '{key}' has an unknown kind '{kind}' (expected 'message' or 'event')",
    "admin.simulator.err_chat_type": "Chat '{key}' has an invalid telegram_chat_type '{type}' (expected private, group or supergroup)",
    "admin.simulator.err_chat_id_required": "Chat '{key}' is a group and needs a telegram_chat_id",
    "admin.simulator.err_chat_id_negative": "Chat '{key}' needs a negative Telegram group chat ID",
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
    "admin.simulator.profile_simulations": "Profile simulations",
    "admin.simulator.choose_profile_simulation": "Choose a declared simulation",
    "admin.simulator.load_profile_simulation": "Load",
    "admin.simulator.no_profile_simulations": "This profile declares no simulations.",
    "admin.simulator.profile_simulation_load_failed": "Could not load this simulation: {message}",
    "admin.simulator.select_identity_first": "Select an acting identity above to list this profile's simulations.",
    "admin.simulator.bot_mode_unconfigured": "This profile has not declared SIMULATOR_PORT, so message steps cannot be routed through the simulation-mode bot process (docs/bot_simulation_mode_design.md).",
    "admin.simulator.bot_mode_unreachable": "Could not reach the simulation-mode bot process. Is `python -m bot.simulator_app` running for this profile?",
    "admin.simulator.bot_no_reply": "(the bot sent no reply)",
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

    "unified.simulation.commander_name": "Simulated Commander",
    "unified.simulation.viewer_name": "Simulated Viewer",
    "unified.simulation.response_team_label": "Simulated response team",
    "unified.simulation.viewer_dm_label": "Viewer private chat",
    "unified.simulation.overall_picture.title": "Overall situational picture (demo)",
    "unified.simulation.overall_picture.description": (
        "A single-step demo: a simulated viewer privately asks for the overall situational "
        "picture, a LOW-criticality, no-approval-needed protocol that completes immediately."
    ),
    "unified.simulation.overall_picture.step_text": "What is the overall situational picture right now?",

    "unified.simulation.sec001.persona.eli_response_team": "Eli - Response Team",
    "unified.simulation.sec001.persona.yossi_technician": "Yossi - Contract Technician",
    "unified.simulation.sec001.persona.sdemot_security_coordinator": "Sdemot Hub - Neighboring Security Coordinator",
    "unified.simulation.sec001.persona.danny_response_team": "Danny - Response Team",
    "unified.simulation.sec001.persona.site_security_officer": "Site Security Officer",
    "unified.simulation.sec001.persona.michael_response_team": "Michael - Response Team",
    "unified.simulation.sec001.persona.police_duty_officer": "Police Station Duty Officer",
    "unified.simulation.sec001.persona.yuval_response_team": "Yuval - Response Team",
    "unified.simulation.sec001.persona.patrol_unit_40": "Patrol Unit 40",
    "unified.simulation.sec001.persona.gil_response_team": "Gil - Response Team",
    "unified.simulation.sec001.persona.resident_avraham": "Resident - Avraham (Expansion Neighborhood)",
    "unified.simulation.sec001.persona.dan_response_team": "Dan - Response Team",
    "unified.simulation.sec001.persona.mda_dispatch": "MDA Dispatch (Magen David Adom)",
    "unified.simulation.sec001.persona.police_patrol": "Police Patrol",
    "unified.simulation.sec001.persona.yasam_commander": "Special Patrol Unit (YASAM) Commander - Police",
    "unified.simulation.sec001.chat.response_team.label": "Response Team",
    "unified.simulation.sec001.chat.cameras.label": "Camera Hub",
    "unified.simulation.sec001.chat.external_forces.label": "External Forces",
    "unified.simulation.sec001.chat.commander_dm.label": "Private chat with the security officer",
    "unified.simulation.sec001.group.cameras.label": "Camera Hub",
    "unified.simulation.sec001.group.external_forces.label": "External Forces",
    "unified.simulation.sec001.phase1.title": "Readiness Team Incident - Preparation Phase: Routine and Minor Faults",
    "unified.simulation.sec001.phase1.description": "Collecting daily roster data, minor network faults and maintenance, and perimeter reports with no defined threat.",
    "unified.simulation.sec001.phase1.step1.text": "Good morning, just letting you know I'm on reserve duty from Sunday through Tuesday evening - unavailable in the community.",
    "unified.simulation.sec001.phase1.step2.text": "Camera 08 (south corner) is showing intermittent reception interference. Might just be a branch blocking the view or a focus issue.",
    "unified.simulation.sec001.phase1.step3.text": "To the whole sector: a small fire in open ground near the regional access road. Fire and rescue are handling it, no risk to agricultural land.",
    "unified.simulation.sec001.phase1.step4.text": "Guys, I got a new phone. The new number is updated - checking in during tonight's quiet shift.",
    "unified.simulation.sec001.phase1.step5.text": "Good evening, put together a daily summary for me: who's missing from tonight's roster, and what's the status of the perimeter-fence cameras?",
    "unified.simulation.sec001.phase1.step6.text": "I woke up with a high fever, I won't be able to join this evening's patrol.",
    "unified.simulation.sec001.phase1.step7.text": "Camera 03 (east fence, section 4) has been deliberately taken down for two hours for a routine version update.",
    "unified.simulation.sec001.phase1.step8.text": "Sector update: last night an ATV was stolen from our side. High likelihood the thieves moved along the perimeter route.",
    "unified.simulation.sec001.phase1.step9.text": "Show me an updated sector situational picture ahead of tonight.",
    "unified.simulation.sec001.phase2.title": "Readiness Team Incident - Main Escalation Phase: Heating Up and Accumulating Events",
    "unified.simulation.sec001.phase2.description": "The transition from routine to emergency: a series of unusual surveillance faults, police indications of a suspicious vehicle, and identification of physical damage to the perimeter fence requiring a callout while short-staffed.",
    "unified.simulation.sec001.phase2.step1.text": "Good morning. Strange - camera 03, which we took down yesterday for the update, still hasn't come back. Now camera 04 next to it is stuck on a frozen image too.",
    "unified.simulation.sec001.phase2.step2.text": "To the whole area: a report was received of a white commercial vehicle with no license plates, seen moving slowly near your eastern orchards.",
    "unified.simulation.sec001.phase2.step3.text": "Hey guys, I hear heavy equipment being unloaded near the west gate. Is there planned work there today?",
    "unified.simulation.sec001.phase2.step4.text": "Give me a quick picture: do we have anything suspicious in the eastern sector? And what's this talk about the west gate?",
    "unified.simulation.sec001.phase2.step5.text": "Guys, I physically reached camera 03 on the east fence. There's a physically cut communication cable! This is deliberate sabotage, not a network fault!",
    "unified.simulation.sec001.phase2.step6.text": "We've located the white commercial vehicle abandoned in the eastern olive grove, about 150 meters from the community fence. Doors open, vehicle empty. Deploying a K9 unit.",
    "unified.simulation.sec001.phase2.step7.text": "This is a live incident! Cross-reference all the agent information for me immediately, call out the response team, and recommend a force deployment!",
    "unified.simulation.sec001.phase2.step8.text": "Got the callout, leaving home toward the eastern sector. Arriving within 4 minutes. Who else is with me on the team?",
    "unified.simulation.sec001.phase2.step9.text": "Reached section 4 east. I can see a fresh breach in the perimeter fence! Footprints leading inward toward the expansion neighborhood!",
    "unified.simulation.sec001.phase3.title": "Readiness Team Incident - Extreme Crisis Phase: Active Intrusion, Confusion, and Full Lockdown",
    "unified.simulation.sec001.phase3.description": "Reaching the operational edge: suspects breaching the community, false reports causing force dilution, coordinating a complex casualty incident, house-to-house searches, and closing out the incident under an information overload.",
    "unified.simulation.sec001.phase3.step1.text": "Sirens at the east gate! I saw a suspicious figure in the Levi family's yard at 12 Olive Street! He's got something long in his hand!",
    "unified.simulation.sec001.phase3.step2.text": "Wait! Residents are now reporting continuous gunfire near the west gate! I'm running over there!",
    "unified.simulation.sec001.phase3.step3.text": "We received a report of a gunshot casualty at the entrance to the expansion neighborhood! Ambulance en route, requesting response-team security for the medical crew.",
    "unified.simulation.sec001.phase3.step4.text": "I've got total chaos here! Dan is running west because of a gunfire report, MDA is talking about a casualty in the east, and a resident is reporting an armed man in the expansion neighborhood. Sort this out for me right now! Where do I send the available force?!",
    "unified.simulation.sec001.phase3.step5.text": "Clarification: there is no gunfire at the west gate! The reported gunfire was a warning shot fired by our own patrol car in the eastern orchard area. Do not split forces toward the west!",
    "unified.simulation.sec001.phase3.step6.text": "I reached 12 Olive Street in the expansion neighborhood! Linked up with MDA, treating the casualty (a resident injured by glass while fleeing). I saw the suspect flee toward the old public building!",
    "unified.simulation.sec001.phase3.step7.text": "I managed to get a mobile tactical camera up on the office mast! We can see one suspect hiding on the roof of the old public building, holding a dark object in his hand.",
    "unified.simulation.sec001.phase3.step8.text": "A YASAM unit and a K9 (Yamag) unit are now entering the community grounds. Incident command for the takeover will transfer to us the moment we reach the building.",
    "unified.simulation.sec001.phase3.step9.text": "Incident under control! The suspect was arrested on the roof without any shots fired. He turns out to be an intruder who tried to flee after cutting the fence. Being transferred for questioning.",
    "unified.simulation.sec001.phase3.step10.text": "Excellent, the incident is over. Run me a full end-to-end incident summary: timeline, any failures/false reports that occurred, casualty and roster status, and a calming message to distribute to residents.",

    "unified.simulation.fire002.persona.lahav_avi_shift_commander": "Lahav Avi - Shift Commander",
    "unified.simulation.fire002.persona.omri_firefighter": "Sergeant Omri - Firefighter",
    "unified.simulation.fire002.persona.roni_surveillance_operator": "Roni - Surveillance Operator",
    "unified.simulation.fire002.persona.kkl_mountains_sector": "KKL Hub - Mountains Sector",
    "unified.simulation.fire002.persona.police_hub_agam": "Police Hub - Operations Division (Agam)",
    "unified.simulation.fire002.persona.station_commander": "Station Commander",
    "unified.simulation.fire002.persona.yuval_ashed3_commander": "Sergeant Yuval - Ashed 3 Team Commander",
    "unified.simulation.fire002.persona.citizen_reports_group": "Citizen - Reports Group",
    "unified.simulation.fire002.persona.fire_police_patrol": "Police Patrol",
    "unified.simulation.fire002.persona.district_fire_commander": "District Commander - Fire and Rescue Services",
    "unified.simulation.fire002.chat.fire_response_team.label": "Fire Crew",
    "unified.simulation.fire002.chat.fire_cameras.label": "Observation Hub",
    "unified.simulation.fire002.chat.fire_external_forces.label": "External Forces",
    "unified.simulation.fire002.chat.fire_commander_dm.label": "Private chat with the station commander",
    "unified.simulation.fire002.group.fire_response_team.label": "Fire Crew",
    "unified.simulation.fire002.group.fire_cameras.label": "Observation Hub",
    "unified.simulation.fire002.group.fire_external_forces.label": "External Forces",
    "unified.simulation.fire002.phase1.title": "Fire and Rescue Incident - Preparation Phase: Routine, Heat Load, and Equipment Maintenance",
    "unified.simulation.fire002.phase1.description": "Preparing for a heatwave day: managing the shift roster, minor vehicle/equipment faults, and routine reports of small open-area fires.",
    "unified.simulation.fire002.phase1.step1.text": "Good morning. Updating the opening roster: 6 firefighters on Team A, engines Ashed 3 and Carmel 1 fully operational.",
    "unified.simulation.fire002.phase1.step2.text": "Just letting you know I need to leave at 12:00 for a routine medical checkup, back on shift at 15:00.",
    "unified.simulation.fire002.phase1.step3.text": "The temperature sensor and thermal camera at the Oranim observation tower are showing a low-level heat alert due to a severe heatwave and easterly winds.",
    "unified.simulation.fire002.phase1.step4.text": "To all parties: due to the heatwave, we've issued a directive banning open fires in all forests in the area. Forest rangers are conducting patrols.",
    "unified.simulation.fire002.phase1.step5.text": "Camera 02 (quarry junction) has been deliberately taken offline for lens cleaning due to heavy dust.",
    "unified.simulation.fire002.phase1.step6.text": "Report of a small brush fire alongside Route 444, likely from a cigarette. A patrol car is on scene, no risk to structures.",
    "unified.simulation.fire002.phase1.step7.text": "Put together a midday situational picture for me: what's our force and vehicle availability under these heatwave conditions?",
    "unified.simulation.fire002.phase2.title": "Fire and Rescue Incident - Main Escalation Phase: Fire Spread and Dispatch Overload",
    "unified.simulation.fire002.phase2.description": "Escalation under extreme conditions: a brush fire in open ground spreads with the wind toward the forest and industrial buildings, alongside false reports and a thermal camera fault.",
    "unified.simulation.fire002.phase2.step1.text": "Initial smoke detected on camera 05 (Oranim ridge)! Looks like a small fire source in open ground, spreading eastward with the wind.",
    "unified.simulation.fire002.phase2.step2.text": "Dozens of reports are coming in from citizens about thick smoke visible from Route 444. Traffic congestion is developing in the area.",
    "unified.simulation.fire002.phase2.step3.text": "Engine Ashed 3 is en route to the location. Arriving in 4 minutes. Reminder that Omri is at his medical checkup, roster at the station is reduced.",
    "unified.simulation.fire002.phase2.step4.text": "Camera 05 has gone into thermal confusion due to heavy smoke and glare. The lens is frozen and can't be repositioned remotely.",
    "unified.simulation.fire002.phase2.step5.text": "Report from the field: the fire jumped over a dirt path and entered the tree line! There's a real risk of it spreading toward the industrial park.",
    "unified.simulation.fire002.phase2.step6.text": "We're seeing rapid spread inside the forest. Deploying 2 of our own firefighting tractors - requesting sector coordination with you.",
    "unified.simulation.fire002.phase2.step7.text": "Show me an urgent situational picture: what's the exact fire location, what's the status of the crews in the field, and what do the agents recommend regarding an emergency callout?",
    "unified.simulation.fire002.phase3.title": "Fire and Rescue Incident - Extreme Crisis Phase: Hazmat Threat, Resident Evacuation, and Critical Overload",
    "unified.simulation.fire002.phase3.description": "Reaching the operational edge: the fire approaches a hazardous-materials plant in the industrial park, an order to evacuate the first row of houses, false reports of people trapped, and managing national/district-level roster resources.",
    "unified.simulation.fire002.phase3.step1.text": "Emergency! The flames on the northern front have crossed the access road and reached the fence of the 'Chemi-Kal' plant. There's a gas tank and ammonia containers in the yard there!",
    "unified.simulation.fire002.phase3.step2.text": "Beginning immediate evacuation of the first row of houses on Oranim Street due to thick, toxic smoke! Requesting guidance on additional road closures.",
    "unified.simulation.fire002.phase3.step3.text": "There are two children trapped on the roof of the building at 14 Oranim Street! The bars are locked, the smoke is coming inside!",
    "unified.simulation.fire002.phase3.step4.text": "We have two critical emergency hotspots: hazmat at the plant versus people trapped in the houses! Our roster is still short-staffed. Prioritize the response and water/crew allocation for me right now!",
    "unified.simulation.fire002.phase3.step5.text": "Check at 14 Oranim Street: the house is empty! The children were evacuated earlier by their parents. The trapped-persons report is a false alarm!",
    "unified.simulation.fire002.phase3.step6.text": "A tactical drone camera shows the fire is now touching the plant's external gas tank. An immediate cooling water curtain is required!",
    "unified.simulation.fire002.phase3.step7.text": "Dispatching 4 Alon tanker trucks (water tankers) from a neighboring station plus 2 firefighting aircraft to you. Skies are clear for aerial spraying.",
    "unified.simulation.fire002.phase3.step8.text": "The district reinforcements have arrived! We've set up a water curtain around the gas tanks and the flames at the plant have been contained. No hazmat leak.",
    "unified.simulation.fire002.phase3.step9.text": "The incident is contained. Put together a preliminary debrief for me: timeline, resource management, identification of the false reports, and guidance for returning residents to their homes.",

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
    "unified.protocol.dispatch_mutual_aid.description": (
        "Requesting fire-service mutual aid for an active fire: water-tanker trucks from another station, "
        "or firefighting aircraft. Commander-only action requiring approval. Only a fire-and-rescue "
        "organization operates this capability."
    ),
    "unified.protocol.dispatch_mutual_aid.expected_output": "Confirmation that the requested mutual-aid tankers or aircraft were recorded for the named location.",
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

    "orchestrator.picture.default_domain_query": (
        "Report the current state of your whole area of responsibility right now: counts, statuses, names, "
        "identifiers, locations, and any anomaly. Answer only from your tools' data."
    ),
    "orchestrator.picture.recent_events_question": (
        "Which events were recorded in the last {hours} hours? For each event state its time, classification, "
        "area, protocol, and outcome."
    ),
    "orchestrator.picture.no_recent_events": "No events were recorded in the last {hours} hours.",
    "orchestrator.picture.recent_events_label": "Recent events (last {hours} hours)",
    "orchestrator.picture.domain_unavailable": "No report was received from {domain}.",
    "orchestrator.picture.fallback_header": "Situational picture as of {time}:",
    "orchestrator.picture.missing_note": "(Operational note: no report was received from {domains})",
    "orchestrator.picture.typed.title": "Situational picture",
    "orchestrator.picture.typed.cameras": (
        "Cameras: {active}/{total} active; {degraded} degraded; {offline} offline; {unknown} unknown."
    ),
    "orchestrator.picture.typed.cameras_unknown": "Cameras: data unavailable.",
    "orchestrator.picture.typed.drones": (
        "Drones: {ready} ready; {airborne} airborne; {charging} charging; "
        "{maintenance} in maintenance; {unknown} unknown; {active_missions} active missions; {total} total."
    ),
    "orchestrator.picture.typed.drones_unknown": "Drones: data unavailable.",
    "orchestrator.picture.typed.team": (
        "{roster}: {available} available; {unavailable} unavailable; "
        "{not_reported} not reported; {pending_identity} pending identity; {total} total."
    ),
    "orchestrator.picture.typed.team_unknown": "{roster}: data unavailable.",
    "orchestrator.picture.typed.manpower": (
        "Manpower: {effective} of {reported} reported personnel currently available."
    ),
    "orchestrator.picture.typed.resources": "Resources: {resources}.",
    "orchestrator.picture.typed.findings_header": "Operational findings:",
    "orchestrator.picture.typed.recommendations_header": "Recommended next actions:",
    "orchestrator.picture.typed.finding_line": "- {text}",
    "orchestrator.picture.typed.recommendation_line": "- {text}",
    "orchestrator.picture.typed.recent_reports_header": "Recent committed reports:",
    "orchestrator.picture.typed.recent_report": "- {text}",
    "orchestrator.picture.reasoned.title": "Commander SITREP",
    "orchestrator.picture.reasoned.facts_header": "Verified facts:",
    "orchestrator.picture.reasoned.assessments_header": "Operational assessment:",
    "orchestrator.picture.reasoned.recommendations_header": "Recommended actions:",
    "orchestrator.picture.reasoning.execution_markers": "executed|completed|dispatched|activated|sent|approved",
    "orchestrator.picture.finding.cameras_all_active": (
        "Camera coverage is normal: all {count} cameras are active."
    ),
    "orchestrator.picture.finding.cameras_gap": (
        "Camera coverage has a gap: {active} of {total} cameras are active."
    ),
    "orchestrator.picture.finding.cameras_degraded": "{count} camera(s) are degraded; coverage is reduced.",
    "orchestrator.picture.finding.cameras_offline": "{count} camera(s) are offline or in maintenance.",
    "orchestrator.picture.finding.cameras_unknown": "Camera coverage cannot currently be verified.",
    "orchestrator.picture.finding.cameras_inconsistent": "Camera state data is inconsistent.",
    "orchestrator.picture.finding.drones_ready": "Air capability is available: {count} drones are ready.",
    "orchestrator.picture.finding.drones_none_ready": "No drone is currently marked ready.",
    "orchestrator.picture.finding.drones_unknown": "Drone readiness cannot currently be verified.",
    "orchestrator.picture.finding.drones_inconsistent": (
        "Drone and active-mission data are inconsistent; operational readiness is not inferred."
    ),
    "orchestrator.picture.finding.team_available": "Confirmed available team members: {count}.",
    "orchestrator.picture.finding.team_no_confirmed": "There is currently no confirmed available team member.",
    "orchestrator.picture.finding.team_not_reported": "Availability reports are missing from {count} team members.",
    "orchestrator.picture.finding.team_unknown": "Team availability cannot currently be verified.",
    "orchestrator.picture.finding.team_pending_identity": "Availability for {count} team member(s) is pending identity verification.",
    "orchestrator.picture.finding.team_inconsistent": (
        "Team availability data is inconsistent; readiness is not inferred."
    ),
    "orchestrator.picture.recommendation.collect_availability": (
        "Complete availability reporting for the {count} team members who have not reported."
    ),
    "orchestrator.follow_up.marker.why": "why",
    "orchestrator.follow_up.marker.what_happened": "what happened",
    "orchestrator.follow_up.marker.was_executed": "was it done",
    "orchestrator.follow_up.marker.did_execute": "did it execute",
    "orchestrator.follow_up.marker.why_failed": "why did it fail",
    "orchestrator.follow_up.marker.what_now": "what now",
    "orchestrator.follow_up.marker.yes": "yes",
    "orchestrator.follow_up.marker.approve": "approve",
    "orchestrator.follow_up.marker.approved": "approved",
    "orchestrator.follow_up.prefix.why": "why ",
    "orchestrator.follow_up.prefix.what": "what ",
    "failure.deadline_expired": "The event expired before processing could finish.",
    "failure.approval_expired": "The approval window expired; the action was not executed.",
    "failure.required_event_data_expired": "The event-data window expired; the event was not executed.",
    "failure.clarification_expired": "The clarification window expired; the event was not executed.",
    "failure.structured_unavailable": "The structured response was unavailable, so no operational state was changed.",
    "failure.unknown_entity": "The referenced operational entity was not found, so no state was changed.",
    "failure.missing_details": "Required operational details are missing, so the report was not committed.",
    "failure.validation": "The report did not pass operational validation, so no state was changed.",
    "orchestrator.follow_up.prefix.this": "",

    # Bilingual extraction vocabulary is stored here as canonical matching
    # patterns; it is not rendered directly to users.
    "profile.response_team.name": "Response team",
    "profile.response_team.roster": "Readiness team",
    "profile.response_team.member": "Team member",
    "profile.fire_station.name": "Fire station",
    "profile.fire_station.roster": "Fire crew",
    "profile.fire_station.member": "Firefighter",
    "profile.fire_station.resource.ashed": "Ashed",
    "profile.fire_station.resource.carmel": "Carmel",
    "profile.fire_station.resource.ashed.pattern": r"(?:אשד|ashed)\s*(\d+)",
    "profile.fire_station.resource.carmel.pattern": r"(?:כרמל|carmel)\s*(\d+)",
    "extraction.friendly_forces.fire_ban": r"איסור הדלקת|איסור אש|fire ban|no.?burn|fire.?lighting prohibition",
    "extraction.friendly_forces.forests": r"יערות|יער|forests?",
    "extraction.friendly_forces.rangers": r"יערנים|rangers?",
    "extraction.friendly_forces.heatwave": r"שרב|גל חום|heatwave|heat wave",
    "extraction.friendly_forces.fire_incident": r"שריפה|שריפת|\bfire\b",
    "extraction.friendly_forces.brush_fire": r"שריפת קוצים|brush fire",
    "extraction.friendly_forces.size_small": r"קטנה|קטן|\bsmall\b",
    "extraction.friendly_forces.size_large": r"גדולה|גדול|\blarge\b|\bmajor\b",
    "extraction.friendly_forces.route_number": r"(?:כביש|route|highway)\s*(\d{{1,4}})\b",
    "extraction.friendly_forces.cigarette": r"סיגריה|מסיגריה|cigarette",
    "extraction.friendly_forces.hedged": r"כנראה|ייתכן|חשד|possibly|likely|suspected|probably",
    "extraction.friendly_forces.police_patrol": r"ניידת|משטרה|police|patrol car",
    "extraction.friendly_forces.firefighters": r"כיבוי|כבאים|firefighters?|fire crews?",
    "extraction.friendly_forces.no_building_risk": r"אין סיכון\s+(?:ל|למ)?מבנים|no risk to buildings|no structures? at risk",

    "extraction.surveillance.camera_reference": r"(?:cam[-\s]?|camera\s*|מצלמה\s*(?:cam[-\s]?)?)(\d{{1,3}})\b",
    "extraction.surveillance.camera_active": r"חזרה לפעול|שבה לפעול|עלתה חזרה|back online|restored|is back up",
    "extraction.surveillance.camera_offline": r"הופסק|הורדה|הורדנו|נותק|כבתה|לא משדרת|אינה משדרת|offline|shut down|shutdown|taken down|went dark",
    "extraction.surveillance.camera_degraded": r"הפרעות|תקועה|מטושטש|קפאה|לסירוגין|לפרקים|בלבול תרמי|interference|degraded|stuck|frozen|blurred|intermittent",
    "extraction.surveillance.planned_shutdown": r"יזומי|יזומה|מתוכננ|תחזוק|ניקוי|עדכון גרס|planned|scheduled|maintenance|cleaning|version update",
    "extraction.surveillance.downtime_two_hours": r"לשעתיים|שעתיים|two hours",
    "extraction.surveillance.downtime_hours": r"(\d+(?:\.\d+)?)\s*(?:שעות|hours?)",
    "extraction.surveillance.downtime_one_hour": r"לשעה|one hour|an hour",
    "extraction.surveillance.heat_alert": r"התראת חום|התרעת חום|heat alert|heat warning",
    "extraction.surveillance.severity_low": r"נמוכ|\blow\b",
    "extraction.surveillance.severity_high": r"גבוה|\bhigh\b|\bsevere\b",
    "extraction.surveillance.temperature_sensor": r"חיישן טמפרטורה|temperature sensor",
    "extraction.surveillance.thermal_camera": r"מצלמה תרמית|thermal camera",

    "extraction.team_status.manpower_count": r"(\d+)\s*(?:כבאים|לוחמים|אנשים|firefighters?|personnel|members?)",
    "extraction.team_status.medical_check": r"בדיקה רפואית|בדיקה תקופתית|medical check|medical exam|checkup",
    "extraction.team_status.reserve_duty": r"מילואים|reserve duty",
    "extraction.team_status.illness": r"חום גבוה|חולה|מחלה|חולהני|fever|\bsick\b|illness",
    "extraction.team_status.leave": r"חופשה|חופש|on leave|vacation",

    "extraction.supersession.prior_report_reference": r"הדיווח|דיווח קודם|שדווח|שדווחה|שדווחו|previous report|earlier report|the report (?:about|regarding|of)|prior report|reported earlier",
    "extraction.supersession.negation_or_cancellation": r"אין\b|בוטל|מבוטל|בטל|ביטול|\bthere is no\b|\bthere are no\b|\bno\s+\S+\s+at\b|\bcancel|\bretract",
    "extraction.supersession.falsity_statement": r"דיווח שווא|דיווחי שווא|אזעקת שווא|סרק\b|התברר כשגוי|אינו נכון|לא נכון|false report|false alarm|unfounded|disregard|stand down",
    "extraction.supersession.clarification_marker": r"הבהרה|תיקון|מתקן|clarification|correction|to clarify",
    "extraction.supersession.token": r"[\w֐-׿]{{3,}}",
    "extraction.supersession.stopwords": "את|על|של|זה|הוא|היא|אני|אנחנו|יש|אין|לא|כן|הבהרה|דיווח|the|and|for|with|that|this|there|report|reported|clarification|correction|from|our|are|was|were|not",
}
