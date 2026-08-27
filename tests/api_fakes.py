"""Shared building blocks for the api/ test suite.

Plain helper functions and classes, not fixtures — each test_api_*.py
file wires its own pytest fixtures around these, the same pattern
tests/helpers.py already established for profile-validation fakes.

`build_context` constructs a real `api.app.ApiContext` — real
`SQLitePersistence`, a real `AgentRegistry` with a real `ReferenceAgent`
and `HistoryAgent`, a real `FlowDeps`, a real (unstarted)
`SerialEventQueue`, a real (unstarted) `SummaryScheduler` — with only the
Main/Insights Agent slots filled by a caller-supplied scripted stand-in,
mirroring tests/test_orchestrator_flows.py's own `deps` fixture. Nothing
here talks to a real model; `agents.adapter._get_crewai` still needs
monkeypatching wherever a real `ReferenceAgent`/`HistoryAgent` is actually
invoked — each test file brings its own autouse fixture for that, same as
tests/test_orchestrator_flows.py already does.
"""

import threading

from agents.history import HistoryAgent
from agents.reference import ReferenceAgent
from agents.registry import build_agent_registry
from api.app import ApiContext
from config.settings_store import SettingsStore
from history.interface import SummaryScheduler
from history.query import HistoryQueryService
from orchestrator.flows import FlowDeps, SerialEventQueue
from persistence.sqlite_backend import SQLitePersistence
from protocols.loader import ProtocolSet
from protocols.model import CriticalityLevel, Protocol
from registries.areas import AreaRegistry
from registries.event_types import EventTypeRegistry

VIEWER_IDENTITY = "viewer-1"
COMMANDER_IDENTITY = "commander-1"
SENSOR_IDENTITY = "sensor-1"
IDENTITY_HEADER = "X-Identity"


class FakeResult:
    def __init__(self, status, text):
        self.status = status
        self.text = text


class ScriptedAgent:
    """A duck-typed Main/Insights Agent stand-in: dispatches by sniffing
    the prompt for a keyword unique to each decision — the same technique
    tests/test_orchestrator_flows.py uses, since one object plays every
    role a real MainAgent plays across one flow run.
    """

    def __init__(self, dispatch: dict[str, str], default_status="success"):
        self._dispatch = dispatch
        self._default_status = default_status
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append(text)
        for keyword, response_text in self._dispatch.items():
            if keyword in text:
                return FakeResult(self._default_status, response_text)
        raise AssertionError(f"no scripted response for prompt starting: {text[:150]!r}")


class FakeSettings:
    def __init__(self, risk_threshold=0.5, retry_count=3, lookback_window_days=30):
        self.risk_threshold = risk_threshold
        self.retry_count = retry_count
        self.lookback_window_days = lookback_window_days

    def get_retry_count(self):
        return self.retry_count

    def get_risk_threshold(self):
        return self.risk_threshold

    def get_lookback_window_days(self):
        return self.lookback_window_days

    def set_retry_count(self, value):
        self.retry_count = value

    def set_risk_threshold(self, value):
        self.risk_threshold = value

    def set_lookback_window_days(self, value):
        self.lookback_window_days = value


def protocols() -> tuple[Protocol, ...]:
    return (
        Protocol(
            name="status_check",
            description="applies to a routine status check",
            participating_agents=("reference_agent",),
            approved_tools=("check_status",),
            expected_success_output="a status report",
            criticality=CriticalityLevel.LOW,
            approval_flag=False,
        ),
        Protocol(
            name="dispatch_response",
            description="applies when a response must be dispatched",
            participating_agents=("reference_agent",),
            approved_tools=("check_status", "record_action"),
            expected_success_output="confirmation a response was dispatched",
            criticality=CriticalityLevel.HIGH,
            approval_flag=True,
        ),
    )


def extraction_response(classification="fire", area="north_sector", description="smoke at gate 3", severity="moderate", occurred_at="2026-08-20T09:00:00") -> str:
    import json

    return json.dumps(
        {"classification": classification, "area": area, "entities": ["gate-3"], "description": description, "severity": severity, "occurred_at": occurred_at}
    )


def happy_path_agent(risk_score="0.2", selected="status_check", verdict="success", agent_task="check gate 3", extraction=None, intent=None) -> ScriptedAgent:
    dispatch = {
        "Extract this operational event": extraction or extraction_response(),
        "RISK_SCORE": f"RISK_SCORE: {risk_score}\nREASON: assessed",
        "Choose the protocol": f"SELECTED: {selected}\nREASON: fits",
        "participating in the": f"AGENT: reference_agent\nTASK: {agent_task}",
        "VERDICT:": f"VERDICT: {verdict}\nREASONING: matches expected output",
        # orchestrator.question_flow's direct-lookup classification, ahead
        # of agent-selection for every question — "ROUTE: normal" here
        # keeps every existing question-answering test's own
        # "Decide which of the following agents" dispatch entry reachable
        # unchanged, exactly as before this classification step existed.
        "Decide whether this question can be answered by directly looking up": "ROUTE: normal",
    }
    if intent is not None:
        dispatch["kind of message"] = f"INTENT: {intent}\nREASON: classified"
    return ScriptedAgent(dispatch)


class _AlwaysSucceedsAgent:
    """A permissive Insights-Agent stand-in — returns a fixed, harmless
    "insight" for any prompt at all. Used as `build_context`'s default so
    a test that isn't specifically exercising insight text doesn't need
    its own dispatch entry for whatever prompt `orchestrator.insights
    .build_insight` happens to send.
    """

    def process(self, text, allowed_tools):
        return FakeResult("success", "insight")


def build_context(tmp_path, main_agent=None, insights_agent=None, module_path=None, users=((VIEWER_IDENTITY, "viewer"), (COMMANDER_IDENTITY, "commander"), (SENSOR_IDENTITY, "viewer"))) -> ApiContext:
    persistence = SQLitePersistence(str(tmp_path / "api_test.db"))
    for identity, level in users:
        persistence.write_user(identity, level)

    reference_agent = ReferenceAgent(model="m")
    history_agent = HistoryAgent(model="m")
    fake_main_agent = main_agent if main_agent is not None else happy_path_agent()
    fake_insights_agent = insights_agent if insights_agent is not None else _AlwaysSucceedsAgent()

    # main_agent/insights_agent are not participating agents — they're
    # never dispatched to by name through the registry, only passed as
    # explicit parameters throughout orchestrator.flows's public API (and
    # held directly on ApiContext, mirroring how api.app.build_context
    # pulls them back out of the registry after assembling it). Only
    # real, name-bearing agents belong in the registry itself — same
    # pattern tests/test_orchestrator_flows.py's own `deps` fixture uses.
    registry = build_agent_registry({"history_agent": history_agent}, [reference_agent])

    settings_store = FakeSettings()
    history_query_service = HistoryQueryService(persistence, history_agent, settings_store)

    deps = FlowDeps(
        persistence=persistence,
        settings_store=settings_store,
        registry=registry,
        protocol_set=ProtocolSet(protocols=protocols()),
        event_type_registry=EventTypeRegistry(types=("fire", "medical", "human_activation")),
        area_registry=AreaRegistry(areas=("north_sector", "south_sector")),
        history_query_service=history_query_service,
    )

    queue = SerialEventQueue(lambda item: item[1]())
    queue.start()

    scheduler = SummaryScheduler(persistence, history_agent)

    return ApiContext(
        deps=deps,
        main_agent=fake_main_agent,
        insights_agent=fake_insights_agent,
        loaded_profile=_FakeLoadedProfile(module_path or "fixtures.profiles.minimal_profile"),
        queue=queue,
        scheduler=scheduler,
    )


class _FakeLoadedProfile:
    """Stands in for `profiles.loader.LoadedProfile` — api/system.py and
    api/protocols.py only ever read `.module_path`/`.profile_file_hash`
    off it; a real one needs a real profile module on disk, which most of
    these tests have no reason to require. Defaults to the real demo
    fixture profile so `profiles.loader.hash_profile_file` (used by
    api/system.py) has an actual file to hash; a protocol-write test
    passes its own disposable temp module instead, since writing a
    protocol genuinely edits the file at `module_path` on disk and must
    never touch a shared fixture file.
    """

    def __init__(self, module_path: str):
        from profiles.loader import hash_profile_file

        self.module_path = module_path
        # Captured once, here, at "load" time — like the real LoadedProfile
        # does — not recomputed live. A property recomputing it on every
        # access would always equal api/system.py's own fresh recompute,
        # making a genuinely pending profile-file edit undetectable.
        self.profile_file_hash = hash_profile_file(module_path)


def auth_headers(identity: str) -> dict:
    return {IDENTITY_HEADER: identity}


class RunningApiServer:
    """A real `api.app.build_app` Flask app listening on a real,
    OS-assigned TCP port on 127.0.0.1 — for `bot.http_api_client
    .HttpApiClient` tests, which make genuine HTTP requests over a real
    socket (`app.test_client()` never opens one; it dispatches WSGI calls
    in-process, which `HttpApiClient`'s own `urllib` calls cannot reach).
    Runs the werkzeug dev server on a background daemon thread; `base_url`
    is ready the moment the constructor returns.
    """

    def __init__(self, ctx: ApiContext):
        from werkzeug.serving import make_server

        from api.app import build_app

        self.ctx = ctx
        app = build_app(ctx)
        self._server = make_server("127.0.0.1", 0, app)
        self.port = self._server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join()
        self.ctx.queue.stop()
        self.ctx.deps.persistence.close()

    def __enter__(self) -> "RunningApiServer":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
