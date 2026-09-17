"""Response authority boundary regressions (task 30)."""

from bot.contracts import JobResult
from bot.interactions import format_job_result
from orchestrator.response_contract import (
    ResponseClaim,
    ResponseEnvelope,
    capability_response,
    permission_response,
    render_response,
    validate_response,
)


FALLBACK = "This is outside the system's available capabilities."


def test_unsupported_pythagoras_capability_is_refusal_only():
    response = capability_response("The formula is a²+b²=c²", "pythagoras", {"report_event"})

    assert response.response_kind == "refusal"
    assert render_response(response, fallback=FALLBACK) == FALLBACK
    assert "a²+b²" not in render_response(response, fallback=FALLBACK)


def test_unsupported_dijkstra_capability_cannot_include_algorithm_explanation():
    response = capability_response("Dijkstra repeatedly selects the nearest node", "dijkstra", {"ask_current_state"})

    assert render_response(response, fallback=FALLBACK) == FALLBACK
    assert "nearest node" not in render_response(response, fallback=FALLBACK)


def test_capability_claim_must_match_authorized_catalog():
    response = ResponseEnvelope(
        "I can manage settings.",
        "informational",
        claims=(ResponseClaim("capability", "manage_settings", "capability:manage_settings"),),
        authorized_capabilities=frozenset({"report_event"}),
    )

    assert render_response(response, fallback=FALLBACK) == FALLBACK


def test_permission_claim_without_authenticated_source_is_blocked():
    response = ResponseEnvelope(
        "Your commander permission was registered.",
        "permission",
        claims=(ResponseClaim("permission", "commander", None, verified=False),),
    )

    assert render_response(response, fallback="Permission is unchanged.") == "Permission is unchanged."


def test_permission_claim_is_allowed_only_from_authenticated_snapshot():
    response = permission_response("Your viewer permission is unchanged.", "viewer")

    assert render_response(response, fallback="unknown") == "Your viewer permission is unchanged."

    forged = ResponseEnvelope(
        "Your commander permission is now active.",
        "permission",
        claims=(ResponseClaim("permission", "commander", "auth:viewer-1"),),
        authenticated_permission="viewer",
    )
    assert render_response(forged, fallback="Permission is unchanged.") == "Permission is unchanged."


def test_approval_claim_without_approval_record_is_blocked():
    response = ResponseEnvelope(
        "Your approval was recorded.",
        "approval",
        claims=(ResponseClaim("approval", "approved", "auth:viewer-1"),),
    )

    assert render_response(response, fallback="No approval was recorded.") == "No approval was recorded."


def test_terminal_action_claim_without_tool_receipt_is_blocked():
    response = ResponseEnvelope(
        "The drone was dispatched.",
        "action_status",
        claims=(ResponseClaim("action_status", "executed", "event:event-1"),),
    )

    assert render_response(response, fallback="Action status could not be verified.") == "Action status could not be verified."


def test_verified_tool_and_state_claim_can_be_rendered():
    response = ResponseEnvelope(
        "The drone was dispatched.",
        "action_status",
        claims=(
            ResponseClaim("action_status", "executed", "state:mission-1"),
            ResponseClaim("tool_execution", "receipt", "tool:dispatch_drone"),
        ),
    )

    validate_response(response)
    assert render_response(response, fallback="unverified") == "The drone was dispatched."


def test_unknown_claim_category_is_rejected_by_contract():
    response = ResponseEnvelope(
        "unsupported claim",
        "informational",
        claims=(ResponseClaim("made_up", "value", "state:x"),),  # type: ignore[arg-type]
    )

    assert render_response(response, fallback="unknown") == "unknown"


def test_informational_response_from_real_capability_is_allowed():
    response = capability_response("I can report events.", "report_event", {"report_event"})

    assert render_response(response, fallback=FALLBACK) == "I can report events."


def test_job_formatter_hides_action_success_without_execution_evidence():
    result = JobResult(
        job_id="job-1",
        outcome="succeeded",
        protocol_name="dispatch_drone_to_incident",
        steps_completed=("surveillance_agent: The drone was dispatched.",),
        execution_evidence=(),
    )

    text = format_job_result(result)

    assert "could not be verified" in text
    assert "The drone was dispatched." not in text


def test_job_formatter_allows_action_result_with_runtime_evidence():
    result = JobResult(
        job_id="job-2",
        outcome="succeeded",
        protocol_name="dispatch_drone_to_incident",
        steps_completed=("surveillance_agent: The drone was dispatched.",),
        execution_evidence=("tool:dispatch_drone",),
    )

    assert "The drone was dispatched." in format_job_result(result)
