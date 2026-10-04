"""Message ingestion routes (`POST /Msg`)."""

from datetime import datetime, timedelta, timezone
import inspect
import logging
import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

from api._route_deps import utc_now_storage, work_concurrency_keys
from api.request_boundary import (
    InvalidInputError,
    RunFailureError,
    ServiceUnavailableError,
    authenticate,
    require,
)
from api.routes_messages_support import (
    KNOWN_BUTTON_PROTOCOLS,
    SITUATIONAL_PICTURE_PROTOCOL,
    apply_group_scope,
    queued_answer_text,
    validate_message_fields,
)
from agents import AgentInvocationError, authenticated_request_identity, set_invocation_deadline
from auth.permissions import PermissionLevel, RequestedOperation
from history import record_event_outcome, storage_timestamp
from orchestrator.flows import (
    OrchestrationParseError,
    WorkItem,
    answer_conversationally,
    answer_question,
    answer_question_from_plan,
    apply_drone_selection_reply,
    apply_event_data_reply,
    attempt_direct_lane,
    begin_report,
    begin_request,
    build_role_aware_system_context,
    build_situational_picture,
    classify_intent,
    picture_protocol,
    question_requests_picture,
    read_picture_directly,
    continue_from_risk_assessment,
    plan_message,
    protocol_requires_approval,
    resume_after_event_data,
    run_report_extraction,
)
from profiles import HUMAN_ACTIVATION_TYPE, OptimizationPolicy
from tools import deep_debug_enabled, get_trace_id, new_trace_id, set_trace_id, trace_context

if TYPE_CHECKING:
    from api.app import ApiContext

logger = logging.getLogger(__name__)

_queued_answer_text = queued_answer_text

def _accepts_a_call_with_no_arguments(fn) -> bool:
    """True when every parameter already has a default, so a button can call the tool as-is."""

    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.default is inspect.Parameter.empty:
            return False
    return True


def build_messages_blueprint(app_ctx: "ApiContext") -> Blueprint:
    """JSON route that classifies and runs one inbound `/Msg`."""

    blueprint = Blueprint("messages", __name__)
    messages = app_ctx.loaded_profile.message_catalog
    non_human_activation_event_types = tuple(
        event_type
        for event_type in app_ctx.deps.event_type_registry.types
        if event_type != HUMAN_ACTIVATION_TYPE
    )
    areas = tuple(app_ctx.deps.area_registry.areas)

    @blueprint.route("/Msg", methods=["POST"])
    def post_msg():
        """Classify one inbound message and answer, queue, or continue a hold."""

        # `ctx` is request-local: for a message from a Telegram group bound to a
        # specialist it becomes a copy of `app_ctx` whose `deps` only expose that
        # agent (+ history). Every `ctx.deps.*` read below is therefore scoped
        # automatically; nothing else on the context differs.
        ctx = app_ctx
        caller_identity = request.headers.get("X-Identity")
        level = authenticate(ctx.deps.persistence, caller_identity)
        require(level, RequestedOperation.SUBMIT_MESSAGE)

        request_payload = request.get_json(silent=True) or {}
        source_message_id = request_payload.get("source_message_id")
        conversation_id = request_payload.get("conversation_id")
        event_data_event_id = request_payload.get("event_data_event_id")
        telegram_chat_id = request_payload.get("telegram_chat_id")
        telegram_chat_type = request_payload.get("telegram_chat_type")
        ack_message_id = request_payload.get("ack_message_id")

        ctx, scoped_agent = apply_group_scope(
            app_ctx, telegram_chat_id, telegram_chat_type, messages
        )
        if ctx is not app_ctx:
            logger.info(
                "message scoped to group agent",
                extra={"event": "group_scope_applied", "chat_id": str(telegram_chat_id), "agent": scoped_agent, "trace_id": get_trace_id()},
            )

        # Built fresh per authenticated request so a viewer's context omits
        # protected arrays entirely, rather than filtering them later.
        system_context = build_role_aware_system_context(
            level,
            ctx.loaded_profile.profile_name,
            ctx.deps.protocol_set.all(),
            ctx.deps.registry,
            non_human_activation_event_types,
            areas,
        )

        text, sender_identity = validate_message_fields(request_payload, caller_identity, messages)

        trace_id = get_trace_id() or new_trace_id()
        set_trace_id(trace_id)

        optimization_policy = getattr(ctx.loaded_profile, "optimization_policy", OptimizationPolicy())
        set_invocation_deadline(time.monotonic() + optimization_policy.direct_deadline_seconds)
        history_turns = getattr(ctx.loaded_profile, "conversation_history_turns", 0)
        history_ttl = getattr(ctx.loaded_profile, "conversation_history_ttl_hours", 24)

        def _remember(role: str, content: str, event_id: str | None = None) -> None:
            """Append one conversation turn when this request carries a conversation id."""

            if conversation_id is not None and history_turns > 0:
                ctx.deps.persistence.append_conversation_message(
                    conversation_id,
                    role,
                    content,
                    ttl_hours=history_ttl,
                    max_turns=history_turns,
                    event_id=event_id,
                )

        if source_message_id:
            existing_event = ctx.deps.persistence.fetch_event_by_source_message("telegram", sender_identity, str(source_message_id))
            if existing_event is not None:
                existing_kind = "request" if existing_event.get("classification") == "human_activation" else "report"
                return jsonify({
                    "taken_as": existing_kind,
                    "event_id": existing_event["event_id"],
                    "status": "queued" if existing_event.get("outcome") is None else existing_event["outcome"],
                    "duplicate": True,
                }), 202

        prior_messages: tuple[dict, ...] = ()
        if conversation_id is not None and history_turns > 0:
            prior_messages = tuple(ctx.deps.persistence.fetch_conversation_messages(conversation_id, history_turns * 2))

        _remember("user", text)

        # Event-data replies are explicit. Sharing a sender/conversation with an
        # old hold is insufficient because a new button or request must remain
        # an independent message.
        pending_hold = None
        if event_data_event_id is not None:
            candidate = ctx.deps.persistence.fetch_held_event("event_data", event_data_event_id)
            pending_event = ctx.deps.persistence.fetch_event(event_data_event_id)
            if (
                candidate is None
                or candidate.get("resolved")
                or pending_event is None
                or pending_event.get("conversation_id") != conversation_id
                or pending_event.get("sender_identity") != caller_identity
            ):
                raise InvalidInputError(messages.text("api.event_data_reply_not_pending"))
            pending_hold = candidate

        # A drone-choice reply is operational input, not free-form missing event
        # data. Forward it to the surveillance specialist so the model chooses
        # a one-drone recall or a fleet recall; do not send it through the planner.
        drone_selection_hold = (
            pending_hold if pending_hold is not None and pending_hold.get("missing_fields") == ["drone_selection"] else None
        )

        if drone_selection_hold is not None:
            require(level, RequestedOperation.APPROVE_RUN)
            try:
                selection = apply_drone_selection_reply(
                    ctx.deps, text, drone_selection_hold, resolved_by=caller_identity,
                )
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            _remember("assistant", selection.message, selection.event_id)
            return jsonify({
                "taken_as": "clarification" if selection.status == "waiting_for_drone_selection" else "event_update",
                "event_id": selection.event_id,
                "answer": selection.message,
                "status": selection.status,
            })

        matching_event_data_hold = pending_hold is not None

        if matching_event_data_hold:
            reservation = ctx.queue.reserve(True)
            if reservation is None:
                raise ServiceUnavailableError(messages.text("api.queue_full_event_detail"))
            try:
                event_data_reply = apply_event_data_reply(
                    ctx.deps,
                    ctx.main_agent,
                    text,
                    caller_identity,
                    conversation_id,
                    prior_messages,
                    event_data_event_id,
                )
            except OrchestrationParseError as exc:
                ctx.queue.release_reservation(reservation)
                logger.warning(
                    "event data reply failed validation — abandoning hold to prevent infinite loop",
                    extra={"event": "event_data_reply_invalid", "reason": str(exc), "trace_id": trace_id},
                )
                # Resolve the stuck hold so the user is not trapped in an infinite loop.
                # Fall through: the message will be treated as a new request.
                try:
                    ctx.deps.persistence.resolve_held_event(
                        "event_data",
                        pending_hold["hold_id"],
                        {"resolved_by": "system:abandoned_parse_error"},
                    )
                except Exception:
                    logger.exception(
                        "failed to abandon event-data hold after parse error",
                        extra={"event": "event_data_hold_abandon_failed", "trace_id": trace_id},
                    )
                matching_event_data_hold = False
            except Exception:
                ctx.queue.release_reservation(reservation)
                raise
            if event_data_reply is not None and event_data_reply.ambiguous_event_ids:
                ctx.queue.release_reservation(reservation)
                answer = messages.text(
                    "api.event_detail_ambiguous",
                    count=str(len(event_data_reply.ambiguous_event_ids)),
                )
                _remember("assistant", answer)
                return jsonify(
                    {
                        "taken_as": "clarification",
                        "status": "ambiguous_event_data_hold",
                        "pending_event_ids": list(event_data_reply.ambiguous_event_ids),
                        "answer": answer,
                    }
                )

            if event_data_reply is not None:
                event_id = event_data_reply.event_id
                if not event_data_reply.updates:
                    ctx.queue.release_reservation(reservation)
                    _remember("assistant", event_data_reply.message, event_id)
                    return jsonify(
                        {
                            "taken_as": "clarification",
                            "event_id": event_id,
                            "answer": event_data_reply.message,
                            "status": "waiting_for_event_data",
                        }
                    )

                def _resume_waiting_work() -> None:
                    """Continue the held event after the reporter supplied missing fields."""

                    with trace_context(trace_id):
                        resume_after_event_data(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

                ctx.queue.submit(
                    WorkItem(
                        (event_id, _resume_waiting_work),
                        trace_id=trace_id,
                        priority=0,
                        deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                        concurrency_keys=work_concurrency_keys(caller_identity, scoped_agent),
                    ),
                    reservation,
                )
                _remember("assistant", event_data_reply.message, event_id)
                return jsonify(
                    {
                        "taken_as": "event_update",
                        "event_id": event_id,
                        "updated_fields": sorted(event_data_reply.updates),
                        "answer": event_data_reply.message,
                        "status": "queued",
                    }
                ), 202
            ctx.queue.release_reservation(reservation)

        # Fast Path for known buttons / deterministic protocol selection
        # button -> known protocol -> RBAC -> approval if required -> agent -> approved tool
        # An explicit hint/button names any declared protocol regardless of which agent the
        # group is bound to -- the bound agent is a context hint for LLM-driven protocol
        # selection only (orchestrator/group_routing.py::scope_deps), never a restriction on
        # an explicit, caller-asserted protocol name.
        matched_protocol_name = request_payload.get("protocol_hint") or KNOWN_BUTTON_PROTOCOLS.get(str(text).strip())
        matched_protocol = ctx.deps.protocol_set.get(matched_protocol_name) if matched_protocol_name else None

        if matched_protocol is not None:
            received_at = utc_now_storage()
            is_commander = level >= PermissionLevel.COMMANDER
            needs_approval = protocol_requires_approval(matched_protocol, is_commander)

            if needs_approval:
                require(level, RequestedOperation.REQUEST_ACTION)
                reservation = ctx.queue.reserve(False)
                if reservation is None:
                    raise ServiceUnavailableError(messages.text("api.queue_full"))
                deadline_at = storage_timestamp(
                    datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds)
                )
                try:
                    event_id = begin_request(
                        ctx.deps, text, received_at, sender_identity, source_message_id,
                        conversation_id=conversation_id, deadline_at=deadline_at,
                        sender_permission_level=level.name.lower(),
                    )
                except Exception:
                    ctx.queue.release_reservation(reservation)
                    raise

                def _work_fast_path() -> None:
                    """Continue a button/hint-selected protocol after its risk check."""

                    with trace_context(trace_id):
                        continue_from_risk_assessment(
                            ctx.deps,
                            event_id,
                            ctx.main_agent,
                            ctx.insights_agent,
                            selected_protocol=matched_protocol,
                        )

                ctx.queue.submit(
                    WorkItem(
                        (event_id, _work_fast_path),
                        trace_id=trace_id,
                        deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                        concurrency_keys=work_concurrency_keys(sender_identity, scoped_agent),
                    ),
                    reservation,
                )
                _remember("assistant", messages.text("api.queued_request_debug", task_id=event_id), event_id)
                return jsonify({
                    "taken_as": "request", "event_id": event_id, "status": "queued",
                    "answer": _queued_answer_text(messages, "request", event_id),
                }), 202

            if matched_protocol.name == "query_historical_incidents":
                require(level, RequestedOperation.ASK_QUESTION)
                caller_filter = None if is_commander else caller_identity
                try:
                    history_ans = ctx.deps.history_query_service.query(text, sender_identity_filter=caller_filter)
                    answer = history_ans.answer
                except Exception as exc:
                    answer = f"\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05e9\u05dc\u05d9\u05e4\u05ea \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d4: {exc}"
                _remember("assistant", answer)
                return jsonify({"taken_as": "question", "answer": answer, "protocol": matched_protocol.name})

            if len(matched_protocol.participating_agents) > 1:
                # A multi-domain, read-only picture: the Main Agent decides what to ask each
                # participating specialist, they answer from live tool data concurrently, the
                # recent event log is pulled for the window it chose, and the answer is written
                # from those findings only (orchestrator/situational_picture.py). A viewer's
                # recent-events view keeps the same ownership scope as any question they ask.
                require(level, RequestedOperation.ASK_QUESTION)
                picture = build_situational_picture(
                    ctx.main_agent,
                    matched_protocol,
                    ctx.deps.registry,
                    ctx.deps.history_query_service,
                    str(text),
                    caller_identity=caller_identity,
                    sender_identity_filter=None if is_commander else caller_identity,
                )
                _remember("assistant", picture.text)
                return jsonify({
                    "taken_as": "question",
                    "answer": picture.text,
                    "protocol": matched_protocol.name,
                    "provenance": picture.provenance(),
                })

            ag_name = matched_protocol.participating_agents[0]
            ag = ctx.deps.registry.get(ag_name)
            allowed_tools = list(matched_protocol.approved_tools)
            exposed_by_name = {tool_info.name: tool_info for tool_info in ag.exposed_tools()}
            require_op = (
                RequestedOperation.REQUEST_ACTION
                if any(exposed_by_name[name].side_effecting for name in allowed_tools if name in exposed_by_name)
                else RequestedOperation.ASK_QUESTION
            )
            require(level, require_op)
            with authenticated_request_identity(caller_identity):
                # A single read-only tool is already the answer. Calling it directly
                # skips a specialist model round-trip on attendance/status buttons.
                only_tool = allowed_tools[0] if len(allowed_tools) == 1 else None
                only_info = exposed_by_name.get(only_tool) if only_tool else None
                tool_fn = getattr(ag, only_tool) if only_tool and hasattr(ag, only_tool) else None
                if only_info is not None and not only_info.side_effecting and tool_fn is not None and _accepts_a_call_with_no_arguments(tool_fn):
                    answer = tool_fn()
                else:
                    res = ag.process(text, allowed_tools)
                    answer = res.text if res.status == "success" else f"\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05d4\u05e4\u05e2\u05dc\u05ea \u05e1\u05d5\u05db\u05df: {res.text}"
            _remember("assistant", answer)
            return jsonify({
                "taken_as": "question" if require_op == RequestedOperation.ASK_QUESTION else "event_update",
                "answer": answer,
                "protocol": matched_protocol.name,
            })

        message_plan = None
        planner_mode = optimization_policy.planner_mode
        if planner_mode in {"shadow", "merged"}:
            try:
                message_plan = plan_message(
                    ctx.main_agent,
                    ctx.deps.protocol_set.all(),
                    text,
                    ctx.deps.registry,
                    ctx.deps.history_query_service,
                    prior_messages,
                    system_context,
                )
            except OrchestrationParseError as exc:
                logger.warning(
                    "merged message planner failed validation",
                    extra={"event": "message_plan_invalid", "planner_mode": planner_mode, "reason": str(exc), "trace_id": trace_id},
                )
                if planner_mode == "merged":
                    answer = messages.text("api.clarify_check_record_do")
                    _remember("assistant", answer)
                    return jsonify({"taken_as": "clarification", "answer": answer})

        received_at = utc_now_storage()

        try:
            intent = message_plan.intent if planner_mode == "merged" and message_plan is not None else classify_intent(
                ctx.main_agent, ctx.deps.protocol_set.all(), text, prior_messages
            )
        except OrchestrationParseError as exc:
            raise RunFailureError(str(exc)) from exc
        except AgentInvocationError as exc:
            # A report must never vanish just because the model that classifies its intent is
            # unavailable -- unlike a parse failure (the model answered, just unusably), this is
            # a model-invocation failure with no event created yet. Persist the raw text now,
            # with its outcome already recorded as failed, so it survives for later triage/retry.
            # Scoped to AgentInvocationError specifically (timeout/model/output-parse/warmup/
            # tool-construction -- every real "model unavailable" shape), not a bare Exception:
            # an unexpected bug elsewhere must still fail loudly (a real 5xx), never be quietly
            # reinterpreted as "the model was unavailable" and smoothed into a handled 422.
            # instead of being lost with no trace (it would otherwise never reach begin_report,
            # which every other path already calls before running any model on the report).
            deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
            lost_event_id = begin_report(
                ctx.deps, text, "telegram", received_at, sender_identity, source_message_id,
                conversation_id=conversation_id, deadline_at=deadline_at,
                sender_permission_level=level.name.lower(),
                telegram_chat_id=str(telegram_chat_id) if telegram_chat_id is not None else None,
                telegram_chat_type=str(telegram_chat_type) if telegram_chat_type is not None else None,
                ack_message_id=str(ack_message_id) if ack_message_id is not None else None,
            )
            record_event_outcome(ctx.deps.persistence, lost_event_id, "failed", failure_reason=f"intent classification failed: {exc}")
            raise RunFailureError(f"intent classification failed: {exc}") from exc

        logger.info(
            "intent classified",
            extra={"event": "intent_classified", "intent": intent.intent, "reason": intent.reason, "trace_id": get_trace_id()},
        )

        if intent.intent == "needs_clarification":
            answer = intent.clarification_question or messages.text("api.clarify_action")
            _remember("assistant", answer)
            return jsonify({
                "taken_as": "clarification",
                "answer": answer,
            })

        if intent.intent == "conversational":
            require(level, RequestedOperation.CONVERSE)
            try:
                reply = (
                    message_plan.conversational_reply
                    if planner_mode == "merged" and message_plan is not None
                    else answer_conversationally(ctx.main_agent, text, system_context, prior_messages)
                )
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            _remember("assistant", reply)
            return jsonify({"taken_as": "conversational", "answer": reply})

        if intent.intent == "question":
            require(level, RequestedOperation.ASK_QUESTION)
            # A viewer's question may only see events they submitted; a commander
            # is unrestricted (filter stays None).
            caller_sender_identity_filter = None if level is PermissionLevel.COMMANDER else caller_identity
            picture_stem = ctx.loaded_profile.module_path.rsplit(".", 1)[-1]
            if question_requests_picture(text, picture_stem):
                protocol = picture_protocol(ctx.deps.protocol_set)
                if protocol is not None:
                    answer = read_picture_directly(protocol, ctx.deps.registry)
                    _remember("assistant", answer)
                    return jsonify({"taken_as": "question", "answer": answer})
            try:
                if planner_mode == "merged" and message_plan is not None and message_plan.question_selection is not None:
                    question_answer = answer_question_from_plan(
                        ctx.main_agent,
                        text,
                        message_plan.question_selection,
                        ctx.deps.registry,
                        ctx.deps.history_query_service,
                        max_fanout=optimization_policy.specialist_fanout,
                        caller_sender_identity_filter=caller_sender_identity_filter,
                        conversation_messages=prior_messages,
                    )
                    answer = question_answer.text
                    provenance = question_answer.provenance
                else:
                    answer = answer_question(
                        ctx.main_agent, text, ctx.deps.registry, ctx.deps.history_query_service,
                        caller_sender_identity_filter=caller_sender_identity_filter,
                        conversation_messages=prior_messages,
                    )
                    provenance = None
            except OrchestrationParseError as exc:
                raise RunFailureError(str(exc)) from exc
            _remember("assistant", answer)
            response_payload = {"taken_as": "question", "answer": answer}
            if provenance is not None:
                response_payload["provenance"] = provenance
            return jsonify(response_payload)

        if intent.intent == "report":
            require(level, RequestedOperation.REPORT_EVENT)
            reservation = ctx.queue.reserve(False)
            if reservation is None:
                raise ServiceUnavailableError(messages.text("api.queue_full"))
            deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
            try:
                event_id = begin_report(
                    ctx.deps, text, "telegram", received_at, sender_identity, source_message_id,
                    conversation_id=conversation_id, deadline_at=deadline_at,
                    sender_permission_level=level.name.lower(),
                    telegram_chat_id=str(telegram_chat_id) if telegram_chat_id is not None else None,
                    telegram_chat_type=str(telegram_chat_type) if telegram_chat_type is not None else None,
                    ack_message_id=str(ack_message_id) if ack_message_id is not None else None,
                )
            except Exception:
                ctx.queue.release_reservation(reservation)
                raise

            # Item 9: the direct lane runs synchronously, outside the serial queue, before this
            # report would otherwise be queued for the full pipeline -- a cheap classification
            # call handles it immediately when it's a simple, low-stakes, unambiguous action;
            # anything else (a threat/risk indicator, ambiguity, a missing parameter, or simply
            # not matching a direct-lane-eligible tool) returns None and falls through to the
            # ordinary queued path below, completely unchanged.
            direct_lane_result = attempt_direct_lane(ctx.deps, ctx.main_agent, event_id, sender_identity, text)
            if direct_lane_result is not None:
                ctx.queue.release_reservation(reservation)
                finished_event = ctx.deps.persistence.fetch_event(event_id)
                answer = (finished_event or {}).get("report_text") or _queued_answer_text(messages, "report", event_id)
                _remember("assistant", answer, event_id)
                return jsonify({
                    "taken_as": "report", "event_id": event_id, "status": "completed",
                    "outcome": direct_lane_result.outcome, "answer": answer,
                })

            def _work() -> None:
                """Run report extraction for a newly queued report."""

                with trace_context(trace_id):
                    run_report_extraction(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

            ctx.queue.submit(
                WorkItem(
                    (event_id, _work), trace_id=trace_id,
                    deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                    concurrency_keys=work_concurrency_keys(sender_identity, scoped_agent),
                ),
                reservation,
            )
            _remember("assistant", messages.text("api.queued_report_debug", task_id=event_id), event_id)
            return jsonify({
                "taken_as": "report", "event_id": event_id, "status": "queued",
                "answer": _queued_answer_text(messages, "report", event_id),
            }), 202

        if intent.intent != "request":
            raise RunFailureError(f"unsupported message intent: {intent.intent!r}")

        require(level, RequestedOperation.REQUEST_ACTION)
        picture_stem = ctx.loaded_profile.module_path.rsplit(".", 1)[-1]
        if question_requests_picture(text, picture_stem):
            protocol = picture_protocol(ctx.deps.protocol_set)
            if protocol is not None:
                answer = read_picture_directly(protocol, ctx.deps.registry)
                _remember("assistant", answer)
                return jsonify({"taken_as": "request", "answer": answer})
        is_commander = level >= PermissionLevel.COMMANDER
        reservation = ctx.queue.reserve(False)
        if reservation is None:
            raise ServiceUnavailableError(messages.text("api.queue_full"))
        deadline_at = storage_timestamp(datetime.now(timezone.utc) + timedelta(seconds=optimization_policy.job_deadline_seconds))
        try:
            event_id = begin_request(
                ctx.deps, text, received_at, sender_identity, source_message_id,
                conversation_id=conversation_id, deadline_at=deadline_at,
                sender_permission_level=level.name.lower(),
                telegram_chat_id=str(telegram_chat_id) if telegram_chat_id is not None else None,
                telegram_chat_type=str(telegram_chat_type) if telegram_chat_type is not None else None,
                ack_message_id=str(ack_message_id) if ack_message_id is not None else None,
            )
        except Exception:
            ctx.queue.release_reservation(reservation)
            raise

        def _work() -> None:
            """Continue a queued request from risk assessment."""

            with trace_context(trace_id):
                continue_from_risk_assessment(ctx.deps, event_id, ctx.main_agent, ctx.insights_agent)

        ctx.queue.submit(
            WorkItem(
                (event_id, _work), trace_id=trace_id,
                deadline_monotonic=time.monotonic() + optimization_policy.job_deadline_seconds,
                    concurrency_keys=work_concurrency_keys(sender_identity, scoped_agent),
            ),
            reservation,
        )
        _remember("assistant", messages.text("api.queued_request_debug", task_id=event_id), event_id)
        return jsonify({
            "taken_as": "request", "event_id": event_id, "status": "queued",
            "answer": _queued_answer_text(messages, "request", event_id),
        }), 202

    return blueprint
