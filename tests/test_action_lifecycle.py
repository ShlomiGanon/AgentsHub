from agents.contracts import AgentResult, ToolInfo, ToolReceipt, tool
from agents.runtime import Agent, tool_execution_context
from history import InitialEventEnvelope, record_action_lifecycle, record_initial_event, record_step_execution
from history.contracts import StepExecutionEnvelope
from persistence.sqlite_store import SQLitePersistence
from protocols.contracts import Step, StepOutcome
from protocols.executor import execute_step_with_retry
from api.routes import _verified_execution_evidence


class _ToolAgent(Agent):
    name = "tool_agent"
    role = "executor"
    system_prompt = "execute"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.invocations = 0
        super().__init__("test/model")

    @tool("write_record", "write one record", side_effecting=True, idempotent=False)
    def write_record(self, value: str) -> str:
        self.invocations += 1
        if self.fail:
            raise RuntimeError("write failed")
        return value


class _Settings:
    def get_retry_count(self):
        return 3


def _event(store):
    return record_initial_event(
        store,
        InitialEventEnvelope(
            raw_text="action",
            source="telegram",
            received_at="2026-09-17T10:00:00+00:00",
            sender_identity="user-1",
        ),
    )


def test_runtime_tool_receipt_contains_safe_correlation_and_no_arguments():
    agent = _ToolAgent()
    with tool_execution_context("event-1", "step-1"):
        result = agent.execute_tool("write_record", {"value": "ok"}, ["write_record"])

    assert result.status == "success"
    assert len(result.tool_receipts) == 1
    receipt = result.tool_receipts[0]
    assert receipt.tool_name == "write_record"
    assert receipt.success is True
    assert receipt.side_effecting is True
    assert receipt.event_id == "event-1"
    assert receipt.step_id == "step-1"
    assert not hasattr(receipt, "arguments")


def test_tool_failure_emits_failed_receipt():
    agent = _ToolAgent(fail=True)
    with tool_execution_context("event-1", "step-1"):
        try:
            agent.execute_tool("write_record", {"value": "bad"}, ["write_record"])
        except Exception as exc:
            assert exc.tool_receipts[0].status == "failed"
    assert agent.invocations == 1


def test_failed_direct_step_persists_failed_receipt():
    agent = _ToolAgent(fail=True)
    step = Step(
        agent_name="tool_agent", task_text="write", allowed_tools=("write_record",),
        direct_tool_name="write_record", direct_tool_arguments={"value": "bad"},
    )
    outcome = execute_step_with_retry(agent, step, _Settings())
    assert outcome.succeeded is False
    assert outcome.action_state == "failed"
    assert outcome.tool_receipts[0].status == "failed"


def test_direct_side_effect_step_uses_receipt_and_does_not_retry_after_success():
    agent = _ToolAgent()
    step = Step(
        agent_name="tool_agent",
        task_text="write",
        allowed_tools=("write_record",),
        direct_tool_name="write_record",
        direct_tool_arguments={"value": "ok"},
    )
    states = []
    outcome = execute_step_with_retry(
        agent,
        step,
        _Settings(),
        event_id="event-1",
        lifecycle_callback=lambda state, _step: states.append(state),
    )
    assert outcome.succeeded is True
    assert outcome.action_state == "executed"
    assert len(outcome.tool_receipts) == 1
    assert agent.invocations == 1
    assert states == ["executing", "executed"]


def test_postcondition_verifier_is_typed_and_can_block_execution():
    agent = _ToolAgent()
    step = Step(
        agent_name="tool_agent", task_text="write", allowed_tools=("write_record",),
        direct_tool_name="write_record", direct_tool_arguments={"value": "ok"},
    )
    outcome = execute_step_with_retry(
        agent, step, _Settings(),
        postcondition_verifier=lambda _receipt: False,
    )
    assert outcome.succeeded is False
    assert outcome.action_state == "failed"
    assert outcome.tool_receipts[0].state_verified is False


def test_postcondition_verifier_true_marks_receipt_verified_and_executed():
    agent = _ToolAgent()
    step = Step(
        agent_name="tool_agent", task_text="write", allowed_tools=("write_record",),
        direct_tool_name="write_record", direct_tool_arguments={"value": "ok"},
    )
    outcome = execute_step_with_retry(agent, step, _Settings(), postcondition_verifier=lambda _receipt: True)
    assert outcome.succeeded is True
    assert outcome.action_state == "executed"
    assert outcome.tool_receipts[0].state_verified is True


def test_resume_with_verified_receipt_skips_side_effect_step():
    agent = _ToolAgent()
    step = Step(
        agent_name="tool_agent", task_text="write", allowed_tools=("write_record",),
        direct_tool_name="write_record", direct_tool_arguments={"value": "ok"},
    )
    receipt = ToolReceipt(
        tool_name="write_record", status="succeeded", success=True,
        started_at="2026-09-17T10:00:01+00:00", completed_at="2026-09-17T10:00:02+00:00",
        side_effecting=True,
    )
    from protocols.executor import execute_steps
    result = execute_steps(
        [step], {"tool_agent": agent}, _Settings(),
        prior_outcomes=(
            StepOutcome(
                step=step, result_text="ok", attempt_count=1, succeeded=False,
                status="failed", action_state="executed", tool_receipts=(receipt,),
            ),
        ),
    )
    assert result.completed is True
    assert agent.invocations == 0


def test_read_only_step_has_no_action_lifecycle_semantics():
    class ReadOnlyAgent:
        name = "reader"
        def exposed_tools(self):
            return (ToolInfo("read", "read", False, None),)
        def process(self, text, allowed_tools):
            return AgentResult(status="success", text="ok")

    outcome = execute_step_with_retry(
        ReadOnlyAgent(), Step(agent_name="reader", task_text="read", allowed_tools=("read",)), _Settings()
    )
    assert outcome.succeeded is True
    assert outcome.action_state is None
    assert outcome.tool_receipts == ()


def test_response_provenance_comes_from_receipt_not_direct_tool_metadata():
    event = {
        "action_state": "executed",
        "steps": [
            {"status": "succeeded", "direct_tool_name": "write_record", "result_text": "בוצע", "tool_receipts": []},
            {"status": "succeeded", "direct_tool_name": "write_record", "result_text": "בוצע", "tool_receipts": [
                {"tool_name": "write_record", "status": "succeeded", "success": True, "receipt_id": "r1"}
            ]},
        ],
    }
    assert _verified_execution_evidence(event) == ["tool:write_record:r1"]


def test_agent_prose_does_not_create_execution_lifecycle():
    class ProseAgent:
        name = "prose"
        def exposed_tools(self):
            return (ToolInfo("write_record", "write", True, False),)
        def process(self, text, allowed_tools):
            return AgentResult(status="success", text="בוצע")

    step = Step(agent_name="prose", task_text="write", allowed_tools=("write_record",))
    outcome = execute_step_with_retry(ProseAgent(), step, _Settings())
    assert outcome.succeeded is True
    assert outcome.action_state is None
    assert outcome.tool_receipts == ()


def test_lifecycle_approval_is_distinct_from_execution_and_persists_receipt(tmp_path):
    store = SQLitePersistence(str(tmp_path / "action.db"))
    try:
        event_id = _event(store)
        record_action_lifecycle(store, event_id, "requested")
        record_action_lifecycle(store, event_id, "pending_approval")
        record_action_lifecycle(store, event_id, "approved")
        assert store.fetch_event(event_id)["action_state"] == "approved"

        receipt = ToolReceipt(
            tool_name="write_record", status="succeeded", success=True,
            started_at="2026-09-17T10:00:01+00:00", completed_at="2026-09-17T10:00:02+00:00",
            event_id=event_id, step_id="step-1", side_effecting=True,
        )
        record_step_execution(
            store,
            event_id,
            StepExecutionEnvelope(
                step_index=0, agent_name="tool_agent", task_text="write",
                allowed_tools=["write_record"], result_text="ok", attempt_count=1,
                status="succeeded", action_state="executed", tool_receipts=(receipt,),
            ),
        )
        record_action_lifecycle(store, event_id, "executing")
        record_action_lifecycle(store, event_id, "executed", receipts=(receipt,))
        restored = store.fetch_event(event_id)
        assert restored["action_state"] == "executed"
        assert restored["action_tool_receipts"][0]["tool_name"] == "write_record"
        assert restored["steps"][0]["tool_receipts"][0]["receipt_id"] == receipt.receipt_id
    finally:
        store.close()
