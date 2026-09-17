import json

from agents import AgentRegistry, AgentResult, FriendlyForcesAgent
from orchestrator.flows import FlowDeps, _commit_report_domain_state, begin_report, run_report_extraction
from persistence.sqlite_store import SQLitePersistence
from profiles import AreaRegistry, EventTypeRegistry


class _ExtractionAgent:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def process(self, text, allowed_tools):
        self.calls += 1
        return AgentResult(status="success", text=json.dumps(self.payload))


def _deps(tmp_path, owner, agent):
    persistence = SQLitePersistence(str(tmp_path / "events.db"))
    return persistence, FlowDeps(
        persistence=persistence,
        settings_store=None,
        registry=AgentRegistry({owner: agent}),
        protocol_set=None,
        event_type_registry=EventTypeRegistry(("surveillance_report", "friendly_forces_report", "team_attendance_report")),
        area_registry=AreaRegistry(("north_gate",)),
        history_query_service=None,
        group_owner=owner,
    )


def test_group_owner_overrides_conflicting_classifier_and_commits_terminal_report(tmp_path):
    friendly = FriendlyForcesAgent(model="mock")
    persistence, deps = _deps(tmp_path, "friendly_forces_agent", friendly)
    model = _ExtractionAgent(
        {
            "classification": "surveillance_report",
            "area": None,
            "entities": ["ATV-7"],
            "description": "stolen vehicle reported in the sector",
            "severity": "high",
            "occurred_at": None,
            "business_fields": {"incident_kind": "stolen_vehicle"},
        }
    )
    try:
        event_id = begin_report(deps, "sector intelligence update", "telegram", "2026-09-17T10:00:00+00:00", "u1")
        result = run_report_extraction(deps, event_id, model, None)
        event = persistence.fetch_event(event_id)
        assert result.outcome == "succeeded"
        assert event["classification"] == "friendly_forces_report"
        assert event["outcome"] == "succeeded"
        assert model.calls == 1
    finally:
        persistence.close()


def test_owner_scope_never_falls_through_to_another_domain():
    class _Owner:
        name = "friendly_forces_agent"

        def ingest_report(self, event):
            from agents import ReportIngestionResult

            return ReportIngestionResult("not_applicable")

    class _Other:
        name = "surveillance_agent"

        def ingest_report(self, event):
            from agents import ReportIngestionResult

            return ReportIngestionResult("committed", "wrong domain")

    class _Persistence:
        def fetch_event(self, event_id):
            return {"event_id": event_id, "classification": "friendly_forces_report"}

    from agents import AgentRegistry

    deps = FlowDeps(
        persistence=_Persistence(), settings_store=None,
        registry=AgentRegistry({"friendly_forces_agent": _Owner(), "surveillance_agent": _Other()}),
        protocol_set=None, event_type_registry=None, area_registry=None,
        history_query_service=None, group_owner="friendly_forces_agent",
    )
    result = _commit_report_domain_state(deps, "e1")
    assert result.status == "not_applicable"


def test_owner_ingestion_without_typed_result_is_explicit_failure():
    class _Owner:
        name = "friendly_forces_agent"

        def ingest_report(self, event):
            return None

    class _Persistence:
        def fetch_event(self, event_id):
            return {"event_id": event_id, "classification": "friendly_forces_report"}

    deps = FlowDeps(
        persistence=_Persistence(), settings_store=None,
        registry=AgentRegistry({"friendly_forces_agent": _Owner()}),
        protocol_set=None, event_type_registry=None, area_registry=None,
        history_query_service=None, group_owner="friendly_forces_agent",
    )
    result = _commit_report_domain_state(deps, "e1")
    assert result.status == "failed"


def test_report_ingestion_status_preserves_legacy_boolean_constructor():
    from agents import ReportIngestionResult

    assert ReportIngestionResult(True).status == "committed"
    assert ReportIngestionResult(False).status == "rejected"
    assert ReportIngestionResult(committed=True).committed is True
    assert ReportIngestionResult("failed").committed is False
