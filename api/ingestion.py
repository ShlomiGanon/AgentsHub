"""POST /Event (work_plan.md §7.3).

Sensor ingestion. Sets the occurrence timestamp equal to the receipt
timestamp by recording `source="sensor"` — `history.extraction
.extract_event`'s own `source == "sensor"` branch (Mission 5) already
sets `occurred_at` to `received_at` and never asks the model to extract
one; this endpoint doesn't re-implement that, only triggers it. This is
half of §7.5's unified ingestion — see `api/ingestion.py` for the other
half and `tests/test_api_unified_ingestion.py` for the convergence proof.

One trace ID (§1.8) is generated here, at ingestion, and explicitly
threaded — as data, not relying on contextvar propagation across a
thread boundary it cannot cross on its own — into the queued
continuation's own closure, so every log record from `begin_report`
through the final history write carries the same one (§9.2's own
"confirm the trace ID connects every log record" requirement, found
unwired anywhere in the real code before this).

The synchronous portion below sets it via `tools.tracing.set_trace_id`,
not `with trace_context(...)` — this response still needs to reach
werkzeug's own request-log line, written only *after* this function
returns (see that function's own docstring); scoping it to a `with`
block that exits before `return` would reset it back to `""` first, the
exact gap this fixes (found live: querying by trace_id reconstructed
everything except the one line recording the request itself). The
queued continuation, on a different thread, still uses `trace_context`
as before — a bounded block is exactly right there.

`_now()` goes through `storage_timestamp` (re-exported from `history
.interface`, the declared entry point — `history.time_utils` itself is
internal to that package, per `docs/allowed_calls.md`) rather than
a raw `.isoformat()` call — found and fixed during §9.19/§9.20's
integration testing: a raw `.isoformat()` keeps microsecond precision and
a `+00:00` suffix, while every other `occurred_at`/`received_at` value
already in this system (anything passing through `storage_timestamp`,
including every range-query bound `history/query.py` builds) is
whole-second precision with no suffix. `occurred_at` is a plain SQLite
TEXT column compared lexicographically — two different formats in the
same column silently break that comparison for any pair of events close
enough together to land in the same second, exactly the condition a real
burst produces. See `docs/progress.md`'s §9.19/§9.20 entries for the full
diagnosis.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError
from history.interface import storage_timestamp
from orchestrator.flows import begin_report, run_report_extraction
from tools.tracing import new_trace_id, set_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext


def _now() -> str:
    return storage_timestamp(datetime.now(timezone.utc))


def build_events_blueprint(ctx: "ApiContext") -> Blueprint:
    blueprint = Blueprint("events", __name__)

    @blueprint.route("/Event", methods=["POST"])
    def post_event():
        level = authenticate(ctx.deps.persistence, request.headers.get("X-Identity"))
        require(level, "send_message")

        body = request.get_json(silent=True) or {}
        text = body.get("text")
        sender_identity = body.get("sender_identity")

        if not text:
            raise InvalidInputError("'text' is required", field="text")
        if not sender_identity:
            raise InvalidInputError("'sender_identity' is required", field="sender_identity")

        trace_id = new_trace_id()
        set_trace_id(trace_id)
        event_id = begin_report(ctx.deps, text, "sensor", _now(), sender_identity)

        def _work() -> None:
            with trace_context(trace_id):
                run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit((event_id, _work))

        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint

"""POST /Msg (work_plan.md §7.4).

Human ingestion — reports, requests, questions, and purely conversational
messages all arrive here; intent classification (§6.13) decides which.
Composes the split primitives (`begin_report`/`run_report_extraction`,
`begin_request`/`continue_from_risk_assessment`) itself rather than
calling `orchestrator.flows.process_message`, which runs a report or
request synchronously start to finish — exactly what §7.2 exists to
avoid blocking a request on. A question or a conversational message is
answered synchronously here, per §7.4's own rule: "a question has no job
to track" — a conversational reply has even less to track, since nothing
is retrieved or routed at all. This is half of §7.5's unified ingestion —
see `api/ingestion.py` for the other half and
`tests/test_api_unified_ingestion.py` for the convergence proof.

One trace ID (§1.8) covers the whole handler, including intent
classification — see `api/ingestion.py`'s own docstring for why it's
explicitly threaded into a queued continuation's closure rather than
relying on contextvar propagation across the queue's worker thread, and
for why the synchronous portion sets it via `tools.tracing.set_trace_id`
rather than a `with trace_context(...)` block that would reset it before
werkzeug's own request-log line gets written.

`_now()` goes through `storage_timestamp` for the same reason as
`api/ingestion.py`'s own `_now()` — see that module's docstring for the
full diagnosis (a format mismatch that silently broke same-second
range-query comparisons, found during §9.19/§9.20's integration testing).
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api.auth import authenticate, require
from api.errors import InvalidInputError, RunFailureError
from auth.permissions import PermissionLevel
from history.interface import storage_timestamp
from orchestrator.flows import (
    OrchestrationParseError,
    answer_conversationally,
    answer_question,
    begin_report,
    begin_request,
    classify_intent,
    continue_from_risk_assessment,
    run_report_extraction,
)
from tools.tracing import get_trace_id, new_trace_id, set_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)


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
        set_trace_id(trace_id)

        try:
            intent = classify_intent(ctx.main_agent, ctx.deps.protocol_set.all(), text)
        except OrchestrationParseError as exc:
            raise RunFailureError(str(exc)) from exc

        logger.info(
            "intent classified",
            extra={"event": "intent_classified", "intent": intent.intent, "reason": intent.reason, "trace_id": get_trace_id()},
        )

        received_at = _now()

        if intent.intent == "conversational":
            try:
                reply = answer_conversationally(ctx.main_agent, text)
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            return jsonify({"taken_as": "conversational", "answer": reply})

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



