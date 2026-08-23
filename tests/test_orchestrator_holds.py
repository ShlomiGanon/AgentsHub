import pytest

from auth.permissions import PermissionLevel
from orchestrator.holds import answer_approval_hold, create_approval_hold, determine_approval_hold
from orchestrator.main_agent import RiskAssessment
from orchestrator.selection import ProtocolSelectionResult
from persistence.sqlite_backend import SQLitePersistence
from protocols.model import CriticalityLevel, Protocol


def _protocol(name, approval_flag):
    return Protocol(
        name=name,
        description="d",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="x",
        criticality=CriticalityLevel.LOW,
        approval_flag=approval_flag,
    )


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "test.db"))
    yield backend
    backend.close()


# -- determine_approval_hold --------------------------------------------


def test_flagged_protocol_not_commander_holds():
    selection = ProtocolSelectionResult(status="selected", protocol_name="p", reason="r")
    protocols = {"p": _protocol("p", approval_flag=True)}

    assert determine_approval_hold(selection, protocols, originated_from_commander=False) == "flagged_protocol"


def test_flagged_protocol_commander_bypasses_the_flag():
    selection = ProtocolSelectionResult(status="selected", protocol_name="p", reason="r")
    protocols = {"p": _protocol("p", approval_flag=True)}

    assert determine_approval_hold(selection, protocols, originated_from_commander=True) is None


def test_ambiguous_not_commander_holds():
    selection = ProtocolSelectionResult(status="ambiguous", candidate_names=("a", "b"), reason="r")

    assert determine_approval_hold(selection, {}, originated_from_commander=False) == "ambiguous_selection"


def test_ambiguous_commander_still_holds():
    # A commander's authority bypasses the approval flag, not ambiguity —
    # there's no protocol yet for their authority to authorize.
    selection = ProtocolSelectionResult(status="ambiguous", candidate_names=("a", "b"), reason="r")

    assert determine_approval_hold(selection, {}, originated_from_commander=True) == "ambiguous_selection"


def test_unflagged_selected_protocol_never_holds():
    selection = ProtocolSelectionResult(status="selected", protocol_name="p", reason="r")
    protocols = {"p": _protocol("p", approval_flag=False)}

    assert determine_approval_hold(selection, protocols, originated_from_commander=False) is None


# -- create_approval_hold / answer_approval_hold -------------------------


def _selection():
    return ProtocolSelectionResult(status="selected", protocol_name="dispatch_response", reason="matches")


def _risk():
    return RiskAssessment(score=0.8, level="high", reason="active fire")


def test_create_and_answer_round_trip_approved(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    [held] = store.list_held_events("approval")
    assert held["hold_id"] == hold_id
    assert held["event_id"] == "evt-1"
    assert held["selected_protocol_name"] == "dispatch_response"

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "approved")

    assert result.status == "approved"
    assert result.hold["hold_id"] == hold_id
    assert store.list_held_events("approval") == []


def test_answer_rejected_decision(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    result = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "rejected")

    assert result.status == "rejected"


def test_viewer_cannot_answer(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())

    result = answer_approval_hold(store, hold_id, "viewer-1", PermissionLevel.VIEWER, "approved")

    assert result.status == "unauthorized"
    # the hold is untouched — still unresolved
    assert len(store.list_held_events("approval")) == 1


def test_answering_an_unknown_hold_is_not_found(store):
    result = answer_approval_hold(store, "never-existed", "commander-1", PermissionLevel.COMMANDER, "approved")

    assert result.status == "not_found"


def test_answering_an_already_resolved_hold_is_reported_distinctly(store):
    hold_id = create_approval_hold(store, "evt-1", "flagged_protocol", _selection(), _risk())
    first = answer_approval_hold(store, hold_id, "commander-1", PermissionLevel.COMMANDER, "approved")
    second = answer_approval_hold(store, hold_id, "commander-2", PermissionLevel.COMMANDER, "approved")

    assert first.status == "approved"
    assert second.status == "not_found"  # not silently accepted as a fresh answer
