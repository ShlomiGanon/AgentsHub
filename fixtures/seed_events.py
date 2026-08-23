"""Seed dataset (work_plan.md §2.12).

Fixture events spanning several months as **completed historical
records** — each already classified, risk-assessed, protocol-assigned,
stepped, and outcomed, as if the database were the product of months of
running rather than a queue of things still to process.

Classifications/areas match `fixtures/profiles/minimal_profile.py`
("fire", "medical", "human_activation" / "north_sector", "south_sector").
All timestamps sit around a fixed reference "now" of 2026-08-20T12:00:00,
against a typical 30-day lookback window, so which records fall inside vs.
outside that window is deliberate, not incidental — see the comments
below.

Every event dict here is shaped exactly like what
`persistence.interface.append_event` accepts (including a `"steps"` key,
upserted into `event_steps` in the same call).
"""

REFERENCE_NOW = "2026-08-20T12:00:00"

SEED_EVENTS: list[dict] = [
    # -- Repeated classification+area: fire / north_sector -----------------
    # Three occurrences of the same pair, spaced so precedent search
    # (typical 30-day window) finds two of them and misses the third.
    {
        "event_id": "seed-fire-north-1",
        "received_at": "2026-08-18T09:01:00",
        "source": "sensor",
        "sender_identity": "sensor-gate-3",
        "occurred_at": "2026-08-18T09:00:00",  # 2 days before REFERENCE_NOW — inside window
        "occurred_at_is_fallback": False,
        "raw_text": "Smoke detected near gate 3, moderate density, no visible flame yet.",
        "classification": "fire",
        "area": "north_sector",
        "entities": ["gate-3"],
        "description": "Smoke reported at gate 3, no flame observed",
        "severity": "moderate",
        "risk_level": "high",
        "risk_reason": "unattended smoke source near a perimeter access point",
        "selected_protocol": "basic_response",
        "protocol_reason": "single reference-agent status check is sufficient for a moderate smoke report",
        "precedent_matched_event_ids": [],
        "insight_text": "Smoke resolved to a contained equipment fault; no further action needed.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status at gate 3.", "allowed_tools": ["check_status"], "result_text": "Equipment fault, contained, no fire.", "attempt_count": 1},
        ],
    },
    {
        "event_id": "seed-fire-north-2",
        "received_at": "2026-07-28T09:06:00",
        "source": "sensor",
        "sender_identity": "sensor-gate-3",
        "occurred_at": "2026-07-28T09:05:00",  # 23 days before REFERENCE_NOW — inside a 30-day window
        "occurred_at_is_fallback": False,
        "raw_text": "Smoke alarm at gate 3 again, similar to last time.",
        "classification": "fire",
        "area": "north_sector",
        "entities": ["gate-3"],
        "description": "Second smoke report at gate 3",
        "severity": "moderate",
        "risk_level": "high",
        "risk_reason": "recurrence of a prior smoke source at the same access point",
        "selected_protocol": "basic_response",
        "protocol_reason": "matches the same low-complexity smoke-check pattern as the prior occurrence",
        "precedent_matched_event_ids": ["seed-fire-north-1"],
        "insight_text": "Same equipment fault as the prior occurrence; flagged for maintenance.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status at gate 3.", "allowed_tools": ["check_status"], "result_text": "Same equipment fault as before.", "attempt_count": 1},
        ],
    },
    {
        "event_id": "seed-fire-north-3",
        "received_at": "2026-06-01T09:00:00",
        "source": "sensor",
        "sender_identity": "sensor-gate-3",
        "occurred_at": "2026-06-01T09:00:00",  # ~80 days before REFERENCE_NOW — outside a 30-day window (the near-miss)
        "occurred_at_is_fallback": False,
        "raw_text": "Smoke alarm at gate 3, unrelated older incident.",
        "classification": "fire",
        "area": "north_sector",
        "entities": ["gate-3"],
        "description": "Older smoke report at gate 3, outside the typical lookback window",
        "severity": "moderate",
        "risk_level": "high",
        "risk_reason": "unattended smoke source near a perimeter access point",
        "selected_protocol": "basic_response",
        "protocol_reason": "single reference-agent status check is sufficient for a moderate smoke report",
        "precedent_matched_event_ids": [],
        "insight_text": "Resolved as a one-off sensor fault.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status at gate 3.", "allowed_tools": ["check_status"], "result_text": "Sensor fault, no fire.", "attempt_count": 1},
        ],
    },

    # -- Unresolved prior event ----------------------------------------------
    # Seen but never concluded — closure must refuse to rely on this as a
    # precedent even though it matches on classification+area+window.
    {
        "event_id": "seed-fire-north-unresolved",
        "received_at": "2026-08-10T14:01:00",
        "source": "sensor",
        "sender_identity": "sensor-gate-3",
        "occurred_at": "2026-08-10T14:00:00",  # 10 days before REFERENCE_NOW — inside window
        "occurred_at_is_fallback": False,
        "raw_text": "Faint smoke smell reported near gate 3, unconfirmed.",
        "classification": "fire",
        "area": "north_sector",
        "entities": ["gate-3"],
        "description": "Unconfirmed smoke smell at gate 3",
        "severity": "low",
        "risk_level": "low",
        "risk_reason": "unconfirmed report, no visual or sensor corroboration",
        "selected_protocol": "basic_response",
        "protocol_reason": "single reference-agent status check pending",
        "precedent_matched_event_ids": [],
        "insight_text": None,
        "outcome": None,  # deliberately unresolved — no run outcome was ever recorded
    },

    # -- Contradictory pair: same occurrence, disagreeing reports -----------
    {
        "event_id": "seed-medical-south-contradiction-a",
        "received_at": "2026-08-15T20:10:00",
        "source": "sensor",
        "sender_identity": "sensor-south-1",
        "occurred_at": "2026-08-15T20:05:00",
        "occurred_at_is_fallback": False,
        "raw_text": "One person with a minor injury near the south gate, self-reported as okay.",
        "classification": "medical",
        "area": "south_sector",
        "entities": ["south-gate"],
        "description": "One person, minor injury, ambulatory",
        "severity": "low",
        "risk_level": "low",
        "risk_reason": "single ambulatory casualty, self-reported as stable",
        "selected_protocol": "basic_response",
        "protocol_reason": "low-severity single-casualty check",
        "precedent_matched_event_ids": [],
        "insight_text": "Confirmed minor injury, casualty declined further assistance.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status near the south gate.", "allowed_tools": ["check_status"], "result_text": "One person, minor scrape, declined assistance.", "attempt_count": 1},
        ],
    },
    {
        "event_id": "seed-medical-south-contradiction-b",
        "received_at": "2026-08-15T20:14:00",
        "source": "telegram",
        "sender_identity": "viewer-42",
        "occurred_at": "2026-08-15T20:05:00",
        "occurred_at_is_fallback": False,
        "raw_text": "Two people hurt at the south gate, one looks serious, needs help now!",
        "classification": "medical",
        "area": "south_sector",
        "entities": ["south-gate"],
        "description": "Two people, one serious injury reported",
        "severity": "high",
        "risk_level": "high",
        "risk_reason": "conflicting report of multiple casualties including one serious",
        "selected_protocol": "basic_response",
        "protocol_reason": "escalated pending confirmation of casualty count and severity",
        "precedent_matched_event_ids": [],
        "insight_text": "Independent report disagrees with the sensor account on casualty count and severity; both retained rather than reconciled.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status near the south gate.", "allowed_tools": ["check_status"], "result_text": "Only one person found, minor injury — could not confirm a second casualty.", "attempt_count": 1},
        ],
    },

    # -- Late-arriving Telegram report: occurred before it was received -----
    {
        "event_id": "seed-fire-south-late-report",
        "received_at": "2026-08-20T08:00:00",
        "source": "telegram",
        "sender_identity": "viewer-7",
        "occurred_at": "2026-08-19T22:00:00",  # reported the morning after it happened
        "occurred_at_is_fallback": False,
        "raw_text": "Forgot to report last night — saw a small fire near the south fence around 10pm, it went out on its own.",
        "classification": "fire",
        "area": "south_sector",
        "entities": ["south-fence"],
        "description": "Small self-extinguished fire near the south fence, reported the next morning",
        "severity": "low",
        "risk_level": "low",
        "risk_reason": "self-extinguished, reported after the fact with no ongoing danger",
        "selected_protocol": "basic_response",
        "protocol_reason": "low-severity after-the-fact report needing only a status check",
        "precedent_matched_event_ids": [],
        "insight_text": "No residual risk found at the reported location.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status at the south fence.", "allowed_tools": ["check_status"], "result_text": "No sign of fire or damage.", "attempt_count": 1},
        ],
    },

    # -- Partial report: extraction plausibly left fields empty --------------
    {
        "event_id": "seed-medical-partial",
        "received_at": "2026-08-12T11:00:00",
        "source": "telegram",
        "sender_identity": "viewer-3",
        "occurred_at": "2026-08-12T10:55:00",
        "occurred_at_is_fallback": False,
        "raw_text": "someone's not feeling well near the mess hall, not sure how bad",
        "classification": "medical",
        "area": None,  # extraction could not resolve a location
        "entities": None,
        "description": "Person feeling unwell, location and severity unclear",
        "severity": None,
        "risk_level": "low",
        "risk_reason": "no indication of a serious condition, though details are sparse",
        "selected_protocol": "basic_response",
        "protocol_reason": "minimal information available, defaulting to a status check",
        "precedent_matched_event_ids": [],
        "insight_text": "Could not corroborate further; recommended a follow-up if symptoms persist.",
        "outcome": "uncertain",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status near the mess hall.", "allowed_tools": ["check_status"], "result_text": "No one found matching the description.", "attempt_count": 1},
        ],
    },

    # -- Resolved clarification hold: text no classification fit -----------
    {
        "event_id": "seed-clarification-resolved",
        "received_at": "2026-08-05T16:00:00",
        "source": "telegram",
        "sender_identity": "viewer-9",
        "occurred_at": "2026-08-05T15:50:00",
        "occurred_at_is_fallback": False,
        "raw_text": "something's going on near the old warehouse, not sure what, looks weird",
        "classification": "medical",  # final classification, chosen by the commander below
        "area": "south_sector",
        "entities": ["old-warehouse"],
        "description": "Ambiguous report near the old warehouse, resolved by a commander as a medical check",
        "severity": None,
        "risk_level": "low",
        "risk_reason": "no clear indication of danger once classified",
        "selected_protocol": "basic_response",
        "protocol_reason": "minimal information, defaulting to a status check",
        "clarification_held": True,
        "clarification_unresolved_field": "classification",
        "clarification_resolved_by": "commander-1",
        "clarification_chosen_classification": "medical",
        "precedent_matched_event_ids": [],
        "insight_text": "Resolved as a false alarm once classified and checked.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status near the old warehouse.", "allowed_tools": ["check_status"], "result_text": "Nothing found, false alarm.", "attempt_count": 1},
        ],
    },

    # -- Closed on precedent --------------------------------------------------
    {
        "event_id": "seed-closed-on-precedent",
        "received_at": "2026-08-19T09:00:00",
        "source": "sensor",
        "sender_identity": "sensor-gate-3",
        "occurred_at": "2026-08-19T08:59:00",
        "occurred_at_is_fallback": False,
        "raw_text": "Smoke alarm at gate 3 again.",
        "classification": "fire",
        "area": "north_sector",
        "entities": ["gate-3"],
        "description": "Fourth smoke report at gate 3, matches known equipment fault",
        "severity": "moderate",
        "risk_level": "low",
        "risk_reason": "matches a well-established resolved precedent at the same location",
        "selected_protocol": None,
        "protocol_reason": None,
        "precedent_matched_event_ids": ["seed-fire-north-1", "seed-fire-north-2"],
        "precedent_closed_by_event_id": "seed-fire-north-2",
        "insight_text": None,
        "outcome": "closed_on_precedent",
    },

    # -- Failed run -------------------------------------------------------
    {
        "event_id": "seed-failed-run",
        "received_at": "2026-08-14T13:00:00",
        "source": "sensor",
        "sender_identity": "sensor-south-2",
        "occurred_at": "2026-08-14T12:55:00",
        "occurred_at_is_fallback": False,
        "raw_text": "Unusual reading from the south sector perimeter sensor, cause unclear.",
        "classification": "fire",
        "area": "south_sector",
        "entities": ["south-perimeter-sensor"],
        "description": "Unexplained sensor reading, cause never determined",
        "severity": "moderate",
        "risk_level": "high",
        "risk_reason": "unexplained anomaly on a perimeter sensor",
        "selected_protocol": "basic_response",
        "protocol_reason": "status check needed to investigate the anomaly",
        "precedent_matched_event_ids": [],
        "insight_text": None,
        "outcome": "failed",
        "outcome_failure_reason": "reference agent exhausted its retry limit without a usable result",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status at the south perimeter sensor.", "allowed_tools": ["check_status"], "result_text": None, "attempt_count": 3},
        ],
    },

    # -- Human activation ------------------------------------------------
    {
        "event_id": "seed-human-activation-approved",
        "received_at": "2026-08-17T22:00:00",
        "source": "telegram",
        "sender_identity": "commander-2",
        "occurred_at": "2026-08-17T22:00:00",
        "occurred_at_is_fallback": False,
        "raw_text": "Send someone to check the south gate right now, I have a bad feeling about it.",
        "classification": "human_activation",
        "area": "south_sector",
        "entities": ["south-gate"],
        "description": "Commander-requested precautionary check of the south gate",
        "severity": None,
        "risk_level": "low",
        "risk_reason": "precautionary request with no specific indication of danger",
        "selected_protocol": "basic_response",
        "protocol_reason": "commander request for a status check",
        "precedent_matched_event_ids": [],
        "insight_text": "Nothing found; precautionary check closed out.",
        "outcome": "succeeded",
        "steps": [
            {"step_index": 0, "agent_name": "reference_agent", "task_text": "Check status at the south gate.", "allowed_tools": ["check_status"], "result_text": "All clear.", "attempt_count": 1},
        ],
    },
    {
        "event_id": "seed-human-activation-declined",
        "received_at": "2026-08-16T18:00:00",
        "source": "telegram",
        "sender_identity": "viewer-5",
        "occurred_at": "2026-08-16T18:00:00",
        "occurred_at_is_fallback": False,
        "raw_text": "Can someone dispatch the side-effecting response tool at the north gate for me?",
        "classification": "human_activation",
        "area": "north_sector",
        "entities": ["north-gate"],
        "description": "Viewer request for a side-effecting action, declined at approval",
        "severity": None,
        "risk_level": "low",
        "risk_reason": "viewer-originated request for a flagged, side-effecting protocol",
        "selected_protocol": "side_effecting_response",
        "protocol_reason": "matches a request for the side-effecting action explicitly",
        "approval_held": True,
        "approval_reason": "flagged_protocol",
        "approval_answered_by": "commander-1",
        "approval_answered_at": "2026-08-16T18:05:00",
        "precedent_matched_event_ids": [],
        "insight_text": None,
        "outcome": "declined",
    },
]


def load_seed_dataset(persistence) -> None:
    """Write every SEED_EVENTS record into `persistence` via append_event.

    `persistence` is anything satisfying persistence.interface — this
    function references no engine specifics, consistent with §2.11.
    """

    for event in SEED_EVENTS:
        persistence.append_event(event)
