"""Behind-The-Scenes trace aggregation and diagnostics engine.

Extracts real execution stages, agent collaboration graphs, tool side-effects,
and performance metrics from raw SQLite log_entries without altering any core logic.
Strictly read-only and safe for operator diagnosis.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


SENSITIVE_FIELD_NAMES = frozenset({
    "prompt",
    "prompt_text",
    "response",
    "response_text",
    "system_prompt",
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
    "private_key",
    "messages",
    "reason",
    "reasoning",
    "task",
    "task_text",
    "result",
    "result_text",
    "output",
    "raw_output",
    "model_output",
    "chain_of_thought",
    "internal_reasoning",
})


def _safe_str(value: Any, limit: int = 140) -> str:
    """Safe str."""

    if value is None:
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _trace_status(value: Any, *, event: str | None = None) -> str:
    """Normalize recorded statuses without turning missing evidence into success."""
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "succeeded", "completed", "ok", "selected"}:
        return "success"
    if normalized in {"failed", "failure", "error", "blocked", "rejected"}:
        return "failed"
    if normalized in {"running", "in_progress", "started"}:
        return "running"
    if normalized in {"waiting", "awaiting_approval", "ambiguous"}:
        return "waiting"
    if event == "provider_request_finished":
        return "success"
    if event in {"provider_request_failed", "step_failed", "specialist_failed", "specialist_timeout"}:
        return "failed"
    return "unknown"


def _number(value: Any, default: float = 0.0) -> float:
    """Number."""

    try:
        return default if value is None else float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_timestamp(val: Any) -> datetime | None:
    """Parse timestamp."""

    if not val or not isinstance(val, str):
        return None
    try:
        # Normalize ISO strings
        cleaned = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def sanitize_trace_details(details: dict[str, Any]) -> dict[str, Any]:
    """Strip full prompts, raw responses, and credentials before returning to UI."""
    def clean_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean_value(item)
                for key, item in value.items()
                if str(key).strip().lower() not in SENSITIVE_FIELD_NAMES
            }
        if isinstance(value, (list, tuple)):
            return [clean_value(item) for item in value]
        if isinstance(value, str) and len(value) > 300:
            return _safe_str(value, 300)
        return value

    return clean_value(details)


def _profiles_match(expected: str | None, actual: str | None) -> bool:
    """Profiles match."""

    if not expected or not actual:
        return True
    if expected == actual:
        return True
    exp_norm = expected.strip().lower().replace(" ", "_")
    act_norm = actual.strip().lower().replace(" ", "_")
    if exp_norm == act_norm:
        return True
    exp_stem = exp_norm.split(".")[-1]
    act_stem = act_norm.split(".")[-1]
    return exp_stem == act_stem or exp_stem in act_norm or act_stem in exp_norm


def _agent_icon(name: str) -> str:
    """Agent icon."""

    return {
        "main_agent": "🤖", "roster_agent": "📋", "team_status_agent": "📋",
        "surveillance_agent": "👁️", "neighboring_forces_agent": "🤝",
        "security_agent": "🛡️", "fire_agent": "🚒", "firefighting_agent": "🚒",
        "fire_station": "🚒", "medical_agent": "🚑", "engineering_agent": "⚙️",
        "insights_agent": "💡", "report_composer": "📊", "persistence_store": "🗄️",
        "user_client": "👤", "final_outcome": "🎯",
    }.get(name, "🤖")


def _agent_display_name(name: str) -> str:
    """Agent display name."""

    return {
        "main_agent": "Main Agent (orchestrator)", "team_status_agent": "Attendance and personnel specialist",
        "roster_agent": "Attendance and personnel specialist", "surveillance_agent": "Surveillance and drone specialist",
        "neighboring_forces_agent": "Neighboring forces and support specialist", "security_agent": "Security and readiness specialist",
        "fire_agent": "Fire and rescue specialist", "firefighting_agent": "Fire and rescue specialist",
        "fire_station": "Fire and rescue specialist", "medical_agent": "Medical and evacuation specialist",
        "engineering_agent": "Engineering and infrastructure specialist", "insights_agent": "Insights and trends agent",
        "report_composer": "Operational report composer", "persistence_store": "Database and verification",
        "user_client": "User / reporting channel", "final_outcome": "Summary reply to the user",
    }.get(name, name)


def _execution_graph(entries: list[dict[str, Any]], outcome: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build only evidenced invocation, provider, tool, and persisted-outcome links."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    provider_events: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    result_events: list[dict[str, Any]] = []
    step_events: list[dict[str, Any]] = []
    routing_events: list[dict[str, Any]] = []
    persistence_events: list[dict[str, Any]] = []
    composition_events: list[dict[str, Any]] = []
    invocation_completions: dict[str, dict[str, Any]] = {}

    def ensure_invocation(invocation_id: Any, agent_name: Any, entry: dict[str, Any], *, legacy: bool = False):
        if not isinstance(invocation_id, str) or not invocation_id.strip():
            return None
        if not isinstance(agent_name, str) or not agent_name.strip() or agent_name == "unattributed" or len(agent_name) > 100:
            return None
        node = by_id.get(invocation_id)
        event = entry.get("event")
        observed_start = entry.get("started_at")
        if observed_start is None and event == "agent_invocation_started":
            observed_start = entry.get("timestamp")
        if node is None:
            node = {
                "id": f"invocation_{invocation_id}", "type": "invocation", "label": agent_name,
                "sublabel": entry.get("stage") or "Agent Invocation", "icon": "🤖",
                "status": "unknown", "started_at": observed_start,
                "task": _safe_str(entry.get("task_summary"), 120),
                "allowed_tools": entry.get("allowed_tools") or [], "llm_calls": [], "tools": [],
                "invocation_id": invocation_id, "legacy": legacy,
            }
            nodes.append(node)
            by_id[invocation_id] = node
        elif node.get("legacy") and not legacy:
            node["legacy"] = False
        node["agent_name"] = agent_name
        node["label"] = agent_name
        node["parent_invocation_id"] = entry.get("parent_invocation_id") or node.get("parent_invocation_id")
        node["parent_agent"] = entry.get("parent_agent") or node.get("parent_agent")
        node["protocol_name"] = entry.get("protocol_name") or node.get("protocol_name")
        node["stage"] = entry.get("stage") or node.get("stage")
        if observed_start:
            node["started_at"] = node.get("started_at") or observed_start
            if event == "agent_invocation_started":
                node["started_at_source"] = "agent_invocation_started"
            elif event in {"provider_request_finished", "provider_request_failed"} and not node.get("started_at_source"):
                node["started_at_source"] = "first_provider_request"
        return node
    has_input = any(e.get("event") in {"report_received", "request_received"} for e in entries)
    if has_input:
        nodes.append({"id": "input", "type": "user", "label": "Message received", "status": "success", "icon": "👤"})
    root_status = (
        "success" if outcome in {"succeeded", "completed", "closed_on_precedent"}
        else ("failed" if outcome in {"failed", "failure", "error"} else ("unknown" if outcome else "running"))
    )
    root = {"id": "orchestrator", "type": "main", "label": "Orchestrator", "status": root_status, "icon": "🤖"}
    nodes.append(root)
    if has_input:
        edges.append({"id": "input_orchestrator", "source": "input", "target": "orchestrator", "type": "input", "label": "Ingestion", "status": "completed"})

    for index, entry in enumerate(entries):
        event = entry.get("event")
        invocation_id = entry.get("invocation_id")
        if event in {"group_scope_applied", "agent_selection", "protocol_selection"}:
            routing_events.append(entry)
        if event in {"event_data_saved", "attendance_cycle_opened", "persistence_verified", "verification_succeeded", "verification_failed"}:
            persistence_events.append(entry)
        if event in {"picture_planned", "report_composed", "picture_composed", "response_composed", "question_composition"}:
            composition_events.append(entry)
        if event == "agent_invocation_started" and invocation_id:
            node = ensure_invocation(invocation_id, entry.get("agent_name") or entry.get("agent"), entry)
            if node is not None:
                node["status"] = "running"
        elif event in {"agent_invocation_finished", "agent_invocation_failed"} and invocation_id:
            node = ensure_invocation(invocation_id, entry.get("agent_name") or entry.get("agent"), entry)
            if node is None:
                continue
            node["status"] = _trace_status(entry.get("status"), event=event)
            node["finished_at"] = entry.get("timestamp")
            node["duration_ms"] = entry.get("duration_ms")
            if not node.get("started_at") and node.get("duration_ms") is not None:
                finished_at = _parse_timestamp(entry.get("timestamp"))
                if finished_at:
                    node["started_at"] = (finished_at - timedelta(milliseconds=_number(node["duration_ms"]))).isoformat()
                    node["started_at_source"] = "inferred_from_agent_duration"
            node["result"] = f"{entry.get('result_chars', 0)} characters" if node["status"] == "success" else _safe_str(entry.get("error_type"))
            invocation_completions[invocation_id] = entry
        elif event == "model_invocation_finished" and invocation_id:
            node = ensure_invocation(invocation_id, entry.get("agent_name") or entry.get("agent"), entry)
            if node is not None:
                # Crew/model completion proves model work occurred, not that Agent.process
                # returned a valid result. Keep its timing separate from Agent lifecycle.
                node["model_status"] = _trace_status(entry.get("status"), event=event)
                node["model_finished_at"] = entry.get("timestamp")
                node["model_duration_ms"] = entry.get("duration_ms") or entry.get("latency_ms")
                node["model_input_tokens"] = entry.get("input_tokens")
                node["model_output_tokens"] = entry.get("output_tokens")
        elif event == "model_invocation_finished" and not invocation_id:
            # Preserve the completion evidence, but do not label it as an Agent invocation.
            node = {
                "id": f"legacy_model_completion_{index}", "type": "model",
                "label": f"Model completion · {entry.get('agent') or 'unknown Agent'} · missing invocation id",
                "sublabel": entry.get("stage") or "unknown stage",
                "icon": "🤖", "status": _trace_status(entry.get("status"), event=event),
                "finished_at": entry.get("timestamp"), "duration_ms": entry.get("latency_ms"),
                "llm_calls": [], "call_count": 0, "legacy": True, "model_completion_event": True,
                "agent_name": entry.get("agent"), "attribution_status": "unattributed",
                "task": "A model completion event was stored without agent_invocation_id; it is not proof of an identified Agent invocation.",
                "result": "Raw output is not shown.",
            }
            finish_dt = _parse_timestamp(entry.get("timestamp"))
            if finish_dt and isinstance(entry.get("latency_ms"), (int, float)):
                node["started_at"] = (finish_dt - timedelta(milliseconds=entry["latency_ms"])).isoformat()
            nodes.append(node)
        elif event in {"provider_request_finished", "provider_request_failed"}:
            provider_invocation_id = entry.get("agent_invocation_id") or invocation_id
            provider_agent_name = entry.get("agent_name")
            ensure_invocation(provider_invocation_id, provider_agent_name, entry)
            provider_events.append(entry)
        elif event in {"tool_call", "tool_blocked"}:
            tool_entry = dict(entry)
            tool_entry["invocation_id"] = entry.get("agent_invocation_id") or invocation_id
            ensure_invocation(tool_entry["invocation_id"], entry.get("agent_name") or entry.get("agent"), tool_entry)
            tool_events.append(tool_entry)
        elif event in {"step_result", "specialist_finished"}:
            if invocation_id:
                result_agent = entry.get("agent_name") or entry.get("agent")
                completed_node = ensure_invocation(invocation_id, result_agent, entry)
                if completed_node is not None:
                    step_success = entry.get("succeeded")
                    completed_node["status"] = (
                        ("success" if step_success else "failed")
                        if isinstance(step_success, bool)
                        else _trace_status(entry.get("status"), event=event)
                    )
                    completed_node["finished_at"] = entry.get("timestamp")
                    completed_node["duration_ms"] = entry.get("duration_ms") or completed_node.get("duration_ms")
                    if not completed_node.get("started_at") and completed_node.get("duration_ms") is not None:
                        finished_at = _parse_timestamp(entry.get("timestamp"))
                        if finished_at:
                            completed_node["started_at"] = (
                                finished_at - timedelta(milliseconds=_number(completed_node["duration_ms"]))
                            ).isoformat()
                            completed_node["started_at_source"] = "inferred_from_step_duration"
                invocation_completions.setdefault(invocation_id, entry)
            result_events.append(entry)
        elif event == "step_start":
            step_events.append({"start": entry, "finish": None})

    for entry in entries:
        if entry.get("event") not in {"step_result", "step_failed"}:
            continue
        agent_name = entry.get("agent")
        step_id = entry.get("step_id")
        step_index = entry.get("step_index")
        for step in reversed(step_events):
            start = step["start"]
            same_step = (
                start.get("step_id") == step_id if step_id is not None
                else start.get("step_index") == step_index
            )
            if start.get("agent") == agent_name and same_step and step["finish"] is None:
                step["finish"] = entry
                break

    for node in [n for n in nodes if n["type"] == "invocation"]:
        parent_id = node.get("parent_invocation_id")
        parent = f"invocation_{parent_id}" if parent_id in by_id else "orchestrator"
        edges.append({
            "id": f"invoke_{node['id']}", "source": parent, "target": node["id"],
            "type": "invocation", "label": _safe_str(node.get("sublabel"), 35),
            "status": "active" if node["status"] == "running" else "completed",
        })

    for index, entry in enumerate(result_events):
        if entry.get("invocation_id") in invocation_completions:
            continue
        node = by_id.get(entry.get("invocation_id"))
        if node is None:
            continue
        received = entry.get("succeeded") if entry.get("event") == "step_result" else entry.get("status") == "success"
        if not received and entry.get("event") != "step_result":
            continue
        parent_id = node.get("parent_invocation_id")
        parent = f"invocation_{parent_id}" if parent_id in by_id else "orchestrator"
        edges.append({
            "id": f"result_{index}_{node['id']}", "source": node["id"], "target": parent,
            "type": "result", "label": "Step result returned" if received else "Step failure returned",
            "status": "completed" if received else "failed",
        })

    for entry in provider_events:
        invocation_id = entry.get("agent_invocation_id") or entry.get("invocation_id")
        node = by_id.get(invocation_id)
        call = {
            "call_id": entry.get("provider_request_id") or entry.get("call_id"), "provider_request_id": entry.get("provider_request_id") or entry.get("call_id"),
            "trace_id": entry.get("trace_id"),
            "agent_name": entry.get("agent_name") or entry.get("agent"),
            "agent_invocation_id": invocation_id,
            "parent_agent": entry.get("parent_agent"), "parent_invocation_id": entry.get("parent_invocation_id"),
            "stage": entry.get("stage"), "purpose": (
                entry.get("purpose") if entry.get("purpose") not in {None, "", "unattributed"}
                else {"extraction": "event_extraction"}.get(entry.get("stage"), "unattributed")
            ),
            "protocol_name": entry.get("protocol_name"), "tool_name": entry.get("tool_name"),
            "sequence_number": entry.get("sequence_number"),
            "started_at": entry.get("started_at"), "finished_at": entry.get("finished_at"),
            "model": _safe_str(entry.get("model"), 65),
            "latency_ms": entry.get("latency_ms"), "status": entry.get("status"),
            "finish_reason": entry.get("finish_reason"), "input_tokens": entry.get("input_tokens"),
            "output_tokens": entry.get("output_tokens"), "cache_tokens": entry.get("cache_tokens"),
            "call_type": entry.get("call_type"),
            "result_summary": ("Provider request failed" if entry.get("status") == "error" else
                ("Provider requested a tool" if "tool_call" in str(entry.get("call_type") or "").lower() else "Provider returned a response")),
        }
        model_id = f"provider_request_{_safe_str(entry.get('provider_request_id') or entry.get('call_id') or index, 64)}"
        purpose_label = call["purpose"] if call["purpose"] != "unattributed" else (call["stage"] or "unattributed")
        provider_status = _trace_status(entry.get("status"), event=entry.get("event"))
        known_agent = call["agent_name"] not in {None, "", "unattributed"}
        if node is not None:
            model_label = "LLM: " + purpose_label
            attribution_status = "attributed"
        elif known_agent:
            model_label = f"LLM: {call['agent_name']} · missing invocation id"
            attribution_status = "partial"
        else:
            model_label = "LLM: unattributed"
            attribution_status = "unattributed"
        model_node = {
            "id": model_id, "type": "model",
            "label": model_label,
            "sublabel": call["stage"] or "unknown stage", "icon": "🧠",
            "status": provider_status,
            "duration_ms": call["latency_ms"], "llm_calls": [call], "call_count": 1,
            "task": ("Purpose: " + purpose_label) if node is not None else (
                f"Known Agent: {call['agent_name']}; agent_invocation_id was not stored."
                if known_agent else "No reliable link to an agent invocation was stored."
            ),
            "result": call["result_summary"], "agent_invocation_id": call["agent_invocation_id"],
            "agent_name": call["agent_name"], "protocol_name": call["protocol_name"],
            "attribution_status": attribution_status,
            "started_at": call["started_at"], "finished_at": call["finished_at"],
        }
        nodes.append(model_node)
        if node is not None:
            node["llm_calls"].append(call)
            node["protocol_name"] = call["protocol_name"] or node.get("protocol_name")
            node.setdefault("provider_node_ids", []).append(model_id)
            edges.append({
                "id": f"provider_{index}_{model_id}", "source": node["id"], "target": model_id,
                "type": "llm", "label": "LLM: " + purpose_label,
                "status": "failed" if provider_status == "failed" else ("active" if provider_status == "running" else "completed"),
            })
        else:
            # Keep provider work visible when only partial attribution survived.
            # This edge explicitly describes missing linkage; it does not invent an Agent invocation.
            edges.append({
                "id": f"provider_unlinked_{index}_{model_id}", "source": "orchestrator", "target": model_id,
                "type": "unattributed", "label": "Provider call · partial attribution" if known_agent else "Provider call · unattributed",
                "status": "failed" if provider_status == "failed" else ("active" if provider_status == "running" else "completed"),
            })

    tool_nodes_by_invocation: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(tool_events):
        invocation_id = entry.get("invocation_id")
        invocation = by_id.get(invocation_id)
        tool_status = "failed" if entry.get("event") == "tool_blocked" else _trace_status(entry.get("status"), event=entry.get("event"))
        explicit_verification = entry.get("verification_status") or entry.get("verification")
        if entry.get("event") == "tool_blocked":
            explicit_verification = "blocked"
        elif explicit_verification not in {"verified", "unverified", "failed", "verification_unavailable", "read_only", "blocked"}:
            explicit_verification = "read_only" if not bool(entry.get("side_effecting")) else "unverified"
        duration_ms = entry.get("duration_ms")
        if duration_ms is None:
            duration_ms = _number(entry.get("duration_seconds")) * 1000
        tool_node = {
            "id": f"tool_call_{index}", "type": "tool", "label": entry.get("tool_name") or entry.get("tool") or "tool",
            "icon": "🔧", "status": tool_status,
            "duration_ms": round(_number(duration_ms), 1),
            "summary": _safe_str(entry.get("result_summary"), 120),
            "timestamp": entry.get("timestamp"), "agent_invocation_id": invocation_id,
            "caller_agent_name": entry.get("agent_name") or entry.get("agent"),
            "side_effecting": bool(entry.get("side_effecting")),
            "verification": explicit_verification,
            "verification_note": _safe_str(entry.get("verification_note"), 120) or (
                "Tool was blocked" if explicit_verification == "blocked" else
                ("Explicit verification present" if explicit_verification == "verified" else
                 ("Read only" if explicit_verification == "read_only" else "Persistence verification unavailable"))
            ),
            "started_at": entry.get("started_at"), "finished_at": entry.get("finished_at"),
        }
        nodes.append(tool_node)
        if invocation_id:
            tool_nodes_by_invocation.setdefault(invocation_id, []).append(tool_node)
        if invocation is not None:
            invocation["tools"].append(tool_node["id"])
            invocation["tool_node_ids"] = invocation.get("tool_node_ids", []) + [tool_node["id"]]
            prior_decisions = [
                model for model in nodes
                if model.get("type") == "model"
                and model.get("agent_invocation_id") == invocation_id
                and "tool_call" in str((model.get("llm_calls") or [{}])[0].get("call_type") or "").lower()
                and (_parse_timestamp((model.get("llm_calls") or [{}])[0].get("finished_at")) or datetime.min.replace(tzinfo=timezone.utc)) <= (_parse_timestamp(entry.get("timestamp")) or datetime.max.replace(tzinfo=timezone.utc))
            ]
            source_id = prior_decisions[-1]["id"] if prior_decisions else invocation["id"]
            edges.append({
                "id": f"tool_edge_{index}", "source": source_id, "target": tool_node["id"],
                "type": "tool_call", "label": "Tool call",
                "status": "failed" if tool_status == "failed" else ("active" if tool_status == "running" else ("completed" if tool_status == "success" else "pending")),
            })
        else:
            tool_node["details"] = (
                f"Recorded caller={tool_node['caller_agent_name']}, but agent_invocation_id was not stored; "
                "No inferred Agent invocation is shown."
                if tool_node["caller_agent_name"] else "No agent-invocation attribution was recorded; no inferred link is shown."
            )

    for index, entry in enumerate(provider_events):
        invocation_id = entry.get("agent_invocation_id") or entry.get("invocation_id")
        tool_name = entry.get("tool_name")
        if not invocation_id or not tool_name:
            continue
        provider_node_id = f"provider_request_{_safe_str(entry.get('provider_request_id') or entry.get('call_id') or index, 64)}"
        started_at = _parse_timestamp(entry.get("started_at"))
        candidates = [
            tool for tool in tool_nodes_by_invocation.get(invocation_id, [])
            if tool.get("label") == tool_name
            and (_parse_timestamp(tool.get("timestamp")) is not None)
            and started_at is not None
            and _parse_timestamp(tool.get("timestamp")) <= started_at
        ]
        if candidates:
            tool = candidates[-1]
            edges.append({
                "id": f"tool_result_{index}_{provider_node_id}", "source": tool["id"], "target": provider_node_id,
                "type": "tool_result", "label": "Tool result to model", "status": "completed",
            })

    for invocation_id, completion in invocation_completions.items():
        invocation = by_id.get(invocation_id)
        if invocation is None:
            continue
        result_id = f"agent_result_{invocation_id}"
        completion_succeeded = completion.get("succeeded")
        completion_status = (
            ("success" if completion_succeeded else "failed")
            if isinstance(completion_succeeded, bool)
            else _trace_status(completion.get("status"), event=completion.get("event"))
        )
        result_node = {
            "id": result_id, "type": "result", "label": f"Result of {invocation.get('label', 'Agent')}",
            "sublabel": "Agent invocation result", "icon": "📥",
            "status": completion_status,
            "details": "The agent invocation finished; raw output is not shown.",
        }
        nodes.append(result_node)
        children = []
        for model in nodes:
            if model.get("type") == "model" and model.get("agent_invocation_id") == invocation_id:
                when = _parse_timestamp((model.get("llm_calls") or [{}])[0].get("finished_at"))
                children.append((when or datetime.min.replace(tzinfo=timezone.utc), model["id"]))
        for tool in tool_nodes_by_invocation.get(invocation_id, []):
            when = _parse_timestamp(tool.get("timestamp"))
            children.append((when or datetime.min.replace(tzinfo=timezone.utc), tool["id"]))
        source_id = max(children)[1] if children else invocation["id"]
        edges.append({
            "id": f"result_edge_{invocation_id}", "source": source_id, "target": result_id,
            "type": "agent_result", "label": "Invocation result",
            "status": "completed" if completion_status == "success" else ("failed" if completion_status == "failed" else "pending"),
        })
        parent_id = invocation.get("parent_invocation_id")
        parent_target = f"invocation_{parent_id}" if parent_id in by_id else "orchestrator"
        edges.append({
            "id": f"result_return_{invocation_id}", "source": result_id, "target": parent_target,
            "type": "result", "label": "Result returned",
            "status": "completed" if completion_status == "success" else ("failed" if completion_status == "failed" else "pending"),
        })

    for route_index, entry in enumerate(routing_events):
        event = entry.get("event")
        route_id = f"routing_{route_index}_{event}"
        if event == "group_scope_applied":
            target_agent = entry.get("agent_name") or entry.get("agent") or entry.get("preferred_agent_hint")
            node = {
                "id": route_id, "type": "routing", "routing_kind": "group_target",
                "label": "Group routing hint", "sublabel": _safe_str(target_agent or "unspecified", 70),
                "icon": "🧭", "status": "success" if target_agent else "unknown",
                "target_agent": _safe_str(target_agent, 100),
                "details": "A group target/hint was recorded; that is not proof an agent ran or that others were blocked.",
                "timestamp": entry.get("timestamp"),
            }
        elif event == "agent_selection":
            selected = entry.get("selected_agents") or entry.get("chosen_agents") or []
            if isinstance(selected, str):
                selected = [selected]
            if not isinstance(selected, (list, tuple)):
                selected = []
            selected = [_safe_str(name, 80) for name in selected if isinstance(name, str) and name.strip()]
            recorded_status = entry.get("routing_decision_status") or entry.get("decision_status") or entry.get("status")
            route_status = _trace_status(recorded_status, event=event)
            if route_status == "unknown" and selected:
                route_status = "success"
            routing_mode = _safe_str(entry.get("routing_mode") or "not stored", 60)
            source_stage = _safe_str(entry.get("source_stage") or entry.get("stage") or "not stored", 60)
            node = {
                "id": route_id, "type": "routing", "routing_kind": "agent_selection",
                "label": "Agent target selection", "sublabel": ", ".join(selected) or "no targets stored",
                "icon": "🧭", "status": route_status,
                "selected_agents": selected, "routing_mode": routing_mode,
                "source_stage": source_stage, "selection_count": len(selected),
                "timestamp": entry.get("timestamp"),
                "details": (
                    f"Recorded routing targets: {', '.join(selected) if selected else 'no explicit target'}. "
                    "A logical selection is not proof an Agent ran; invocations appear separately only if observed."
                ),
            }
        else:  # protocol_selection
            protocol_name = _safe_str(entry.get("protocol_name") or "not stored", 100)
            protocol_owner = _safe_str(entry.get("protocol_owner") or entry.get("owner") or "", 80)
            candidates = entry.get("candidate_names") or []
            if isinstance(candidates, str):
                candidates = [candidates]
            candidates = [_safe_str(name, 80) for name in candidates if isinstance(name, str)] if isinstance(candidates, list) else []
            raw_status = entry.get("status")
            route_status = "waiting" if raw_status == "ambiguous" else _trace_status(raw_status, event=event)
            node = {
                "id": route_id, "type": "routing", "routing_kind": "protocol_selection",
                "label": "Protocol selection", "sublabel": protocol_name,
                "icon": "🧭", "status": route_status, "protocol_name": protocol_name,
                "protocol_owner": protocol_owner,
                "candidate_names": candidates, "timestamp": entry.get("timestamp"),
                "details": (
                    f"Recorded protocol: {protocol_name}. "
                    + (f"Protocol owner from the event: {protocol_owner}. " if protocol_owner else "")
                    + "Protocol or domain-owner selection is not proof an Agent ran."
                ),
            }
        nodes.append(node)
        edges.append({
            "id": f"route_edge_{route_index}", "source": "orchestrator", "target": route_id,
            "type": "routing", "label": node["label"], "status": "completed" if node["status"] == "success" else node["status"],
        })

    for step_index, step in enumerate(step_events):
        start, finish = step["start"], step["finish"]
        agent_name = start.get("agent") or "unknown agent"
        step_id = start.get("step_id", start.get("step_index", step_index))
        invocation_id = (finish or {}).get("invocation_id")
        linked_invocation = by_id.get(invocation_id) if isinstance(invocation_id, str) else None
        result_status = (finish or {}).get("succeeded")
        status = (
            ("success" if result_status else "failed") if isinstance(result_status, bool)
            else ("running" if finish is None else _trace_status((finish or {}).get("status"), event=(finish or {}).get("event")))
        )
        execution_kind = start.get("step_kind") or start.get("kind")
        step_node_id = f"protocol_step_{step_index}_{_safe_str(step_id, 40)}"
        step_node = {
            "id": step_node_id, "type": "routing", "routing_kind": "protocol_step",
            "label": f"Protocol step: {agent_name}", "sublabel": f"Step {step_id}", "icon": "🧭",
            "status": status, "target_agent": _safe_str(agent_name, 100),
            "execution_kind": _safe_str(execution_kind, 40),
            "invocation_id": invocation_id if linked_invocation else None,
            "task": _safe_str(start.get("task_summary"), 120),
            "details": (
                f"Protocol step; logical target: {agent_name}. "
                + (f"Linked to an Agent invocation via invocation_id={invocation_id}." if linked_invocation else
                   "No matching invocation id in the Trace; the agent is not assumed to have run.")
            ),
            "started_at": start.get("timestamp"), "finished_at": (finish or {}).get("timestamp"),
        }
        nodes.append(step_node)
        edges.append({
            "id": f"route_step_{step_index}", "source": "orchestrator", "target": step_node_id,
            "type": "routing", "label": "Protocol step", "status": status,
        })
        if linked_invocation is not None:
            edges.append({
                "id": f"step_invocation_{step_index}_{linked_invocation['id']}",
                "source": step_node_id, "target": linked_invocation["id"],
                "type": "delegation", "label": "Invocation by exact id", "status": status,
            })

    for persistence_index, entry in enumerate(persistence_events):
        event = entry.get("event")
        explicit_status = entry.get("verification_status") or entry.get("status")
        is_verification_event = event in {"persistence_verified", "verification_succeeded", "verification_failed"}
        verification = (
            "verified" if event in {"persistence_verified", "verification_succeeded"} or explicit_status == "verified"
            else ("failed" if event == "verification_failed" or explicit_status == "verification_failed" else "unavailable")
        )
        node_id = f"persistence_{persistence_index}_{event}"
        details = (
            "Explicit persistence verification was observed in the Trace."
            if verification == "verified" else
            ("Persistence verification failed according to an explicit event." if verification == "failed" else
             "A save event was recorded, but the Trace has no independent verification evidence.")
        )
        nodes.append({
            "id": node_id, "type": "persistence", "label": "Save and verify",
            "sublabel": "Explicit verification" if is_verification_event else "Save event",
            "icon": "🗄️", "status": "success" if verification == "verified" else ("failed" if verification == "failed" else "unknown"),
            "verification": verification, "details": details, "timestamp": entry.get("timestamp"),
            "record_type": _safe_str(entry.get("record_type") or entry.get("table") or "", 60),
        })
        edges.append({
            "id": f"persistence_edge_{persistence_index}", "source": "orchestrator", "target": node_id,
            "type": "persistence", "label": "Save/verification event observed",
            "status": "completed" if verification == "verified" else ("failed" if verification == "failed" else "pending"),
        })

    for composition_index, entry in enumerate(composition_events):
        event = entry.get("event")
        node_id = f"composition_{composition_index}_{event}"
        composer = entry.get("agent_name") or entry.get("agent") or "composer unspecified"
        node_status = _trace_status(entry.get("status"), event=event)
        if node_status == "unknown":
            node_status = "success"
        nodes.append({
            "id": node_id, "type": "composition", "label": "Response composition",
            "sublabel": _safe_str(composer, 70), "icon": "📝", "status": node_status,
            "details": "A composition event was recorded; raw content is hidden.",
            "timestamp": entry.get("timestamp"),
            "invocation_id": entry.get("invocation_id"),
        })
        edges.append({
            "id": f"composition_edge_{composition_index}", "source": "orchestrator", "target": node_id,
            "type": "composition", "label": "Response composition observed", "status": "completed" if node_status == "success" else node_status,
        })

    if outcome:
        api_completion = next((
            entry for entry in reversed(entries)
            if entry.get("event") == "api_request_finished"
        ), None)
        delivery_confirmed = any(
            entry.get("event") in {"response_delivered", "telegram_message_sent", "simulator_response_received"}
            for entry in entries
        )
        outcome_details = "Outcome saved; that is not proof of browser delivery."
        if api_completion and api_completion.get("status_code") == 200 and not delivery_confirmed:
            outcome_details = "The API finished successfully; confirmation that the reply reached the browser/chat is not in the Trace."
        outcome_node_status = (
            "success" if outcome in {"succeeded", "closed_on_precedent", "completed"}
            else ("failed" if outcome in {"failed", "failure", "error"} else "unknown")
        )
        nodes.append({
            "id": "persisted_outcome", "type": "outcome", "label": f"Job outcome: {outcome}",
            "icon": "📌", "status": outcome_node_status,
            "details": outcome_details,
            "delivery_status": "confirmed" if delivery_confirmed else "unavailable",
        })
        edges.append({"id": "outcome_edge", "source": "orchestrator", "target": "persisted_outcome", "type": "outcome", "label": "Job outcome", "status": "completed" if outcome_node_status == "success" else ("failed" if outcome_node_status == "failed" else "pending")})

    specialist_nodes = [
        node for node in nodes
        if node.get("type") == "invocation" and node.get("invocation_id")
        and node.get("label") not in {"main_agent", "report_composer_agent", "insights_agent"}
    ]
    now = datetime.now(timezone.utc)
    intervals = [
        (node["id"], _parse_timestamp(node.get("started_at")), _parse_timestamp(node.get("finished_at")) or now)
        for node in specialist_nodes
    ]
    observed_parallel_sets: set[frozenset[str]] = set()
    for _, anchor_start, _ in intervals:
        if anchor_start is None:
            continue
        concurrent = frozenset(
            node_id for node_id, started, finished in intervals
            if started is not None and started <= anchor_start and finished > anchor_start
        )
        if len(concurrent) > 1:
            observed_parallel_sets.add(concurrent)
    maximal_parallel_sets = {
        group for group in observed_parallel_sets
        if not any(group < other for other in observed_parallel_sets)
    }
    parallel_invocation_ids = set().union(*maximal_parallel_sets) if maximal_parallel_sets else set()
    for specialist in specialist_nodes:
        specialist["is_parallel"] = specialist["id"] in parallel_invocation_ids
    explanation = ""
    if not specialist_nodes:
        explanation = (
            "Closed on precedent; this Trace observed no specialists or tools."
            if outcome == "closed_on_precedent" and not tool_events else
            "No specialist invocation was recorded in this Trace; do not infer that one occurred."
        )
    graph_nodes_by_id = {node["id"]: node for node in nodes}
    actual_messages = [
        {"id": edge["id"], "kind": edge["type"], "from_id": edge["source"], "to_id": edge["target"],
         "from_label": graph_nodes_by_id[edge["source"]]["label"],
         "to_label": graph_nodes_by_id[edge["target"]]["label"],
         "time": graph_nodes_by_id[edge["target"]].get("started_at") or graph_nodes_by_id[edge["source"]].get("finished_at"),
         "title": edge.get("label", ""), "summary": edge.get("label", ""), "status": edge.get("status", "")}
        for edge in edges if edge["type"] in {"invocation", "result", "tool_call", "llm", "tool_result", "routing", "delegation", "agent_result"}
    ]
    return {
        "nodes": nodes, "edges": edges, "specialist_count": len(specialist_nodes),
        "tool_count": len(tool_events), "explanation": explanation,
        "has_parallel": bool(maximal_parallel_sets), "parallel_batches_count": len(maximal_parallel_sets),
        "parallel_invocation_count": len(parallel_invocation_ids),
    }, actual_messages


def aggregate_trace_data(
    raw_entries: list[dict[str, Any]],
    *,
    profile_name: str | None = None,
    trace_id: str = "",
) -> dict[str, Any]:
    """Aggregate raw log entries into a structured behind-the-scenes view.

    Returns:
        metrics: Performance counters (LLM calls, model time, tools time, tokens, etc.)
        timeline: The 8 execution stages with real timestamps and status
        agents: Collaboration hierarchy (parent -> specialists, parallel flags)
        tools: Tool calls with read vs write (side_effecting) and verification status
        terminal: Whether the request reached a terminal state
    """
    entries = []
    for raw in raw_entries:
        rec_profile = raw.get("profile_name")
        if profile_name and rec_profile and not _profiles_match(profile_name, rec_profile):
            continue
        clean = sanitize_trace_details(raw)
        entries.append(clean)

    # Performance metric accumulators
    llm_call_count = 0
    total_model_latency_ms = 0.0
    total_tools_duration_sec = 0.0
    queue_wait_seconds: float | None = None
    input_tokens = 0
    output_tokens = 0
    cache_tokens = 0
    total_tokens = 0
    has_token_data = False
    retries_count = 0

    first_ts: datetime | None = None
    last_ts: datetime | None = None

    # Collaboration tracking
    agent_invocations: dict[str, list[dict[str, Any]]] = {}
    active_specialists: set[str] = set()
    parallel_batches: list[list[str]] = []
    current_parallel_batch: list[str] = []
    protocol_steps: list[dict[str, Any]] = []

    # Stages tracking (the 8 mandatory phases)
    # 1. Ingestion, 2. Routing, 3. Intent & Extraction, 4. Agent Selection,
    # 5. Protocol Selection, 6. Tool Execution, 7. Persistence & Verification, 8. Synthesis & Outcome
    stages: dict[str, dict[str, Any]] = {
        "ingestion": {
            "id": "ingestion",
            "name": "Message intake",
            "status": "pending",
            "agent": "API / Ingestion",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "routing": {
            "id": "routing",
            "name": "Area and group routing",
            "status": "pending",
            "agent": "Router",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "intent_extraction": {
            "id": "intent_extraction",
            "name": "Intent understanding and entity extraction",
            "status": "pending",
            "agent": "Main Agent",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "agent_selection": {
            "id": "agent_selection",
            "name": "Specialist agent selection",
            "status": "pending",
            "agent": "Main Agent",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "protocol_selection": {
            "id": "protocol_selection",
            "name": "Operational protocol selection",
            "status": "pending",
            "agent": "Protocol Engine",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "tool_execution": {
            "id": "tool_execution",
            "name": "Tool execution",
            "status": "pending",
            "agent": "Specialists",
            "details": "",
            "items": [],
            "started_at": None,
            "duration_ms": None,
        },
        "persistence_verification": {
            "id": "persistence_verification",
            "name": "Operational save and verify",
            "status": "pending",
            "agent": "Persistence Store",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "synthesis": {
            "id": "synthesis",
            "name": "Collect results and compose a reply",
            "status": "pending",
            "agent": "Main Agent",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
    }

    tool_items: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    is_terminal = False
    terminal_outcome: str | None = None
    terminal_reason: str | None = None

    for entry in entries:
        event = entry.get("event")
        ts_raw = entry.get("timestamp")
        ts = _parse_timestamp(ts_raw)
        if ts:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

        # 1. Ingestion
        if event in {"api_request_started", "report_received", "request_received"}:
            stages["ingestion"]["status"] = "success"
            if not stages["ingestion"]["started_at"]:
                stages["ingestion"]["started_at"] = ts_raw
            src = entry.get("source") or entry.get("route") or "api"
            sender = entry.get("sender_identity") or ""
            txt = _safe_str(entry.get("raw_text") or entry.get("message") or "")
            stages["ingestion"]["details"] = f"Source: {src} | Sender: {sender} | {txt}".strip(" | ")
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "user_client",
                "from_label": f"User ({sender})" if sender else "User",
                "from_icon": "👤",
                "to_id": "main_agent",
                "to_label": "Main Agent",
                "to_icon": "🤖",
                "kind": "input",
                "badge": "Report intake",
                "title": "User message received",
                "summary": txt[:110] if txt else "Simulation opening message",
                "body": txt,
                "status": "success",
            })

        # 2. Routing
        if event == "group_scope_applied":
            stages["routing"]["status"] = "success"
            stages["routing"]["started_at"] = stages["routing"]["started_at"] or ts_raw
            agent = entry.get("agent_name") or entry.get("agent") or entry.get("preferred_agent_hint") or "unspecified"
            stages["routing"]["details"] = f"A group routing hint was recorded: {agent}; that is not proof an Agent ran."
        elif event == "group_binding_written":
            stages["routing"]["status"] = "success"

        # 3. Intent & Extraction
        if event == "intent_classified":
            stages["intent_extraction"]["status"] = "success"
            stages["intent_extraction"]["started_at"] = stages["intent_extraction"]["started_at"] or ts_raw
            intent = entry.get("intent", "?")
            stages["intent_extraction"]["details"] = f"Classified intent: {intent}"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "main_agent",
                "from_label": "Main Agent",
                "from_icon": "🤖",
                "to_id": "main_agent",
                "to_label": "Main Agent (intent)",
                "to_icon": "🧠",
                "kind": "intent",
                "badge": "Intent understanding",
                "title": f"Intent classification: {intent}",
                "summary": f"Classified intent: {intent}",
                "body": f"Classified intent: {intent}; reasoning content is not shown.",
                "status": "success",
            })
        elif event == "extraction_result":
            stages["intent_extraction"]["status"] = "success"
            cls_name = entry.get("classification") or "unclassified"
            area = entry.get("area") or "no area"
            missing = entry.get("missing_fields") or []
            suffix = f" | missing: {', '.join(missing)}" if missing else ""
            stages["intent_extraction"]["details"] += f" | area: {area}, classification: {cls_name}{suffix}"
        elif event == "risk_assessed":
            risk = entry.get("risk_level", "?")
            score = entry.get("risk_score", "")
            stages["intent_extraction"]["details"] += f" | risk: {risk} (score: {score})"

        # 4. Agent Selection & Specialist Execution
        if event == "agent_selection":
            selected = entry.get("selected_agents") or entry.get("chosen_agents") or []
            if isinstance(selected, str):
                selected = [selected]
            if not isinstance(selected, (list, tuple)):
                selected = []
            routing_status = entry.get("routing_decision_status") or entry.get("decision_status") or entry.get("status")
            stages["agent_selection"]["status"] = _trace_status(routing_status, event=event)
            if stages["agent_selection"]["status"] == "unknown" and selected:
                stages["agent_selection"]["status"] = "success"
            stages["agent_selection"]["started_at"] = stages["agent_selection"]["started_at"] or ts_raw
            stages["agent_selection"]["details"] = (
                f"Recorded routing targets: {', '.join(_safe_str(a, 80) for a in selected) if selected else 'none'}; "
                "Selection is not proof of invocation."
            )
        elif event == "picture_planned":
            stages["agent_selection"]["status"] = "success"
            domains = entry.get("domains") or {}
            stages["agent_selection"]["details"] = f"Situational picture planned against: {', '.join(domains.keys())}"

        if event == "specialist_started":
            ag = entry.get("agent", "")
            if ag and ag != "main_agent":
                active_specialists.add(ag)
                task_text = _safe_str(entry.get("task_summary") or "Safe task summary was not stored")
                step_idx = entry.get("step_index", len(agent_invocations.get(ag, [])) + 1)
                agent_invocations.setdefault(ag, []).append({
                    "run_index": len(agent_invocations[ag]) + 1,
                    "parent": entry.get("parent_agent", "main_agent"),
                    "status": "running",
                    "started_at": ts_raw,
                    "task": task_text,
                    "step_index": step_idx,
                    "step_id": entry.get("step_id", str(step_idx)),
                })
                current_parallel_batch.append(ag)
                messages.append({
                    "id": f"msg_{len(messages) + 1}",
                    "time": ts_raw,
                    "from_id": "main_agent",
                    "from_label": "Main Agent",
                    "from_icon": "🤖",
                    "to_id": f"specialist_{ag}",
                    "to_label": _agent_display_name(ag),
                    "to_icon": _agent_icon(ag),
                    "kind": "delegation",
                    "badge": "Task order",
                    "title": f"Task order (step {step_idx}) to {_agent_display_name(ag)}",
                    "summary": f"Task for specialist: {task_text[:110]}",
                    "body": task_text,
                    "status": "running",
                })
        elif event == "step_start":
            protocol_steps.append({
                "agent": entry.get("agent"), "step_index": entry.get("step_index"),
                "step_id": entry.get("step_id"), "started_at": ts_raw,
                "status": "running", "invocation_id": None,
            })
            ag = entry.get("agent") or "unknown agent"
            messages.append({
                "id": f"msg_{len(messages) + 1}", "time": ts_raw,
                "from_id": "main_agent", "from_label": "Protocol engine", "from_icon": "🧭",
                "to_id": "protocol_flow", "to_label": f"Protocol step for {ag}", "to_icon": _agent_icon(ag),
                "kind": "protocol_step", "badge": "Step selection",
                "title": f"The protocol selected a step for {ag}",
                "summary": "Step selection is not proof an Agent ran.",
                "body": "An Agent node appears only if the Trace contains an identified invocation.", "status": "running",
            })
        elif event in {"specialist_finished", "specialist_failed", "specialist_timeout"}:
            ag = entry.get("agent", "")
            if ag and ag != "main_agent":
                active_specialists.discard(ag)
                runs = agent_invocations.get(ag, [])
                is_err = "failed" in event or "timeout" in event or not entry.get("succeeded", True)
                st = "failed" if is_err else "success"
                res_text = _safe_str(entry.get("result_summary") or "The specialist result was received; raw output is not shown")
                dur_ms = entry.get("duration_ms")
                if runs:
                    runs[-1]["status"] = st
                    runs[-1]["result"] = res_text
                    if dur_ms is not None:
                        runs[-1]["duration_ms"] = dur_ms
                else:
                    agent_invocations[ag] = [{
                        "run_index": 1,
                        "parent": entry.get("parent_agent", "main_agent"),
                        "status": st,
                        "started_at": ts_raw,
                        "result": res_text,
                    }]
                messages.append({
                    "id": f"msg_{len(messages) + 1}",
                    "time": ts_raw,
                    "from_id": f"specialist_{ag}",
                    "from_label": _agent_display_name(ag),
                    "from_icon": _agent_icon(ag),
                    "to_id": "main_agent",
                    "to_label": "Main Agent",
                    "to_icon": "🤖",
                    "kind": "result",
                    "badge": "Specialist result" if st == "success" else "Specialist error",
                    "title": f"Result returned from {_agent_display_name(ag)}",
                    "summary": f"Result: {res_text[:110]}" if res_text else ("The action failed" if st == "failed" else "The action completed"),
                    "body": res_text or ("Error while executing the specialist step" if st == "failed" else "Completed with no content"),
                    "status": st,
                })
        elif event in {"step_result", "step_failed"}:
            ag = entry.get("agent") or "unknown agent"
            step_idx = entry.get("step_index")
            for step in reversed(protocol_steps):
                if step.get("agent") == entry.get("agent") and step.get("step_index") == step_idx and step.get("status") == "running":
                    step["status"] = "success" if entry.get("succeeded", event == "step_result") else "failed"
                    step["finished_at"] = ts_raw
                    step["invocation_id"] = entry.get("invocation_id")
                    break
            messages.append({
                "id": f"msg_{len(messages) + 1}", "time": ts_raw,
                "from_id": "protocol_flow", "from_label": "Protocol engine", "from_icon": "🧭",
                "to_id": "main_agent", "to_label": "Main Agent", "to_icon": "🤖",
                "kind": "protocol_step_result", "badge": "Protocol step result",
                "title": f"The protocol step for {ag} finished",
                "summary": ("invocation id present" if entry.get("invocation_id") else "No identified Agent invocation was observed"),
                "body": "The step result was stored at protocol level; do not infer that the agent itself ran.",
                "status": "success" if entry.get("succeeded", event == "step_result") else "failed",
            })

        # 5. Protocol Selection
        if event == "protocol_selection":
            status = entry.get("status", "")
            proto = entry.get("protocol_name") or "-"
            stages["protocol_selection"]["started_at"] = stages["protocol_selection"]["started_at"] or ts_raw
            if status == "selected":
                stages["protocol_selection"]["status"] = "success"
                stages["protocol_selection"]["details"] = f"Selected protocol: {proto}"
            elif status == "ambiguous":
                stages["protocol_selection"]["status"] = "running"
                cand = entry.get("candidate_names") or []
                stages["protocol_selection"]["details"] = f"Deciding between: {', '.join(cand)}"
            else:
                stages["protocol_selection"]["status"] = "failed"
                stages["protocol_selection"]["details"] = f"No protocol was selected ({status or 'unknown status'})."

        # 6. Tool Execution
        if event == "tool_call":
            stages["tool_execution"]["status"] = "running"
            tool_name = entry.get("tool", "unknown")
            ag = entry.get("agent_name") or entry.get("agent") or "unknown"
            side_effecting = bool(entry.get("side_effecting", False))
            status = _trace_status(entry.get("status"), event=event)
            dur = (
                _number(entry.get("duration_ms")) / 1000
                if entry.get("duration_ms") is not None
                else _number(entry.get("duration_seconds"))
            )
            total_tools_duration_sec += dur

            # Verification determination
            # Read-only tools cannot mutate state -> "read_only"
            # Write tools must show verified ONLY if authoritative verification exists
            res_summary = _safe_str(entry.get("result_summary", ""))
            verified = entry.get("verification_status") or entry.get("verification")
            allowed_verification = {"verified", "unverified", "failed", "verification_unavailable", "read_only", "blocked"}
            if verified not in allowed_verification:
                verified = "unverified" if side_effecting else "read_only"
            verification_note = _safe_str(entry.get("verification_note"), 120) or (
                "Explicit verification was recorded on the tool event" if verified == "verified" else
                ("Verification unavailable; tool success alone is not persistence verification" if side_effecting else "Read only (no state change)")
            )

            item = {
                "tool": tool_name,
                "agent": ag,
                "side_effecting": side_effecting,
                "status": status,
                "duration_ms": round(dur * 1000, 1),
                "timestamp": ts_raw,
                "summary": res_summary,
                "verification": verified,
                "verification_note": verification_note,
            }
            tool_items.append(item)
            stages["tool_execution"]["details"] = f"Invoked {len(tool_items)} tools"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": f"specialist_{ag}" if ag and ag != "main_agent" else "main_agent",
                "from_label": _agent_display_name(ag) if ag else "Main Agent",
                "from_icon": _agent_icon(ag) if ag else "🤖",
                "to_id": f"tool_{tool_name}",
                "to_label": f"Tool: {tool_name}",
                "to_icon": "🔧",
                "kind": "tool",
                "badge": "Tool (write)" if side_effecting else "Tool (read)",
                "title": f"Invoking tool {tool_name}",
                "summary": f"Call to tool {tool_name}: {res_summary[:90]}",
                "body": res_summary,
                "status": status,
                "duration_ms": round(dur * 1000, 1),
            })
        elif event == "tool_blocked":
            tool_name = entry.get("tool", "unknown")
            ag = entry.get("agent", "unknown")
            tool_items.append({
                "tool": tool_name,
                "agent": ag,
                "side_effecting": False,
                "status": "blocked",
                "duration_ms": 0,
                "timestamp": ts_raw,
                "summary": "The action was blocked due to permissions",
                "verification": "blocked",
                "verification_note": "Action blocked",
            })
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": f"specialist_{ag}" if ag and ag != "main_agent" else "main_agent",
                "from_label": _agent_display_name(ag) if ag else "Main Agent",
                "from_icon": _agent_icon(ag) if ag else "🤖",
                "to_id": f"tool_{tool_name}",
                "to_label": f"Tool: {tool_name}",
                "to_icon": "🔧",
                "kind": "tool",
                "badge": "Tool blocked",
                "title": f"Blocked tool {tool_name}",
                "summary": f"Call to tool {tool_name} was blocked due to permissions",
                "body": "The action was blocked",
                "status": "failed",
            })

        # 7. Persistence & Holds
        if event in {"hold_created", "hold_resolved"}:
            stages["persistence_verification"]["started_at"] = stages["persistence_verification"]["started_at"] or ts_raw
            kind = entry.get("hold_kind", "approval")
            if event == "hold_created":
                stages["persistence_verification"]["status"] = "waiting"
                stages["persistence_verification"]["details"] = f"Hold is active: {kind}; the rationale is not shown in BTS."
                messages.append({
                    "id": f"msg_{len(messages) + 1}",
                    "time": ts_raw,
                    "from_id": "main_agent",
                    "from_label": "Main Agent",
                    "from_icon": "🤖",
                    "to_id": "commander_hold",
                    "to_label": "Commander approval",
                    "to_icon": "🛡️",
                    "kind": "hold",
                    "badge": "Hold for approval",
                    "title": f"Approval request ({kind})",
                    "summary": f"Hold is active: {kind}",
                    "body": "The request is waiting for approval; the rationale is not shown.",
                    "status": "waiting",
                })
            else:
                stages["persistence_verification"]["status"] = "success"
                stages["persistence_verification"]["details"] = f"Hold approved by {entry.get('resolved_by', 'commander')}"
        elif event == "event_data_saved" or event == "attendance_cycle_opened":
            stages["persistence_verification"]["status"] = "success"
            stages["persistence_verification"]["details"] = "A save event was recorded; this Trace has no separate independent verification."
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "main_agent",
                "from_label": "Main Agent",
                "from_icon": "🤖",
                "to_id": "persistence_store",
                "to_label": "Database (SQLite)",
                "to_icon": "🗄️",
                "kind": "persistence",
                "badge": "Saved to store",
                "title": "Operational state updated in the store",
                "summary": "A store save event was recorded; independent verification is unavailable",
                "body": "The Trace includes a save event but no independent verification evidence.",
                "status": "success",
            })

        # 8. Synthesis & Outcomes
        if event in {"step_start", "step_result", "step_retry", "step_failed"}:
            if event == "step_retry":
                retries_count += 1
            idx = entry.get("step_index", 0)
            ag = entry.get("agent", "")
            if event == "step_result":
                succeeded = entry.get("succeeded", False)
                stages["synthesis"]["status"] = "running"
                stages["synthesis"]["details"] = f"Stage {idx} ({ag}) completed successfully" if succeeded else f"Stage {idx} ({ag}) failed"

        if event in {"picture_composed", "report_composed"}:
            stages["synthesis"]["status"] = "running"
            stages["synthesis"]["details"] = "Summary report composed successfully"

        if event == "event_outcome":
            is_terminal = True
            outcome = entry.get("outcome", "unknown")
            terminal_outcome = outcome
            terminal_reason = _safe_str(entry.get("failure_reason") or entry.get("reasoning") or "")
            stages["synthesis"]["status"] = "success" if outcome in {"succeeded", "closed_on_precedent"} else "failed"
            stages["synthesis"]["details"] = f"Final outcome: {outcome}"
            if terminal_reason:
                stages["synthesis"]["details"] += f" ({terminal_reason})"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "main_agent",
                "from_label": "Main Agent",
                "from_icon": "🤖",
                "to_id": "user_client",
                "to_label": "User / reporting channel",
                "to_icon": "👤",
                "kind": "outcome",
                "badge": "Final reply",
                "title": f"Summary reply to the user ({outcome})",
                "summary": f"Result: {outcome} — {terminal_reason[:90] if terminal_reason else 'The request was fully handled'}",
                "body": terminal_reason or f"Status: {outcome}",
                "status": "success" if outcome in {"succeeded", "closed_on_precedent"} else "failed",
            })

        if event == "api_request_finished":
            status_code = entry.get("status_code")
            if status_code == 200 and not is_terminal and not any(e.get("event") == "queue_started" for e in entries):
                is_terminal = True
                terminal_outcome = "succeeded"
                if stages["synthesis"]["status"] == "pending":
                    stages["synthesis"]["status"] = "success"
                    stages["synthesis"]["details"] = "The API finished with HTTP 200; confirmation of chat/browser delivery is not in the Trace."
            elif status_code and status_code >= 400 and not is_terminal:
                is_terminal = True
                terminal_outcome = "failed"
                stages["synthesis"]["status"] = "failed"
                stages["synthesis"]["details"] = f"The request ended with an error ({status_code})"

        # Telemetry / Provider metrics
        if event in {"provider_request_finished", "provider_request_failed"}:
            llm_call_count += 1
            lat = _number(entry.get("latency_ms"))
            total_model_latency_ms += lat

            inp = entry.get("input_tokens")
            outp = entry.get("output_tokens")
            cch = entry.get("cache_tokens")
            tot = entry.get("total_tokens")

            if any(v is not None for v in (inp, outp, cch, tot)):
                has_token_data = True
                input_tokens += int(_number(inp))
                output_tokens += int(_number(outp))
                cache_tokens += int(_number(cch))
                total_tokens += int(_number(tot, _number(inp) + _number(outp)))

        if event == "queue_started":
            queue_wait_seconds = _number(entry.get("queue_wait_seconds"))

    # Compute wall-clock duration
    total_wall_clock_ms = 0.0
    if first_ts and last_ts:
        diff = (last_ts - first_ts).total_seconds()
        total_wall_clock_ms = max(0.0, round(diff * 1000, 1))

    # Detect parallel executions (specialists running concurrently)
    if len(current_parallel_batch) > 1:
        parallel_batches.append(list(set(current_parallel_batch)))

    # Mark tool stage completed if tools ran
    if tool_items:
        statuses = {item["status"] for item in tool_items}
        if statuses <= {"success"}:
            stages["tool_execution"]["status"] = "success"
        elif statuses & {"failed", "error", "blocked"}:
            stages["tool_execution"]["status"] = "failed"
        elif "running" in statuses:
            stages["tool_execution"]["status"] = "running"
        else:
            stages["tool_execution"]["status"] = "unknown"
        stages["tool_execution"]["items"] = tool_items

    # Format tokens display
    tokens_payload: dict[str, Any] | None = None
    if has_token_data:
        tokens_payload = {
            "total": total_tokens,
            "input": input_tokens,
            "output": output_tokens,
            "cache": cache_tokens,
            "display": f"{total_tokens:,} (input: {input_tokens:,}, output: {output_tokens:,}, cache: {cache_tokens:,})",
        }

    # Format metrics
    metrics = {
        "llm_call_count": llm_call_count,
        "total_wall_clock_ms": total_wall_clock_ms,
        "model_latency_ms": round(total_model_latency_ms, 1),
        "tools_duration_ms": round(total_tools_duration_sec * 1000, 1),
        "queue_wait_ms": round(queue_wait_seconds * 1000, 1) if queue_wait_seconds is not None else None,
        "retries_count": retries_count,
        "tokens": tokens_payload,
        "agent_invocations_count": 0,
    }

    # Format collaboration tree
    collaboration = []
    for ag, runs in agent_invocations.items():
        is_parallel = any(ag in batch for batch in parallel_batches)
        collaboration.append({
            "agent": ag,
            "parent": runs[0].get("parent", "main_agent") if runs else "main_agent",
            "call_count": len(runs),
            "is_parallel": is_parallel,
            "runs": runs,
            "final_status": runs[-1]["status"] if runs else "unknown",
        })

    # -------------------------------------------------------------
    # Live Agent Execution Graph Construction
    # -------------------------------------------------------------
    # Main Agent status
    main_status = "pending"
    if stages["ingestion"]["status"] == "success":
        main_status = "running"
    if is_terminal:
        main_status = "success" if terminal_outcome in {"succeeded", "completed", "closed_on_precedent"} else "failed"
    elif stages["persistence_verification"]["status"] == "waiting":
        main_status = "waiting"

    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []

    # 0. User / Ingestion Node
    has_user_input = bool(stages["ingestion"]["details"]) or any(m.get("from_id") == "user_client" for m in messages)
    if has_user_input:
        graph_nodes.append({
            "id": "user_client",
            "label": "User / report",
            "sublabel": "Reporter / Telegram",
            "icon": "👤",
            "type": "user",
            "status": "success",
            "details": stages["ingestion"].get("details", ""),
        })
        graph_edges.append({
            "id": "edge_user_main",
            "source": "user_client",
            "target": "main_agent",
            "type": "input",
            "status": "completed",
            "label": "Report intake",
            "message_count": 1,
            "last_message": stages["ingestion"].get("details", "")[:80],
        })

    # 1. Center: Main Agent
    graph_nodes.append({
        "id": "main_agent",
        "label": "Main Agent (orchestrator)",
        "sublabel": "Main Agent Orchestrator",
        "icon": "🤖",
        "type": "main",
        "status": main_status,
        "intent": stages["intent_extraction"].get("details", ""),
        "protocol": stages["protocol_selection"].get("details", ""),
        "llm_calls": llm_call_count,
        "latency_ms": round(total_model_latency_ms, 1),
        "tokens": total_tokens,
        "tools_count": len(tool_items),
        "is_terminal": is_terminal,
        "outcome": terminal_outcome,
        "outcome_reason": terminal_reason,
    })

    # 2. Specialists (Sub-agents)
    for ag_name, runs in agent_invocations.items():
        is_parallel = any(ag_name in batch for batch in parallel_batches)
        ag_status = runs[-1]["status"] if runs else "unknown"
        ag_dur = sum(float(r.get("duration_ms") or 0.0) for r in runs)
        ag_tasks = [r.get("task") for r in runs if r.get("task")]
        ag_results = [r.get("result") for r in runs if r.get("result")]
        ag_tools = [t for t in tool_items if t["agent"] == ag_name]
        retries = sum(1 for r in runs if r.get("status") == "retry")

        node_id = f"specialist_{ag_name}"
        graph_nodes.append({
            "id": node_id,
            "label": _agent_display_name(ag_name),
            "sublabel": ag_name,
            "icon": _agent_icon(ag_name),
            "type": "specialist",
            "status": ag_status,
            "is_parallel": is_parallel,
            "duration_ms": round(ag_dur, 1) if ag_dur > 0 else None,
            "call_count": len(runs),
            "tasks": ag_tasks,
            "results": ag_results,
            "tools": ag_tools,
            "retries": retries,
        })

        # Count messages between main_agent and this specialist
        spec_msgs = [m for m in messages if m.get("to_id") == node_id or m.get("from_id") == node_id]
        last_spec_msg = spec_msgs[-1]["summary"] if spec_msgs else ""

        # Connecting edge from Main Agent to Specialist
        edge_status = "active" if ag_status == "running" else ("completed" if ag_status == "success" else ("failed" if ag_status == "failed" else "pending"))
        graph_edges.append({
            "id": f"edge_main_{ag_name}",
            "source": "main_agent",
            "target": node_id,
            "type": "delegation",
            "status": edge_status,
            "is_parallel": is_parallel,
            "label": "Parallel ⚡" if is_parallel else "Task order",
            "message_count": len(spec_msgs),
            "last_message": last_spec_msg,
        })

        # 3. Sub-nodes for Tools executed by this specialist
        for idx, t_info in enumerate(ag_tools):
            tool_node_id = f"tool_{ag_name}_{idx}"
            graph_nodes.append({
                "id": tool_node_id,
                "label": t_info["tool"],
                "sublabel": "Write action" if t_info["side_effecting"] else "Read action",
                "icon": "🔧",
                "type": "tool",
                "parent": node_id,
                "status": t_info["status"],
                "side_effecting": t_info["side_effecting"],
                "verification": t_info["verification"],
                "verification_note": t_info["verification_note"],
                "duration_ms": t_info["duration_ms"],
                "summary": t_info["summary"],
            })
            graph_edges.append({
                "id": f"edge_{node_id}_{tool_node_id}",
                "source": node_id,
                "target": tool_node_id,
                "type": "tool_call",
                "status": "completed" if t_info["status"] == "success" else ("failed" if t_info["status"] == "failed" else "active"),
                "label": "Tool (write)" if t_info["side_effecting"] else "Tool (read)",
                "message_count": 1,
                "last_message": t_info["summary"][:80],
            })

    # Tools called directly by Main Agent or unknown
    direct_tools = [t for t in tool_items if t["agent"] in {"main_agent", "unknown", ""}]
    for idx, t_info in enumerate(direct_tools):
        tool_node_id = f"tool_direct_{idx}"
        graph_nodes.append({
            "id": tool_node_id,
            "label": t_info["tool"],
            "sublabel": "Direct tool",
            "icon": "🔧",
            "type": "tool",
            "parent": "main_agent",
            "status": t_info["status"],
            "side_effecting": t_info["side_effecting"],
            "verification": t_info["verification"],
            "verification_note": t_info["verification_note"],
            "duration_ms": t_info["duration_ms"],
            "summary": t_info["summary"],
        })
        graph_edges.append({
            "id": f"edge_main_{tool_node_id}",
            "source": "main_agent",
            "target": tool_node_id,
            "type": "tool_call",
            "status": "completed" if t_info["status"] == "success" else "failed",
            "label": "Direct tool",
            "message_count": 1,
            "last_message": t_info["summary"][:80],
        })

    # Persistence / Database Node (if write tools or persistence verification ran)
    persist_status = stages["persistence_verification"]["status"]
    has_write_tools = any(t["side_effecting"] for t in tool_items)
    if persist_status != "pending" or has_write_tools:
        store_node_id = "persistence_store"
        graph_nodes.append({
            "id": store_node_id,
            "label": "Database and verification",
            "sublabel": "SQLite Store & Verification",
            "icon": "🗄️",
            "type": "persistence",
            "status": persist_status if persist_status != "pending" else "running",
            "details": stages["persistence_verification"].get("details", ""),
            "has_write_tools": has_write_tools,
        })
        graph_edges.append({
            "id": "edge_main_persistence",
            "source": "main_agent",
            "target": store_node_id,
            "type": "persistence",
            "status": "completed" if persist_status == "success" else ("active" if persist_status in {"running", "waiting"} else "pending"),
            "label": "Save / verify",
            "message_count": 1,
            "last_message": stages["persistence_verification"].get("details", "")[:80],
        })

    # Final Outcome Delivery Node (if completed)
    if is_terminal and terminal_outcome:
        outcome_node_id = "final_outcome"
        is_succ = terminal_outcome in {"succeeded", "completed", "closed_on_precedent"}
        graph_nodes.append({
            "id": outcome_node_id,
            "label": f"Final reply: {terminal_outcome}",
            "sublabel": "Final Outcome Delivery",
            "icon": "✅" if is_succ else "❌",
            "type": "outcome",
            "status": "success" if is_succ else "failed",
            "details": terminal_reason or f"Final status: {terminal_outcome}",
        })
        graph_edges.append({
            "id": "edge_main_outcome",
            "source": "main_agent",
            "target": outcome_node_id,
            "type": "outcome",
            "status": "completed" if is_succ else "failed",
            "label": "Reply delivery",
            "message_count": 1,
            "last_message": (terminal_reason or terminal_outcome)[:80],
        })

    graph_payload = {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "has_parallel": any(e.get("is_parallel") for e in graph_edges),
        "parallel_batches_count": len(parallel_batches),
    }

    # The legacy stage summary above remains for timeline metrics, but its
    # inferred graph/messages must not be presented as observed hand-offs.
    graph_payload, causal_messages = _execution_graph(entries, terminal_outcome)
    observed_invocations = [
        node for node in graph_payload["nodes"]
        if node.get("type") == "invocation" and node.get("invocation_id")
    ]
    metrics["agent_invocations_count"] = len(observed_invocations)
    collaboration_by_agent: dict[str, list[dict[str, Any]]] = {}
    for node in observed_invocations:
        collaboration_by_agent.setdefault(node["label"], []).append({
            "invocation_id": node["invocation_id"],
            "parent": node.get("parent_agent") or "Orchestrator",
            "status": node.get("status", "unknown"),
            "started_at": node.get("started_at"),
            "finished_at": node.get("finished_at"),
            "duration_ms": node.get("duration_ms"),
            "task": node.get("task", ""),
            "tools": node.get("tools", []),
        })
    collaboration = [
        {
            "agent": agent_name,
            "parent": runs[0].get("parent", "Orchestrator"),
            "call_count": len(runs),
            "is_parallel": False,
            "runs": runs,
            "final_status": runs[-1].get("status", "unknown"),
        }
        for agent_name, runs in collaboration_by_agent.items()
    ]
    queue_stopped_on_error = not is_terminal and any(
        entry.get("event") == "stage_finished" and entry.get("stage") == "queue_execution"
        and entry.get("status") == "error" for entry in entries
    )
    if queue_stopped_on_error:
        next(node for node in graph_payload["nodes"] if node["id"] == "orchestrator")["status"] = "unknown"

    delivery_confirmed = any(
        entry.get("event") in {"response_delivered", "telegram_message_sent", "simulator_response_received"}
        for entry in entries
    )
    approval_waiting = stages["persistence_verification"]["status"] == "waiting"
    if approval_waiting:
        execution_status = "awaiting_approval"
    elif not is_terminal:
        execution_status = "unknown" if queue_stopped_on_error else "running"
    elif terminal_outcome == "partial":
        execution_status = "partial"
    elif terminal_outcome in {"succeeded", "completed", "closed_on_precedent"}:
        execution_status = "succeeded"
    elif terminal_outcome in {"failed", "failure", "error"}:
        execution_status = "failed"
    else:
        execution_status = "unknown"
    api_http_status = next((
        entry.get("status_code") for entry in reversed(entries)
        if entry.get("event") == "api_request_finished"
    ), None)
    delivery_status = "confirmed" if delivery_confirmed else "unavailable"

    return {
        "trace_id": trace_id,
        "terminal": is_terminal,
        "execution_status": execution_status,
        "outcome": terminal_outcome,
        "outcome_reason": terminal_reason,
        "delivery_status": delivery_status,
        "api_http_status": api_http_status,
        "diagnostic_state": "job_stopped_without_outcome" if queue_stopped_on_error else None,
        "metrics": metrics,
        "stages": list(stages.values()),
        "collaboration": collaboration,
        "tool_items": tool_items,
        "messages": causal_messages,
        "event_count": len(entries),
        "graph": graph_payload,
    }
