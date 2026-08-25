"""POST /Event (work_plan.md §7.3).

Sensor ingestion. Sets the occurrence timestamp equal to the receipt
timestamp by recording `source="sensor"` — `history.extraction
.extract_event`'s own `source == "sensor"` branch (Mission 5) already
sets `occurred_at` to `received_at` and never asks the model to extract
one; this endpoint doesn't re-implement that, only triggers it. This is
half of §7.5's unified ingestion — see `api/messages.py` for the other
half and `tests/test_api_unified_ingestion.py` for the convergence proof.

One trace ID (§1.8) is generated here, at ingestion, and explicitly
threaded — as data, not relying on contextvar propagation across a
thread boundary it cannot cross on its own — into the queued
continuation's own closure, so every log record from `begin_report`
through the final history write carries the same one (§9.2's own
"confirm the trace ID connects every log record" requirement, found
unwired anywhere in the real code before this).

`_now()` goes through `storage_timestamp` (re-exported from `history
.interface`, the declared entry point — `history.time_utils` itself is
internal to that package, per `docs/allowed_calls.md`) rather than
a raw `.isoformat()` call — found and fixed during §9.19/§9.20's
integration testing: a raw `.isoformat()` keeps microsecond precision and
a `+00:00` suffix, while every other `occurred_at`/`received_at` value
already in this system (anything passing through `storage_timestamp`,
including every range-query bound `history/retrieval.py` builds) is
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
from tools.tracing import new_trace_id, trace_context

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
        with trace_context(trace_id):
            event_id = begin_report(ctx.deps, text, "sensor", _now(), sender_identity)

        def _work() -> None:
            with trace_context(trace_id):
                run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit((event_id, _work))

        return jsonify({"event_id": event_id, "status": "queued"}), 202

    return blueprint
