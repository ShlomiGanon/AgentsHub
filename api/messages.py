"""POST /Msg (work_plan.md §7.4).

Human ingestion — reports, requests, and questions all arrive here;
intent classification (§6.13) decides which. Composes the split
primitives (`begin_report`/`run_report_extraction`,
`begin_request`/`continue_from_risk_assessment`) itself rather than
calling `orchestrator.flows.process_message`, which runs a report or
request synchronously start to finish — exactly what §7.2 exists to
avoid blocking a request on. A question is answered synchronously here,
per §7.4's own rule: "a question has no job to track." This is half of
§7.5's unified ingestion — see `api/events.py` for the other half and
`tests/test_api_unified_ingestion.py` for the convergence proof.

One trace ID (§1.8) covers the whole handler, including intent
classification — see `api/events.py`'s own docstring for why it's
explicitly threaded into a queued continuation's closure rather than
relying on contextvar propagation across the queue's worker thread.

`_now()` goes through `storage_timestamp` for the same reason as
`api/events.py`'s own `_now()` — see that module's docstring for the
full diagnosis (a format mismatch that silently broke same-second
range-query comparisons, found during §9.19/§9.20's integration testing).
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError, RunFailureError
from auth.permissions import PermissionLevel
from history.interface import storage_timestamp
from orchestrator.flows import (
    OrchestrationParseError,
    answer_question,
    begin_report,
    begin_request,
    classify_intent,
    continue_from_risk_assessment,
    run_report_extraction,
)
from tools.tracing import new_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext


def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))


def build_messages_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("messages", __name__)

    @blueprint.route("/Msg", methods=["POST"])
    def post_msg():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "send_message")

        body = request.get_json(silent=True) or {}
        text = body.get("text")
        sender_identity = body.get("sender_identity")
        source_message_id = body.get("source_message_id")

        if not text:
            raise InvalidInputError("'text' is required", field="text")
        if not sender_identity:
            raise InvalidInputError("'sender_identity' is required", field="sender_identity")

        trace_id = new_trace_id()
        with trace_context(trace_id):
            try:
                intent = classify_intent(ctx.main_agent, ctx.deps.protocol_set.all(), text)
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc

            received_at = _now()

            if intent.intent == "question":
                try:
                    answer = answer_question(ctx.main_agent, text, ctx.deps.registry, ctx.deps.history_query_service)
                except OrchestrationParseError as exc:
                    raise RunFailureError(str(exc)) from exc
                return jsonify({"taken_as": "question", "answer": answer})

            if intent.intent == "report":
                event_id = begin_report(ctx.deps, text, "telegram", received_at, sender_identity, source_message_id)

                def _work() -> None:
                    with trace_context(trace_id):
                        run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

                ctx.queue.submit((event_id, _work))
                return jsonify({"taken_as": "report", "event_id": event_id, "status": "queued"}), 202

            is_commander = level >= PermissionLevel.COMMANDER
            event_id = begin_request(ctx.deps, text, received_at, sender_identity, source_message_id)

            def _work() -> None:
                with trace_context(trace_id):
                    continue_from_risk_assessment(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent, is_commander)

            ctx.queue.submit((event_id, _work))
            return jsonify({"taken_as": "request", "event_id": event_id, "status": "queued"}), 202

    return blueprint
