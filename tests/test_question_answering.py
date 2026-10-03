"""Question routing and answer composition behavior."""

import threading
import time

import pytest

from agents.history import HistoryAgent
from agents.runtime import build_agent_registry
from agents.runtime import ToolInfo
from messages import get_catalog
from orchestrator.main_agent import OrchestrationParseError
from orchestrator.queue import SerialEventQueue
from orchestrator.reasoning import SpecialistFailure, SpecialistResult, answer_question, run_parallel_specialists
from tools.logging_config import configure_logging
from tools.observability import event_id_context, protocol_context
from tools.tracing import trace_context


class _ScriptedAgent:
    """ScriptedAgent."""
    def __init__(self, name, tool_infos=(), response_text="an answer", status="success", raises=None):
        """Initialize this test helper."""
        self.name = name
        self._tool_infos = tool_infos
        self._response_text = response_text
        self._status = status
        self._raises = raises
        self.calls = []

    def exposed_tools(self):
        """Exposed tools."""
        return self._tool_infos

    @property
    def descriptor(self):
        """Descriptor."""
        from types import SimpleNamespace

        return SimpleNamespace(name=self.name, role=f"role of {self.name}", tools=self._tool_infos)

    def process(self, text, allowed_tools):
        """Process."""
        self.calls.append((text, tuple(allowed_tools)))
        if self._raises is not None:
            raise self._raises

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


class _ScriptedMainAgent:
    """ScriptedMainAgent."""
    def __init__(self, responses):
        """Initialize this test helper."""
        self._responses = list(responses)  # consumed in call order
        self.calls = []

    def process(self, text, allowed_tools):
        """Process."""
        self.calls.append((text, allowed_tools))
        response_text, status = self._responses.pop(0)

        class _Result:
            pass

        _Result.status = status
        _Result.text = response_text
        return _Result()


class _ScriptedHistoryQueryService:
    """ScriptedHistoryQueryService."""
    def __init__(self, answer_text=None, raises=None):
        """Initialize this test helper."""
        self._answer_text = answer_text
        self._raises = raises
        self.calls = []

    def query(self, question, **kwargs):
        """Query."""
        self.calls.append(question)
        if self._raises is not None:
            raise self._raises

        class _Answer:
            answer = self._answer_text

        return _Answer()


READ_ONLY_TOOL = (ToolInfo(name="check_status", description="d", side_effecting=False, idempotent=None),)
MIXED_TOOLS = (
    ToolInfo(name="check_status", description="d", side_effecting=False, idempotent=None),
    ToolInfo(name="record_action", description="d", side_effecting=True, idempotent=False),
)

NO_HISTORY_SERVICE = _ScriptedHistoryQueryService()  # unused in tests with no real HistoryAgent


# Every scenario below that reaches agent-selection at all first pays for
# the new, unconditional direct-lookup classification call (module
# docstring) — a "ROUTE: normal" response for it is prepended to every
# _ScriptedMainAgent response list so the existing, positionally-scripted
# selection/compose responses still land on the right call.
_ROUTE_NORMAL = ("ROUTE: normal", "success")


def test_single_agent_chosen_answer_passes_through_without_a_compose_call():
    """Single agent chosen answer passes through without a compose call."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS, response_text="gate 3 is nominal")
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: reference_agent\nTASK: check gate 3", "success")])

    answer = answer_question(main_agent, "is gate 3 ok?", registry, NO_HISTORY_SERVICE)

    assert answer == "gate 3 is nominal"
    assert len(main_agent.calls) == 2  # classification + selection; no compose call needed for a single agent


def test_side_effecting_tool_is_never_passed_to_a_chosen_agent():
    """Side effecting tool is never passed to a chosen agent."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS)
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: reference_agent\nTASK: check gate 3", "success")])

    answer_question(main_agent, "what's the status?", registry, NO_HISTORY_SERVICE)

    assert reference_agent.calls[0][1] == ("check_status",)  # record_action filtered out


def test_read_only_only_holds_regardless_of_question_wording():
    """Read only only holds regardless of question wording."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS)
    registry = build_agent_registry({}, [reference_agent])
    # A question phrased as if it wants an action still only gets read-only tools.
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: reference_agent\nTASK: dispatch a response to gate 3", "success")])

    answer_question(main_agent, "can you dispatch someone to gate 3?", registry, NO_HISTORY_SERVICE)

    assert "record_action" not in reference_agent.calls[0][1]


def test_multiple_agents_are_composed_into_one_answer():
    """Multiple agents are composed into one answer."""
    status_agent = _ScriptedAgent("status_agent", (), response_text="two similar incidents last month")
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="currently nominal")
    registry = build_agent_registry({}, [status_agent, reference_agent])

    main_agent = _ScriptedMainAgent(
        [
            _ROUTE_NORMAL,
            ("AGENT: status_agent\nTASK: any similar incidents?\nAGENT: reference_agent\nTASK: current status?", "success"),
            ("Historically two similar incidents occurred; current status is nominal.", "success"),
        ]
    )

    answer = answer_question(main_agent, "has this happened before and what's the status now?", registry, NO_HISTORY_SERVICE)

    assert answer == "Historically two similar incidents occurred; current status is nominal."
    assert len(main_agent.calls) == 3  # classification + selection + compose
    compose_prompt = main_agent.calls[2][0]
    assert "two similar incidents last month" in compose_prompt
    assert "currently nominal" in compose_prompt


def test_duplicate_agent_selection_gets_one_targeted_repair():
    """Duplicate agent selection gets one targeted repair."""
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="both sectors are nominal")
    registry = build_agent_registry({}, [reference_agent])
    duplicate = (
        "AGENT: reference_agent\nTASK: check the north sector\n"
        "AGENT: reference_agent\nTASK: check the south sector"
    )
    repaired = "AGENT: reference_agent\nTASK: check both the north and south sectors"
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, (duplicate, "success"), (repaired, "success")])

    answer = answer_question(main_agent, "what is the situation in the sector?", registry, NO_HISTORY_SERVICE)

    assert answer == "both sectors are nominal"
    assert len(main_agent.calls) == 3
    assert "more than once" in main_agent.calls[2][0]
    assert "each agent may appear at most once" in main_agent.calls[2][0].lower()


def test_no_agent_chosen_raises():
    # Free text with neither an AGENT:/TASK: block nor a NONE: line —
    # a genuine parse failure, distinct from a clean NONE decline.
    """No agent chosen raises."""
    registry = build_agent_registry({}, [])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("I cannot determine which agent to ask.", "success")])

    with pytest.raises(OrchestrationParseError):
        answer_question(main_agent, "some question", registry, NO_HISTORY_SERVICE)


def test_none_selection_returns_a_clean_cant_answer_reply_not_a_crash():
    # The repro-1 shape: a question matching no loaded agent's role at
    # all. The model uses the new NONE: line instead of being forced onto
    # the closest-sounding agent — no agent is ever asked anything.
    """None selection returns a clean cant answer reply not a crash."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS)
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent(
        [_ROUTE_NORMAL, ("NONE: no loaded agent tracks individual user tasks", "success")]
    )

    answer = answer_question(main_agent, "do I have any tasks?", registry, NO_HISTORY_SERVICE)

    assert answer == "I don't have a way to answer that. no loaded agent tracks individual user tasks"
    assert reference_agent.calls == []  # never dispatched to


def test_unclear_routing_status_raises():
    """Unclear routing status raises."""
    registry = build_agent_registry({}, [])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("missing context", "unclear_task")])

    with pytest.raises(OrchestrationParseError):
        answer_question(main_agent, "some question", registry, NO_HISTORY_SERVICE)


def test_a_sub_agent_that_fails_does_not_crash_the_whole_answer():
    # Two chosen agents, only one reporting unclear_task — the single-
    # agent clean-reply path below must not apply here; an unusable
    # specialist result still feeds composition rather than becoming the
    # whole answer.
    """A sub agent that fails does not crash the whole answer."""
    failing_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="broken", status="unclear_task")
    other_agent = _ScriptedAgent("status_agent", (), response_text="all clear")
    registry = build_agent_registry({}, [failing_agent, other_agent])
    main_agent = _ScriptedMainAgent(
        [
            _ROUTE_NORMAL,
            ("AGENT: reference_agent\nTASK: check status\nAGENT: status_agent\nTASK: any incidents?", "success"),
            ("composed answer", "success"),
        ]
    )

    answer = answer_question(main_agent, "what's the status?", registry, NO_HISTORY_SERVICE)

    assert answer == "composed answer"


def test_a_single_chosen_agents_unclear_task_gets_the_clean_cant_answer_reply():
    # The direct symptom found in repro 1: previously this returned the
    # agent's raw internal text verbatim as the final answer. Now routed
    # through the same clean presentation a true NONE selection gets, and
    # the agent's own wording is never quoted back to the asker.
    """A single chosen agents unclear task gets the clean cant answer reply."""
    failing_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="please specify a location", status="unclear_task")
    registry = build_agent_registry({}, [failing_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: reference_agent\nTASK: check on my tasks", "success")])

    answer = answer_question(main_agent, "do I have any tasks?", registry, NO_HISTORY_SERVICE)

    assert answer == "I don't have a way to answer that. reference_agent doesn't have a way to help with this question."
    assert "please specify a location" not in answer


# -- Real HistoryAgent routes through HistoryQueryService, not .process() ---


def test_a_real_history_agent_is_routed_through_the_query_service_not_process():
    """A real history agent is routed through the query service not process."""
    history_agent = HistoryAgent(model="m")
    registry = build_agent_registry({}, [history_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: history_agent\nTASK: has this happened before?", "success")])
    history_service = _ScriptedHistoryQueryService(answer_text="handled twice before, both resolved")

    answer = answer_question(main_agent, "has this happened before?", registry, history_service)

    assert answer == "handled twice before, both resolved"
    assert history_service.calls == ["has this happened before?"]


def test_history_query_service_receives_the_agent_specific_task_not_the_original_question():
    """History query service receives the agent specific task not the original question."""
    history_agent = HistoryAgent(model="m")
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="nominal")
    registry = build_agent_registry({}, [history_agent, reference_agent])
    main_agent = _ScriptedMainAgent(
        [
            _ROUTE_NORMAL,
            ("AGENT: history_agent\nTASK: any prior fires at this gate?\nAGENT: reference_agent\nTASK: current status?", "success"),
            ("composed answer", "success"),
        ]
    )
    history_service = _ScriptedHistoryQueryService(answer_text="one prior fire, resolved")

    answer_question(main_agent, "has a fire happened here before and what's the status now?", registry, history_service)

    assert history_service.calls == ["any prior fires at this gate?"]


def test_history_query_error_does_not_crash_the_whole_answer():
    """History query error does not crash the whole answer."""
    from history.query import HistoryQueryError

    history_agent = HistoryAgent(model="m")
    registry = build_agent_registry({}, [history_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: history_agent\nTASK: any prior incidents?", "success")])
    history_service = _ScriptedHistoryQueryService(raises=HistoryQueryError("no material available"))

    answer = answer_question(main_agent, "any prior incidents?", registry, history_service)

    assert answer == "I don't have a way to answer that. no material available"


def test_empty_history_reply_uses_the_error_flag_not_the_exception_wording():
    """Empty history reply uses the error flag not the exception wording."""
    from history.query import HistoryQueryError

    history_agent = HistoryAgent(model="m")
    registry = build_agent_registry({}, [history_agent])
    hebrew_question = "האם היו אירועים?"

    wording_only = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: history_agent\nTASK: any prior incidents?", "success")])
    wording_service = _ScriptedHistoryQueryService(
        raises=HistoryQueryError("no stored events match the requested history filters")
    )
    wording_answer = answer_question(wording_only, hebrew_question, registry, wording_service)
    assert wording_answer.startswith("לא ניתן להשיב על כך כרגע.")
    assert "no stored events" in wording_answer

    flagged = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: history_agent\nTASK: any prior incidents?", "success")])
    flagged_service = _ScriptedHistoryQueryService(raises=HistoryQueryError("nothing here", empty=True))
    flagged_answer = answer_question(flagged, hebrew_question, registry, flagged_service)
    assert flagged_answer == "לא נמצאו אירועים מתאימים בהיסטוריה או ביומן המבצעי."


def test_a_successful_answer_that_looks_like_a_timeout_marker_is_still_the_answer():
    """A successful answer that looks like a timeout marker is still the answer."""
    reference_agent = _ScriptedAgent(
        "reference_agent", MIXED_TOOLS, response_text="(timeout: this is the real finding)"
    )
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("AGENT: reference_agent\nTASK: check gate 3", "success")])

    answer = answer_question(main_agent, "is gate 3 ok?", registry, NO_HISTORY_SERVICE)

    assert answer == "(timeout: this is the real finding)"


def test_a_specialist_exception_appends_the_partial_failure_note():
    """A specialist exception appends the partial failure note."""
    failing_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, raises=RuntimeError("boom"))
    other_agent = _ScriptedAgent("status_agent", (), response_text="all clear")
    registry = build_agent_registry({}, [failing_agent, other_agent])
    main_agent = _ScriptedMainAgent(
        [
            _ROUTE_NORMAL,
            ("AGENT: reference_agent\nTASK: check status\nAGENT: status_agent\nTASK: any incidents?", "success"),
            ("composed answer", "success"),
        ]
    )

    answer = answer_question(main_agent, "what's the status?", registry, NO_HISTORY_SERVICE)

    note = get_catalog("en").text("orchestrator.specialist.partial_failure", agents="reference_agent")
    assert answer == f"composed answer\n{note}"


def test_run_parallel_specialists_records_an_exception_on_the_result_not_in_the_answer():
    """Run parallel specialists records an exception on the result not in the answer."""
    def _boom():
        raise RuntimeError("provider failed")

    results = run_parallel_specialists([("failing_agent", _boom)])

    result = results["failing_agent"]
    assert result.failed is True
    assert result.failure is SpecialistFailure.ERROR
    assert result.answer == ""


def test_run_parallel_specialists_records_a_timeout_on_the_result_not_in_the_answer():
    """Run parallel specialists records a timeout on the result not in the answer."""
    def _slow():
        time.sleep(0.4)
        return SpecialistResult(answer="late")

    def _ok():
        return SpecialistResult(answer="done")

    results = run_parallel_specialists(
        [("slow_agent", _slow), ("ok_agent", _ok)],
        max_workers=2,
        timeout_per_specialist=0.05,
    )

    slow = results["slow_agent"]
    assert slow.failed is True
    assert slow.failure is SpecialistFailure.TIMEOUT
    assert slow.answer == ""
    assert results["ok_agent"] == SpecialistResult(answer="done")


def test_run_parallel_specialists_does_not_treat_answer_text_as_a_failure_signal():
    """Run parallel specialists does not treat answer text as a failure signal."""
    results = run_parallel_specialists(
        [("agent", lambda: SpecialistResult(answer="(timeout: real finding)"))]
    )

    assert results["agent"] == SpecialistResult(answer="(timeout: real finding)")


def test_specialist_logs_include_agent_trace_and_event_id(capsys):
    """Specialist logs include agent trace and event id."""
    import json

    configure_logging("test_profile")

    def _ok():
        return SpecialistResult(answer="done")

    with trace_context("trace-specialist"), event_id_context("evt-42"), protocol_context("status_check"):
        run_parallel_specialists([("reference_agent", _ok)])

    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line]
    started = next(record for record in records if record.get("event") == "specialist_started")
    finished = next(record for record in records if record.get("event") == "specialist_finished")
    assert started["agent"] == "reference_agent"
    assert started["parent_agent"] == "main_agent"
    assert started["trace_id"] == "trace-specialist"
    assert started["event_id"] == "evt-42"
    assert started["protocol_name"] == "status_check"
    assert finished["agent"] == "reference_agent"
    assert finished["trace_id"] == "trace-specialist"
    assert finished["event_id"] == "evt-42"


def test_specialist_timeout_includes_trace_id(capsys):
    """Specialist timeout includes trace id."""
    import json

    configure_logging("test_profile")

    def _slow():
        time.sleep(0.4)
        return SpecialistResult(answer="late")

    def _ok():
        return SpecialistResult(answer="done")

    with trace_context("trace-timeout"):
        run_parallel_specialists(
            [("slow_agent", _slow), ("ok_agent", _ok)],
            max_workers=2,
            timeout_per_specialist=0.05,
        )

    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line]
    timeout = next(record for record in records if record.get("event") == "specialist_timeout")
    assert timeout["agent"] == "slow_agent"
    assert timeout["trace_id"] == "trace-timeout"


def test_queue_started_includes_event_id_and_concurrency_keys(capsys):
    """Queue started includes event id and concurrency keys."""
    import json

    from orchestrator.event_queue import SerialEventQueue, WorkItem

    configure_logging("test_profile")
    seen = []

    def _process(payload):
        seen.append(payload[0])

    queue = SerialEventQueue(_process)
    queue.start()
    with trace_context("trace-queue"):
        queue.submit(WorkItem(("evt-7", lambda: None), trace_id="trace-queue", concurrency_keys=("area:north",)))
        queue.wait_until_idle()
    queue.stop()

    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line]
    started = next(record for record in records if record.get("event") == "queue_started")
    assert started["event_id"] == "evt-7"
    assert started["trace_id"] == "trace-queue"
    assert started["concurrency_keys"] == ["area:north"]
    assert seen == ["evt-7"]


# -- Direct-lookup classification (bypasses agent-selection entirely) ------


class _ScriptedHistoryQueryServiceWithDirectLookup(_ScriptedHistoryQueryService):
    """ScriptedHistoryQueryServiceWithDirectLookup."""
    def __init__(self, *args, most_recent_answer=None, most_recent_raises=None, **kwargs):
        """Initialize this test helper."""
        super().__init__(*args, **kwargs)
        self._most_recent_answer = most_recent_answer
        self._most_recent_raises = most_recent_raises
        self.most_recent_calls = []

    def answer_most_recent_event(self, question, **kwargs):
        """Answer most recent event."""
        self.most_recent_calls.append(question)
        if self._most_recent_raises is not None:
            raise self._most_recent_raises

        class _Answer:
            answer = self._most_recent_answer

        return _Answer()


def test_a_recognized_direct_lookup_bypasses_agent_selection_entirely():
    # The repro-2 shape: "what is the last event?" — recognized by the new
    # classification step, answered via HistoryQueryService.
    # answer_most_recent_event directly. No AGENT:/TASK: call is ever made
    # — agent-selection's own free-text-parsing crash risk never runs.
    """A recognized direct lookup bypasses agent selection entirely."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS)
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([("DIRECT_LOOKUP: most_recent", "success")])
    history_service = _ScriptedHistoryQueryServiceWithDirectLookup(most_recent_answer="a fire report in north_sector, 10 minutes ago")

    answer = answer_question(main_agent, "what is the last event?", registry, history_service)

    assert answer == "a fire report in north_sector, 10 minutes ago"
    assert len(main_agent.calls) == 1  # only the classification call — never agent-selection
    assert history_service.most_recent_calls == ["what is the last event?"]
    assert reference_agent.calls == []  # never dispatched to


def test_a_direct_lookup_with_no_events_yet_gets_a_clean_reply_not_a_crash():
    """A direct lookup with no events yet gets a clean reply not a crash."""
    from history.query import HistoryQueryError

    registry = build_agent_registry({}, [])
    main_agent = _ScriptedMainAgent([("DIRECT_LOOKUP: most_recent", "success")])
    history_service = _ScriptedHistoryQueryServiceWithDirectLookup(most_recent_raises=HistoryQueryError("no events have been recorded yet"))

    answer = answer_question(main_agent, "what is the last event?", registry, history_service)

    assert answer == "I don't have a way to answer that. no events have been recorded yet"


def test_an_unparseable_classification_response_falls_back_to_normal_routing():
    # The classification step must never itself become a new crash path —
    # anything other than a clean DIRECT_LOOKUP: line (free text, "ROUTE:
    # normal", or an unclear_task status) falls through to ordinary
    # agent-selection unchanged.
    """An unparseable classification response falls back to normal routing."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS, response_text="gate 3 is nominal")
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent(
        [
            ("I'm not sure what kind of question this is.", "success"),
            ("AGENT: reference_agent\nTASK: check gate 3", "success"),
        ]
    )

    answer = answer_question(main_agent, "is gate 3 ok?", registry, NO_HISTORY_SERVICE)

    assert answer == "gate 3 is nominal"


def test_a_classification_call_reporting_unclear_task_falls_back_to_normal_routing():
    """A classification call reporting unclear task falls back to normal routing."""
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS, response_text="gate 3 is nominal")
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent(
        [
            ("missing context", "unclear_task"),
            ("AGENT: reference_agent\nTASK: check gate 3", "success"),
        ]
    )

    answer = answer_question(main_agent, "is gate 3 ok?", registry, NO_HISTORY_SERVICE)

    assert answer == "gate 3 is nominal"


def test_structured_history_route_executes_a_validated_history_query_spec():
    """Structured history route executes a validated history query spec."""
    import json
    from types import SimpleNamespace

    class _StructuredHistoryService:
        def __init__(self):
            self.spec = None

        def planning_context(self):
            return {"current_time_utc": "2026-08-28T10:00:00", "classifications": ["fire"], "areas": ["north"]}

        def query_spec(self, question, spec, **kwargs):
            self.spec = spec
            return SimpleNamespace(answer="2 matching events.")

    route = json.dumps({
        "route": "history",
        "history_query": {
            "operation": "count",
            "time_start": "2026-08-01T00:00:00",
            "time_end": "2026-09-01T00:00:00",
            "time_basis": "occurred_at",
            "classifications": ["fire"],
            "areas": ["north"],
            "outcomes": [],
            "protocol_names": [],
            "event_ids": [],
            "risk_levels": [],
            "order": "newest",
            "group_by": "none",
            "limit": 50,
        },
        "reason": "stored-event count",
    })
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, (route, "success")])
    service = _StructuredHistoryService()

    answer = answer_question(main_agent, "How many fires were in the north?", build_agent_registry({}, []), service)

    assert answer == "2 matching events."
    assert service.spec.operation == "count"
    assert service.spec.classifications == ("fire",)


def test_conversation_reference_resolves_to_a_fresh_event_details_lookup():
    # "That event" is resolved from conversation
    # context to a stable Event ID, then re-fetched fresh from history —
    # the remembered assistant text is never treated as current fact. The
    # model (simulated here by the scripted response) is the one that
    # reads conversation context and decides the event_id; this proves the
    # conversation context actually reaches the prompt it needs to, and
    # that the resolved reference flows through as a real, fresh query.
    """Conversation reference resolves to a fresh event details lookup."""
    import json
    from types import SimpleNamespace

    class _StructuredHistoryService:
        def __init__(self):
            self.spec = None

        def planning_context(self):
            return {"current_time_utc": "2026-08-28T10:00:00"}

        def query_spec(self, question, spec, **kwargs):
            self.spec = spec
            return SimpleNamespace(answer="Event evt-42 is currently still queued.")

    route = json.dumps({
        "route": "history",
        "history_query": {
            "operation": "event_details", "time_start": None, "time_end": None, "time_basis": "occurred_at",
            "classifications": [], "areas": [], "outcomes": [], "protocol_names": [], "event_ids": ["evt-42"],
            "risk_levels": [], "order": "newest", "group_by": "none", "limit": 50,
        },
        "reason": "resolved 'that event' to evt-42 from conversation context",
    })
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, (route, "success")])
    service = _StructuredHistoryService()
    prior_messages = ({"role": "assistant", "content": "Recorded — Event ID evt-42, currently queued."},)

    answer = answer_question(
        main_agent, "what's the status of that event now?", build_agent_registry({}, []), service,
        conversation_messages=prior_messages,
    )

    assert answer == "Event evt-42 is currently still queued."
    assert service.spec.operation == "event_details"
    assert service.spec.event_ids == ("evt-42",)
    # Both prompts the model saw actually carried the conversation context —
    # proof the reference could be resolved at all, not just that a
    # scripted response happened to name the right ID.
    direct_lookup_prompt, agent_selection_prompt = (call[0] for call in main_agent.calls)
    assert "evt-42" in direct_lookup_prompt
    assert "evt-42" in agent_selection_prompt


def test_question_router_does_not_expose_decision_only_agents():
    """Question router does not expose decision only agents."""
    main_registry_agent = _ScriptedAgent("main_agent")
    insights_registry_agent = _ScriptedAgent("insights_agent")
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL)
    registry = build_agent_registry({}, [main_registry_agent, insights_registry_agent, reference_agent])
    main_agent = _ScriptedMainAgent([_ROUTE_NORMAL, ("NONE: no matching capability", "success")])

    answer_question(main_agent, "some unsupported question", registry, NO_HISTORY_SERVICE)

    selection_prompt = main_agent.calls[1][0]
    assert '"name": "main_agent"' not in selection_prompt
    assert '"name": "insights_agent"' not in selection_prompt
    assert '"name": "reference_agent"' in selection_prompt


def test_direct_lookup_marker_requires_the_exact_supported_value():
    """Direct lookup marker requires the exact supported value."""
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="gate 3 is nominal")
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([
        ("DIRECT_LOOKUP: anything", "success"),
        ("AGENT: reference_agent\nTASK: check gate 3", "success"),
    ])

    answer = answer_question(main_agent, "is gate 3 ok?", registry, NO_HISTORY_SERVICE)

    assert answer == "gate 3 is nominal"


def test_items_are_processed_in_strict_arrival_order():
    """Items are processed in strict arrival order."""
    processed = []
    q = SerialEventQueue(processed.append)
    q.start()

    for i in range(10):
        q.submit(i)

    q.wait_until_idle()
    q.stop()
    assert processed == list(range(10))


def test_one_failing_item_does_not_stop_subsequent_items():
    """One failing item does not stop subsequent items."""
    processed = []

    def _process(item):
        if item == "bad":
            raise ValueError("boom")
        processed.append(item)

    q = SerialEventQueue(_process)
    q.start()

    q.submit("first")
    q.submit("bad")
    q.submit("third")
    q.wait_until_idle()
    q.stop()

    assert processed == ["first", "third"]


def test_processing_is_serial_not_concurrent():
    # A slow item must finish before the next one starts — this would
    # fail if items were processed concurrently.
    """Processing is serial not concurrent."""
    order = []
    lock_held = threading.Event()

    def _process(item):
        assert not lock_held.is_set(), "two items were being processed at once"
        lock_held.set()
        time.sleep(0.02)
        order.append(item)
        lock_held.clear()

    q = SerialEventQueue(_process)
    q.start()

    for i in range(5):
        q.submit(i)

    q.wait_until_idle()
    q.stop()

    assert order == [0, 1, 2, 3, 4]


def test_wait_until_idle_blocks_until_processing_actually_finished():
    """Wait until idle blocks until processing actually finished."""
    processed = []

    def _slow_process(item):
        time.sleep(0.05)
        processed.append(item)

    q = SerialEventQueue(_slow_process)
    q.start()
    q.submit("x")
    q.wait_until_idle()

    assert processed == ["x"]
    q.stop()


def test_start_is_idempotent():
    """Start is idempotent."""
    processed = []
    q = SerialEventQueue(processed.append)

    q.start()
    q.start()  # must not raise or start a second worker
    q.submit("only-once")
    q.wait_until_idle()
    q.stop()

    assert processed == ["only-once"]


def test_qsize_reflects_items_not_yet_picked_up():
    """Qsize reflects items not yet picked up."""
    release = threading.Event()

    def _process(item):
        del item
        release.wait()

    q = SerialEventQueue(_process)
    q.start()

    q.submit("first")  # picked up immediately, not counted
    time.sleep(0.02)
    q.submit("second")
    q.submit("third")

    assert q.qsize() == 2  # "first" is being processed, not queued

    release.set()
    q.wait_until_idle()
    q.stop()


def test_currently_processing_reports_the_in_flight_item_then_clears():
    """Currently processing reports the in flight item then clears."""
    seen_while_processing = []
    release = threading.Event()

    def _process(item):
        seen_while_processing.append(q.currently_processing())
        if item == "first":
            release.wait()

    q = SerialEventQueue(_process)
    q.start()

    assert q.currently_processing() is None  # nothing submitted yet

    q.submit("first")
    time.sleep(0.02)
    assert q.currently_processing() == "first"

    release.set()
    q.wait_until_idle()

    assert q.currently_processing() is None  # cleared once idle
    assert seen_while_processing == ["first"]
    q.stop()


def test_currently_processing_clears_even_when_the_item_raises():
    """Currently processing clears even when the item raises."""
    q = SerialEventQueue(lambda item: (_ for _ in ()).throw(ValueError("boom")))
    q.start()

    q.submit("bad")
    q.wait_until_idle()

    assert q.currently_processing() is None
    q.stop()


def test_stop_returns_within_timeout_when_the_worker_is_blocked(monkeypatch):
    """Stop returns within timeout when the worker is blocked."""
    from orchestrator import event_queue as event_queue_module
    from orchestrator.event_queue import SerialEventQueue

    monkeypatch.setattr(event_queue_module, "STOP_JOIN_TIMEOUT_SECONDS", 0.2)
    release = threading.Event()
    q = SerialEventQueue(lambda _item: release.wait())
    q.start()
    q.submit("blocked")
    try:
        for _ in range(200):
            if q.currently_processing() is not None:
                break
            time.sleep(0.01)
        started = time.monotonic()
        q.stop()
        assert time.monotonic() - started < 1.0
        assert q._worker.is_alive()
    finally:
        release.set()
        q._worker.join(timeout=1.0)
