"""English field catalog for event-history responses (docs/Next_Plan.md §4.6, §9).

Response-layer metadata only — never persistence schema. Defines what each
persisted `events`/`event_steps` field (persistence/schema.py) means in
English, for the History Agent to explain faithfully, and which fields are
internal plumbing that must never enter a narrative answer, for any caller,
merely because it exists.
"""

from history.contracts import EventFieldDefinition

EVENT_FIELD_CATALOG: tuple[EventFieldDefinition, ...] = (
    EventFieldDefinition(
        "event_id", "Event ID",
        "The event's stable, unique identifier — always state it explicitly so this event can be referenced again later.",
        "narrative",
    ),
    EventFieldDefinition(
        "received_at", "Received at",
        "When AgentsHub received this report — not necessarily when it happened.",
        "narrative",
    ),
    EventFieldDefinition(
        "occurred_at", "Occurred at",
        "When the event is believed to have actually occurred.",
        "narrative",
    ),
    EventFieldDefinition(
        "occurred_at_is_fallback", "Occurrence time is a fallback",
        "True means no occurrence time could be extracted from the report, so the received time was substituted — "
        "say so if asked when it happened, never present it as a confirmed occurrence time.",
        "narrative",
    ),
    EventFieldDefinition(
        "raw_text", "Original report text",
        "The original, unedited text as submitted.",
        "narrative",
    ),
    EventFieldDefinition(
        "description", "Extracted description",
        "The operational description extracted from the raw report — distinct from the raw text itself.",
        "narrative",
    ),
    EventFieldDefinition(
        "classification", "Classification",
        "The event type this report was classified as. Empty means a clarification hold is what resolves it.",
        "narrative",
    ),
    EventFieldDefinition(
        "area", "Area",
        "The location/area the event was classified against.",
        "narrative",
    ),
    EventFieldDefinition(
        "entities", "Entities",
        "Named entities extracted from the report.",
        "narrative",
    ),
    EventFieldDefinition(
        "severity", "Severity",
        "The extracted severity of the event.",
        "narrative",
    ),
    EventFieldDefinition(
        "risk_level", "Risk level",
        "The assessed risk level (high/low) that determined whether the run needed commander approval.",
        "narrative",
    ),
    EventFieldDefinition(
        "risk_reason", "Risk reason",
        "The evidence behind the assessed risk level.",
        "narrative",
    ),
    EventFieldDefinition(
        "selected_protocol", "Selected protocol",
        "The handling protocol chosen for this event.",
        "narrative",
    ),
    EventFieldDefinition(
        "protocol_reason", "Protocol selection reason",
        "Why this protocol was chosen over any other candidate.",
        "narrative",
    ),
    EventFieldDefinition(
        "clarification_held", "Clarification held",
        "Whether this event was held for a commander to resolve a classification clarification.",
        "narrative",
    ),
    EventFieldDefinition(
        "clarification_unresolved_field", "Unresolved field",
        "Which field could not be resolved automatically and needed a commander's clarification.",
        "narrative",
    ),
    EventFieldDefinition(
        "clarification_resolved_by", "Clarification resolved by",
        "Which commander resolved the clarification hold.",
        "narrative",
    ),
    EventFieldDefinition(
        "clarification_chosen_classification", "Chosen classification",
        "The classification a commander chose to resolve the clarification hold.",
        "narrative",
    ),
    EventFieldDefinition(
        "approval_held", "Approval held",
        "Whether this event was held for a commander's approval before running.",
        "narrative",
    ),
    EventFieldDefinition(
        "approval_reason", "Approval hold reason",
        "Why the run was held for approval: a flagged protocol, or an ambiguous protocol selection.",
        "narrative",
    ),
    EventFieldDefinition(
        "approval_answered_by", "Approved/rejected by",
        "Which commander answered the approval hold.",
        "narrative",
    ),
    EventFieldDefinition(
        "approval_answered_at", "Approval answered at",
        "When the approval hold was answered.",
        "narrative",
    ),
    EventFieldDefinition(
        "precedent_matched_event_ids", "Matched precedent events",
        "Prior events that matched this one's classification and area within the lookback window.",
        "narrative",
    ),
    EventFieldDefinition(
        "precedent_closed_by_event_id", "Closed on precedent",
        "The prior, already-resolved event that let this one close without running its own protocol.",
        "narrative",
    ),
    EventFieldDefinition(
        "insight_text", "Insight",
        "The final synthesized conclusion about this event's run.",
        "narrative",
    ),
    EventFieldDefinition(
        "outcome", "Outcome",
        "The final persisted result of this event: succeeded, failed, uncertain, closed_on_precedent, declined, "
        "or no_match_protocol. Absent/None means the run has not finished yet.",
        "narrative",
    ),
    EventFieldDefinition(
        "outcome_failure_reason", "Failure reason",
        "Why the run failed, or why no protocol matched, when the outcome indicates either.",
        "narrative",
    ),
    EventFieldDefinition(
        "steps", "Executed steps",
        "The ordered specialist tasks that ran for this event, the tools each was allowed to use, and each step's "
        "result.",
        "narrative",
    ),
    # Internal plumbing — never enters a narrative answer, for any caller,
    # merely because it exists (docs/Next_Plan.md §9's field list).
    EventFieldDefinition("trace_id", "Trace ID", "Internal request-tracing identifier.", "internal"),
    EventFieldDefinition("conversation_id", "Conversation ID", "Internal conversation-memory key.", "internal"),
    EventFieldDefinition("deadline_at", "Deadline", "Internal processing deadline.", "internal"),
    EventFieldDefinition("source", "Source", "Internal ingestion channel identity.", "internal"),
    EventFieldDefinition("sender_identity", "Sender identity", "Internal ingestion identity.", "internal"),
    EventFieldDefinition("source_message_id", "Source message ID", "Internal ingestion identity.", "internal"),
)

FIELD_BY_KEY: dict[str, EventFieldDefinition] = {definition.key: definition for definition in EVENT_FIELD_CATALOG}

# Every field label paired with its plain-English meaning — passed to the
# History Agent as a stable reference glossary so it can explain a field's
# meaning faithfully without inventing database semantics.
FIELD_MEANINGS_GLOSSARY: dict[str, str] = {
    definition.label: definition.meaning
    for definition in EVENT_FIELD_CATALOG
    if definition.category == "narrative"
}
