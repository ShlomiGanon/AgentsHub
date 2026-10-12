"""Shared operational context: the cross-channel digest and the links a new message inherits from it."""

import contextvars
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents import AgentResult, AgentTimeoutError, set_invocation_deadline
from agents.history import HistoryAgent
from agents.runtime_invoke import _invocation_deadline
from history import InitialEventEnvelope, record_event_outcome, record_initial_event, storage_timestamp
from history.event_pipeline import extraction_result_from_payload
from history.operational_context import operational_context_entries
from history.query import HistoryQueryService
from messages import get_catalog, get_current_catalog, set_current_catalog
from orchestrator.event_queue import PolicyAwareEventQueue, WorkItem
from orchestrator.flows import FlowDeps
from orchestrator.flows_execution import apply_event_links, operational_context_for
from orchestrator.flows_protocol import _record_backed, _recently_alerted
from persistence.sqlite_store import SQLitePersistence
from profiles import AreaRegistry, EventTypeRegistry
from protocols.loader import ProtocolSet

NOW = datetime(2026, 10, 12, 11, 30, tzinfo=timezone.utc)


class _Settings:
    """Minimal settings store for FlowDeps."""

    def get_risk_threshold(self):
        """Risk threshold."""
        return 0.5

    def get_rich_reports_enabled(self):
        """Rich reports flag."""
        return False


@pytest.fixture
def persistence(tmp_path):
    """A real SQLite store in a temp directory."""
    store = SQLitePersistence(str(tmp_path / "context.db"))
    yield store
    store.close()


@pytest.fixture
def deps(persistence):
    """FlowDeps whose history service reads the same store on a fixed clock."""
    areas = AreaRegistry(
        areas=("east_gate", "expansion_neighborhood"),
        labels={"east_gate": "השער המזרחי", "expansion_neighborhood": "שכונת ההרחבה"},
    )
    history_query_service = HistoryQueryService(
        persistence, HistoryAgent(model="m"), _Settings(), clock=lambda: NOW,
        timezone_name="Asia/Jerusalem", area_labels=dict(areas.labels),
    )
    return FlowDeps(
        persistence=persistence,
        settings_store=_Settings(),
        registry=SimpleNamespace(),
        protocol_set=ProtocolSet(protocols=()),
        event_type_registry=EventTypeRegistry(types=("security_incident",)),
        area_registry=areas,
        history_query_service=history_query_service,
    )


def _event(persistence, text, minutes_ago, sender="9000", **fields):
    """Record one event received `minutes_ago` before NOW and apply extra fields."""
    event_id = record_initial_event(
        persistence,
        InitialEventEnvelope(
            raw_text=text, source="telegram",
            received_at=storage_timestamp(NOW - timedelta(minutes=minutes_ago)), sender_identity=sender,
        ),
    )
    if fields:
        persistence.update_event(event_id, fields)
    return event_id


def test_digest_lists_recent_events_oldest_first_with_labels_and_status(persistence):
    """The digest shows local time, sender name, labelled area and a readable status."""
    persistence.write_user("9000", "viewer", "אברם")
    first = _event(persistence, "suspect in the yard", 20, area="east_gate", classification="security_incident")
    second = _event(persistence, "no shooting there", 5)
    record_event_outcome(persistence, first, "succeeded")

    entries = operational_context_entries(
        persistence, now=NOW, zone=timezone(timedelta(hours=3)),
        area_labels={"east_gate": "השער המזרחי"},
    )

    assert [entry["ref"] for entry in entries] == [first, second]
    assert entries[0]["from"] == "אברם"
    assert entries[0]["time"] == "14:10"
    assert entries[0]["area"] == "east_gate (השער המזרחי)"
    assert entries[0]["status"] == "handled"
    assert entries[1]["status"] == "in progress"


def test_digest_excludes_the_current_event_old_events_and_other_senders_for_a_viewer(persistence):
    """The current event, events outside the window, and (for a viewer) others' events are left out."""
    own = _event(persistence, "mine", 10, sender="viewer-1")
    _event(persistence, "someone else's", 10, sender="viewer-2")
    _event(persistence, "too old", 60 * 24, sender="viewer-1")
    current = _event(persistence, "current", 1, sender="viewer-1")

    entries = operational_context_entries(
        persistence, now=NOW, exclude_event_ids=(current,), sender_identity_filter="viewer-1",
    )

    assert [entry["ref"] for entry in entries] == [own]


def test_extraction_payload_carries_link_refs():
    """related/corrects refs from the model survive normalization for the caller to validate."""
    result = extraction_result_from_payload(
        {"classification": None, "area": None, "related_event_ref": "abc", "corrects_event_ref": "def"},
        "telegram", "2026-10-12T11:00:00Z",
        EventTypeRegistry(types=("security_incident",)), AreaRegistry(areas=("east_gate",)),
    )

    assert result.related_event_ref == "abc" and result.corrects_event_ref == "def"


def test_a_follow_up_inherits_the_incident_and_area_of_the_event_it_continues(deps):
    """A message the model links to an earlier report joins its incident and takes its area."""
    anchor = _event(deps.persistence, "armed suspect", 15, area="expansion_neighborhood")
    follow_up = _event(deps.persistence, "ambulance needs security at the entrance", 1)
    context = operational_context_for(deps, exclude_event_id=follow_up)

    links = apply_event_links(deps, follow_up, context, related_ref=anchor)

    event = deps.persistence.fetch_event(follow_up)
    assert links == {"incident_id": anchor, "area": "expansion_neighborhood"}
    assert event["incident_id"] == anchor and event["area"] == "expansion_neighborhood"


def test_a_correction_retracts_exactly_the_linked_report(deps):
    """Only the report named by the model is retracted, never another one in the same area."""
    false_report = _event(deps.persistence, "shooting at the west gate", 10, area="east_gate")
    unrelated = _event(deps.persistence, "white van in the orchards", 30, area="east_gate")
    correction = _event(deps.persistence, "there was no shooting", 1)
    context = operational_context_for(deps, exclude_event_id=correction)

    apply_event_links(deps, correction, context, corrects_ref=false_report)

    assert deps.persistence.fetch_event(correction)["corrects_event_id"] == false_report
    assert deps.persistence.fetch_event(false_report)["retracted"] is True
    assert deps.persistence.fetch_event(unrelated)["retracted"] is False


def test_refs_outside_the_shown_context_are_ignored(deps):
    """A hallucinated or stale ref can never retract or re-parent anything."""
    elsewhere = _event(deps.persistence, "old", 60 * 48, area="east_gate")
    message = _event(deps.persistence, "new", 1)

    links = apply_event_links(deps, message, [], related_ref=elsewhere, corrects_ref=elsewhere)

    assert links == {}
    assert deps.persistence.fetch_event(elsewhere)["retracted"] is False


def test_a_decided_area_is_resolved_through_the_area_registry(deps):
    """A request's decided area may be an id or a label; it is stored as the id."""
    message = _event(deps.persistence, "activate the squad", 1)

    apply_event_links(deps, message, [], area="שכונת ההרחבה")

    assert deps.persistence.fetch_event(message)["area"] == "expansion_neighborhood"


def test_queued_work_keeps_the_submitters_catalog_but_not_its_request_deadline():
    """The worker sees the request's language, while the HTTP-scoped model deadline is dropped."""
    seen = {}
    queue = PolicyAwareEventQueue(lambda payload: seen.update(
        language=get_current_catalog().language, deadline=_invocation_deadline.get(),
    ), workers=1)
    queue.start()

    def _submit():
        set_current_catalog(get_catalog("he"))
        set_invocation_deadline(123.0)
        queue.submit(WorkItem("payload"))

    contextvars.copy_context().run(_submit)
    queue.wait_until_idle()
    queue.stop()

    assert seen == {"language": "he", "deadline": None}


def test_an_expired_item_is_reported_instead_of_silently_dropped():
    """A work item whose deadline passed while queued reaches the expiry callback, not the worker."""
    expired, processed = [], []
    queue = PolicyAwareEventQueue(processed.append, workers=1, on_expired=expired.append)
    queue.start()
    queue.submit(WorkItem("late", deadline_monotonic=0.0))
    queue.wait_until_idle()
    queue.stop()

    assert expired == ["late"] and processed == []


def test_a_repeated_resource_alert_is_recognized(deps):
    """The same unavailable-resource fact raised again within minutes is a repeat."""
    fact = "רחפן לא היה זמין עבור השער המזרחי"
    earlier = _event(deps.persistence, "first report", 0)
    record_event_outcome(deps.persistence, earlier, "handled_resource_unavailable", commander_alert_text=f"alert: {fact}")
    later = _event(deps.persistence, "second report", 0)

    assert _recently_alerted(deps, later, fact, now=NOW) is True
    assert _recently_alerted(deps, later, "something else", now=NOW) is False


def test_a_history_protocol_step_answers_from_the_stored_record(deps):
    """The history agent in a protocol run gets a narrative query over recent events, not a bare task."""
    calls = []

    class _Service:
        """History service double recording the narrative query."""

        def query_spec(self, question, spec, *, sender_identity_filter=None):
            """Return a canned debrief and remember what was asked."""
            calls.append((question, spec.operation, sender_identity_filter))
            return SimpleNamespace(answer="timeline")

    history_deps = FlowDeps(
        persistence=deps.persistence, settings_store=deps.settings_store, registry=deps.registry,
        protocol_set=deps.protocol_set, event_type_registry=deps.event_type_registry,
        area_registry=deps.area_registry, history_query_service=_Service(),
    )
    event = {"raw_text": "full debrief please", "sender_permission_level": "commander", "sender_identity": "c"}

    agent = _record_backed(history_deps, event, HistoryAgent(model="m"))
    result = agent.process("summarize", [])

    assert result == AgentResult(status="success", text="timeline")
    assert calls == [("full debrief please", "narrative", None)]


def test_non_history_agents_are_not_wrapped(deps):
    """Every other specialist runs exactly as before."""
    specialist = SimpleNamespace(name="surveillance_agent")

    assert _record_backed(deps, {}, specialist) is specialist
