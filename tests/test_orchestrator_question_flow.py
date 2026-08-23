import pytest

from agents.history import HistoryAgent
from agents.registry import build_agent_registry
from agents.tooling import ToolInfo
from orchestrator.errors import OrchestrationParseError
from orchestrator.question_flow import answer_question


class _ScriptedAgent:
    def __init__(self, name, tool_infos=(), response_text="an answer", status="success"):
        self.name = name
        self._tool_infos = tool_infos
        self._response_text = response_text
        self._status = status
        self.calls = []

    def exposed_tools(self):
        return self._tool_infos

    @property
    def descriptor(self):
        from types import SimpleNamespace

        return SimpleNamespace(name=self.name, role=f"role of {self.name}", tools=self._tool_infos)

    def process(self, text, allowed_tools):
        self.calls.append((text, tuple(allowed_tools)))

        class _Result:
            status = self._status
            text = self._response_text

        return _Result()


class _ScriptedMainAgent:
    def __init__(self, responses):
        self._responses = list(responses)  # consumed in call order
        self.calls = []

    def process(self, text, allowed_tools):
        self.calls.append((text, allowed_tools))
        response_text, status = self._responses.pop(0)

        class _Result:
            pass

        _Result.status = status
        _Result.text = response_text
        return _Result()


class _ScriptedHistoryQueryService:
    def __init__(self, answer_text=None, raises=None):
        self._answer_text = answer_text
        self._raises = raises
        self.calls = []

    def query(self, question, **kwargs):
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


def test_single_agent_chosen_answer_passes_through_without_a_compose_call():
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS, response_text="gate 3 is nominal")
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([("AGENT: reference_agent\nTASK: check gate 3", "success")])

    answer = answer_question(main_agent, "is gate 3 ok?", registry, NO_HISTORY_SERVICE)

    assert answer == "gate 3 is nominal"
    assert len(main_agent.calls) == 1  # no compose call needed for a single agent


def test_side_effecting_tool_is_never_passed_to_a_chosen_agent():
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS)
    registry = build_agent_registry({}, [reference_agent])
    main_agent = _ScriptedMainAgent([("AGENT: reference_agent\nTASK: check gate 3", "success")])

    answer_question(main_agent, "what's the status?", registry, NO_HISTORY_SERVICE)

    assert reference_agent.calls[0][1] == ("check_status",)  # record_action filtered out


def test_read_only_only_holds_regardless_of_question_wording():
    reference_agent = _ScriptedAgent("reference_agent", MIXED_TOOLS)
    registry = build_agent_registry({}, [reference_agent])
    # A question phrased as if it wants an action still only gets read-only tools.
    main_agent = _ScriptedMainAgent([("AGENT: reference_agent\nTASK: dispatch a response to gate 3", "success")])

    answer_question(main_agent, "can you dispatch someone to gate 3?", registry, NO_HISTORY_SERVICE)

    assert "record_action" not in reference_agent.calls[0][1]


def test_multiple_agents_are_composed_into_one_answer():
    status_agent = _ScriptedAgent("status_agent", (), response_text="two similar incidents last month")
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="currently nominal")
    registry = build_agent_registry({}, [status_agent, reference_agent])

    main_agent = _ScriptedMainAgent(
        [
            ("AGENT: status_agent\nTASK: any similar incidents?\nAGENT: reference_agent\nTASK: current status?", "success"),
            ("Historically two similar incidents occurred; current status is nominal.", "success"),
        ]
    )

    answer = answer_question(main_agent, "has this happened before and what's the status now?", registry, NO_HISTORY_SERVICE)

    assert answer == "Historically two similar incidents occurred; current status is nominal."
    assert len(main_agent.calls) == 2  # selection call + compose call
    compose_prompt = main_agent.calls[1][0]
    assert "two similar incidents last month" in compose_prompt
    assert "currently nominal" in compose_prompt


def test_no_agent_chosen_raises():
    registry = build_agent_registry({}, [])
    main_agent = _ScriptedMainAgent([("I cannot determine which agent to ask.", "success")])

    with pytest.raises(OrchestrationParseError):
        answer_question(main_agent, "some question", registry, NO_HISTORY_SERVICE)


def test_unclear_routing_status_raises():
    registry = build_agent_registry({}, [])
    main_agent = _ScriptedMainAgent([("missing context", "unclear_task")])

    with pytest.raises(OrchestrationParseError):
        answer_question(main_agent, "some question", registry, NO_HISTORY_SERVICE)


def test_a_sub_agent_that_fails_does_not_crash_the_whole_answer():
    failing_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="broken", status="unclear_task")
    registry = build_agent_registry({}, [failing_agent])
    main_agent = _ScriptedMainAgent([("AGENT: reference_agent\nTASK: check status", "success")])

    answer = answer_question(main_agent, "what's the status?", registry, NO_HISTORY_SERVICE)

    assert "no usable answer" in answer


# -- Real HistoryAgent routes through HistoryQueryService, not .process() ---


def test_a_real_history_agent_is_routed_through_the_query_service_not_process():
    history_agent = HistoryAgent(model="m")
    registry = build_agent_registry({}, [history_agent])
    main_agent = _ScriptedMainAgent([("AGENT: history_agent\nTASK: has this happened before?", "success")])
    history_service = _ScriptedHistoryQueryService(answer_text="handled twice before, both resolved")

    answer = answer_question(main_agent, "has this happened before?", registry, history_service)

    assert answer == "handled twice before, both resolved"
    assert history_service.calls == ["has this happened before?"]


def test_history_query_service_receives_the_agent_specific_task_not_the_original_question():
    history_agent = HistoryAgent(model="m")
    reference_agent = _ScriptedAgent("reference_agent", READ_ONLY_TOOL, response_text="nominal")
    registry = build_agent_registry({}, [history_agent, reference_agent])
    main_agent = _ScriptedMainAgent(
        [
            ("AGENT: history_agent\nTASK: any prior fires at this gate?\nAGENT: reference_agent\nTASK: current status?", "success"),
            ("composed answer", "success"),
        ]
    )
    history_service = _ScriptedHistoryQueryService(answer_text="one prior fire, resolved")

    answer_question(main_agent, "has a fire happened here before and what's the status now?", registry, history_service)

    assert history_service.calls == ["any prior fires at this gate?"]


def test_history_query_error_does_not_crash_the_whole_answer():
    from history.query import HistoryQueryError

    history_agent = HistoryAgent(model="m")
    registry = build_agent_registry({}, [history_agent])
    main_agent = _ScriptedMainAgent([("AGENT: history_agent\nTASK: any prior incidents?", "success")])
    history_service = _ScriptedHistoryQueryService(raises=HistoryQueryError("no material available"))

    answer = answer_question(main_agent, "any prior incidents?", registry, history_service)

    assert "no usable answer" in answer
