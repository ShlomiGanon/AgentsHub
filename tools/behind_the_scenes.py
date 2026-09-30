"""Behind-The-Scenes (מאחורי הקלעים) trace aggregation and diagnostics engine.

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
    "response",
    "system_prompt",
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
    "private_key",
    "messages",
})


def _safe_str(value: Any, limit: int = 140) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _parse_timestamp(val: Any) -> datetime | None:
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
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if key in SENSITIVE_FIELD_NAMES:
            continue
        if isinstance(value, str) and len(value) > 300:
            clean[key] = _safe_str(value, 300)
        else:
            clean[key] = value
    return clean


def _profiles_match(expected: str | None, actual: str | None) -> bool:
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
    return {
        "main_agent": "🤖", "roster_agent": "📋", "team_status_agent": "📋",
        "surveillance_agent": "👁️", "neighboring_forces_agent": "🤝",
        "security_agent": "🛡️", "fire_agent": "🚒", "firefighting_agent": "🚒",
        "fire_station": "🚒", "medical_agent": "🚑", "engineering_agent": "⚙️",
        "insights_agent": "💡", "report_composer": "📊", "persistence_store": "🗄️",
        "user_client": "👤", "final_outcome": "🎯",
    }.get(name, "🤖")


def _agent_display_name(name: str) -> str:
    return {
        "main_agent": "סוכן ראשי (מתכלל)", "team_status_agent": "מומחה נוכחות וכוח אדם",
        "roster_agent": "מומחה נוכחות וכוח אדם", "surveillance_agent": "מומחה תצפית ורחפנים",
        "neighboring_forces_agent": "מומחה כוחות שכנים וסיוע", "security_agent": "מומחה אבטחה וכוננות",
        "fire_agent": "מומחה כיבוי והצלה", "firefighting_agent": "מומחה כיבוי והצלה",
        "fire_station": "מומחה כיבוי והצלה", "medical_agent": "מומחה רפואה ופינוי",
        "engineering_agent": "מומחה הנדסה ותשתיות", "insights_agent": "סוכן תובנות ומגמות",
        "report_composer": "מרכיב דוחות תפעוליים", "persistence_store": "מסד נתונים ואימות",
        "user_client": "משתמש / ערוץ דיווח", "final_outcome": "מענה מסכם למשתמש",
    }.get(name, name)


def _execution_graph(entries: list[dict[str, Any]], outcome: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build only evidenced invocation, provider, tool, and persisted-outcome links."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    provider_events: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    result_events: list[dict[str, Any]] = []
    has_input = any(e.get("event") in {"report_received", "request_received"} for e in entries)
    if has_input:
        nodes.append({"id": "input", "type": "user", "label": "הודעה התקבלה", "status": "success", "icon": "👤"})
    root = {"id": "orchestrator", "type": "main", "label": "Orchestrator", "status": "success" if outcome else "running", "icon": "🤖"}
    nodes.append(root)
    if has_input:
        edges.append({"id": "input_orchestrator", "source": "input", "target": "orchestrator", "type": "input", "label": "קליטה", "status": "completed"})

    for index, entry in enumerate(entries):
        event = entry.get("event")
        invocation_id = entry.get("invocation_id")
        if event == "agent_invocation_started" and invocation_id:
            node_id = f"invocation_{invocation_id}"
            node = {
                "id": node_id, "type": "invocation", "label": entry.get("agent") or "סוכן",
                "sublabel": entry.get("stage") or "הפעלת סוכן", "icon": "🤖",
                "status": "running", "started_at": entry.get("timestamp"),
                "task": _safe_str(entry.get("task_summary"), 120),
                "allowed_tools": entry.get("allowed_tools") or [], "llm_calls": [], "tools": [],
                "parent_invocation_id": entry.get("parent_invocation_id"),
            }
            nodes.append(node)
            by_id[invocation_id] = node
        elif event == "agent_invocation_finished" and invocation_id in by_id:
            node = by_id[invocation_id]
            node["status"] = "success" if entry.get("status") == "success" else "failed"
            node["finished_at"] = entry.get("timestamp")
            node["duration_ms"] = entry.get("duration_ms")
            node["result"] = f"{entry.get('result_chars', 0)} תווים" if entry.get("status") == "success" else _safe_str(entry.get("error_type"))
        elif event == "model_invocation_finished" and not invocation_id:
            # Old traces contain a real completion log but no source invocation ID.
            # Keep each call separate; do not fabricate a specialist or tool.
            node = {
                "id": f"legacy_invocation_{index}", "type": "invocation",
                "label": entry.get("agent") or "סוכן", "sublabel": entry.get("stage") or "מודל",
                "icon": "🤖", "status": "success" if entry.get("status") == "success" else "failed",
                "finished_at": entry.get("timestamp"), "duration_ms": entry.get("latency_ms"),
                "llm_calls": [], "tools": [], "legacy": True,
            }
            finish_dt = _parse_timestamp(entry.get("timestamp"))
            if finish_dt and isinstance(entry.get("latency_ms"), (int, float)):
                node["started_at"] = (finish_dt - timedelta(milliseconds=entry["latency_ms"])).isoformat()
            nodes.append(node)
        elif event in {"provider_request_finished", "provider_request_failed"}:
            provider_events.append(entry)
        elif event in {"tool_call", "tool_blocked"}:
            tool_events.append(entry)
        elif event in {"step_result", "specialist_finished"}:
            result_events.append(entry)

    for node in [n for n in nodes if n["type"] == "invocation"]:
        parent_id = node.get("parent_invocation_id")
        parent = f"invocation_{parent_id}" if parent_id in by_id else "orchestrator"
        edges.append({
            "id": f"invoke_{node['id']}", "source": parent, "target": node["id"],
            "type": "invocation", "label": _safe_str(node.get("sublabel"), 35),
            "status": "active" if node["status"] == "running" else "completed",
        })

    for index, entry in enumerate(result_events):
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
            "type": "result", "label": "תוצאת שלב חזרה" if received else "כשל שלב חזר",
            "status": "completed" if received else "failed",
        })

    for entry in provider_events:
        node = by_id.get(entry.get("invocation_id"))
        if node is None and not entry.get("invocation_id"):
            candidates = [n for n in nodes if n.get("legacy") and n.get("sublabel") == entry.get("stage")]
            if len(candidates) == 1:
                node = candidates[0]
        call = {
            "call_id": entry.get("call_id"), "model": _safe_str(entry.get("model"), 65),
            "latency_ms": entry.get("latency_ms"), "status": entry.get("status"),
            "finish_reason": entry.get("finish_reason"), "input_tokens": entry.get("input_tokens"),
            "output_tokens": entry.get("output_tokens"),
        }
        if node is not None:
            node["llm_calls"].append(call)
        else:
            nodes.append({
                "id": f"unattributed_model_{len(nodes)}", "type": "model", "label": "קריאת מודל ללא שיוך ודאי",
                "icon": "🧠", "status": "failed" if entry.get("status") == "error" else "success", "llm_calls": [call],
            })

    for index, entry in enumerate(tool_events):
        invocation = by_id.get(entry.get("invocation_id"))
        tool_node = {
            "id": f"tool_call_{index}", "type": "tool", "label": entry.get("tool") or "כלי",
            "icon": "🔧", "status": "failed" if entry.get("status") in {"error", "blocked"} else "success",
            "duration_ms": round(float(entry.get("duration_seconds") or 0) * 1000, 1),
            "summary": _safe_str(entry.get("result_summary"), 120),
            "side_effecting": bool(entry.get("side_effecting")),
            "verification": "unknown" if entry.get("side_effecting") else "read_only",
        }
        nodes.append(tool_node)
        if invocation is not None:
            invocation["tools"].append(tool_node["id"])
            edges.append({
                "id": f"tool_edge_{index}", "source": invocation["id"], "target": tool_node["id"],
                "type": "tool_call", "label": "הפעלת כלי", "status": "completed",
            })
        else:
            tool_node["details"] = "שיוך להפעלת סוכן לא נרשם; לא מוצג קשר משוער."

    if outcome:
        nodes.append({
            "id": "persisted_outcome", "type": "outcome", "label": f"תוצאת Job: {outcome}",
            "icon": "📌", "status": "success" if outcome in {"succeeded", "closed_on_precedent"} else "failed",
            "details": "תוצאה נשמרה; אין בכך הוכחה למסירה בדפדפן.",
        })
        edges.append({"id": "outcome_edge", "source": "orchestrator", "target": "persisted_outcome", "type": "outcome", "label": "שמירת תוצאה", "status": "completed"})

    specialist_nodes = [n for n in nodes if n["type"] == "invocation" and n.get("label") not in {"main_agent", "report_composer_agent", "insights_agent"}]
    parallel_pairs = 0
    for i, left in enumerate(specialist_nodes):
        left_start = _parse_timestamp(left.get("started_at"))
        left_end = _parse_timestamp(left.get("finished_at")) or datetime.now(timezone.utc)
        if left_start is None:
            continue
        for right in specialist_nodes[i + 1:]:
            right_start = _parse_timestamp(right.get("started_at"))
            right_end = _parse_timestamp(right.get("finished_at")) or datetime.now(timezone.utc)
            if right_start and left_start < right_end and right_start < left_end:
                parallel_pairs += 1
    explanation = ""
    if not specialist_nodes:
        explanation = (
            "נסגר על בסיס תקדים לפני הפעלת מומחים וכלים."
            if outcome == "closed_on_precedent" else
            "לא נרשמה הפעלת מומחה ב־Trace הזה; אין להסיק שהתרחשה אחת."
        )
    graph_nodes_by_id = {node["id"]: node for node in nodes}
    actual_messages = [
        {"id": edge["id"], "kind": edge["type"], "from_id": edge["source"], "to_id": edge["target"],
         "from_label": graph_nodes_by_id[edge["source"]]["label"],
         "to_label": graph_nodes_by_id[edge["target"]]["label"],
         "time": graph_nodes_by_id[edge["target"]].get("started_at") or graph_nodes_by_id[edge["source"]].get("finished_at"),
         "title": edge.get("label", ""), "summary": edge.get("label", ""), "status": edge.get("status", "")}
        for edge in edges if edge["type"] in {"invocation", "result", "tool_call"}
    ]
    return {
        "nodes": nodes, "edges": edges, "specialist_count": len(specialist_nodes),
        "tool_count": len(tool_events), "explanation": explanation,
        "has_parallel": parallel_pairs > 0, "parallel_batches_count": parallel_pairs,
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

    # Stages tracking (the 8 mandatory phases)
    # 1. Ingestion, 2. Routing, 3. Intent & Extraction, 4. Agent Selection,
    # 5. Protocol Selection, 6. Tool Execution, 7. Persistence & Verification, 8. Synthesis & Outcome
    stages: dict[str, dict[str, Any]] = {
        "ingestion": {
            "id": "ingestion",
            "name": "קליטת הודעה",
            "status": "pending",
            "agent": "API / Ingestion",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "routing": {
            "id": "routing",
            "name": "ניתוב גזרה וקבוצה",
            "status": "pending",
            "agent": "Router",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "intent_extraction": {
            "id": "intent_extraction",
            "name": "הבנת כוונה וחילוץ ישויות",
            "status": "pending",
            "agent": "Main Agent",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "agent_selection": {
            "id": "agent_selection",
            "name": "בחירת סוכנים מומחים",
            "status": "pending",
            "agent": "Main Agent",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "protocol_selection": {
            "id": "protocol_selection",
            "name": "בחירת פרוטוקול תפעולי",
            "status": "pending",
            "agent": "Protocol Engine",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "tool_execution": {
            "id": "tool_execution",
            "name": "הפעלת כלים",
            "status": "pending",
            "agent": "Specialists",
            "details": "",
            "items": [],
            "started_at": None,
            "duration_ms": None,
        },
        "persistence_verification": {
            "id": "persistence_verification",
            "name": "שמירה ואימות תפעולי",
            "status": "pending",
            "agent": "Persistence Store",
            "details": "",
            "started_at": None,
            "duration_ms": None,
        },
        "synthesis": {
            "id": "synthesis",
            "name": "איסוף תוצאות ויצירת מענה",
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
            stages["ingestion"]["details"] = f"מקור: {src} | שולח: {sender} | {txt}".strip(" | ")
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "user_client",
                "from_label": f"משתמש ({sender})" if sender else "משתמש",
                "from_icon": "👤",
                "to_id": "main_agent",
                "to_label": "סוכן ראשי",
                "to_icon": "🤖",
                "kind": "input",
                "badge": "קליטת דיווח",
                "title": "קליטת הודעת משתמש",
                "summary": txt[:110] if txt else "הודעת פתיחה של סימולציה",
                "body": txt,
                "status": "success",
            })

        # 2. Routing
        if event == "group_scope_applied":
            stages["routing"]["status"] = "success"
            stages["routing"]["started_at"] = stages["routing"]["started_at"] or ts_raw
            agent = entry.get("agent", "")
            chat = entry.get("chat_id", "")
            stages["routing"]["details"] = f"שויך לסוכן {agent} בצ'אט {chat}"
        elif event == "group_binding_written":
            stages["routing"]["status"] = "success"

        # 3. Intent & Extraction
        if event == "intent_classified":
            stages["intent_extraction"]["status"] = "success"
            stages["intent_extraction"]["started_at"] = stages["intent_extraction"]["started_at"] or ts_raw
            intent = entry.get("intent", "?")
            reason = _safe_str(entry.get("reason", ""))
            stages["intent_extraction"]["details"] = f"כוונה: {intent} ({reason})"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "main_agent",
                "from_label": "סוכן ראשי",
                "from_icon": "🤖",
                "to_id": "main_agent",
                "to_label": "סוכן ראשי (הבנת כוונה)",
                "to_icon": "🧠",
                "kind": "intent",
                "badge": "הבנת כוונה",
                "title": f"סיווג כוונה: {intent}",
                "summary": f"כוונה: {intent} — {reason[:90]}",
                "body": f"כוונה: {intent}\nנימוק: {reason}",
                "status": "success",
            })
        elif event == "extraction_result":
            stages["intent_extraction"]["status"] = "success"
            cls_name = entry.get("classification") or "לא סווג"
            area = entry.get("area") or "ללא גזרה"
            missing = entry.get("missing_fields") or []
            suffix = f" | חסרים: {', '.join(missing)}" if missing else ""
            stages["intent_extraction"]["details"] += f" | גזרה: {area}, סיווג: {cls_name}{suffix}"
        elif event == "risk_assessed":
            risk = entry.get("risk_level", "?")
            score = entry.get("risk_score", "")
            stages["intent_extraction"]["details"] += f" | סיכון: {risk} (ציון: {score})"

        # 4. Agent Selection & Specialist Execution
        if event == "agent_selection":
            stages["agent_selection"]["status"] = "success"
            stages["agent_selection"]["started_at"] = stages["agent_selection"]["started_at"] or ts_raw
            chosen = entry.get("chosen_agents") or []
            reason = _safe_str(entry.get("reason", ""))
            stages["agent_selection"]["details"] = f"סוכנים שנבחרו: {', '.join(chosen) if chosen else 'ללא'} ({reason})"
        elif event == "picture_planned":
            stages["agent_selection"]["status"] = "success"
            domains = entry.get("domains") or {}
            stages["agent_selection"]["details"] = f"תמונת מצב מתוכננת מול: {', '.join(domains.keys())}"

        if event in {"specialist_started", "step_start"}:
            ag = entry.get("agent", "")
            if ag and ag != "main_agent":
                active_specialists.add(ag)
                task_text = _safe_str(entry.get("task_text") or entry.get("task") or "")
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
                    "from_label": "סוכן ראשי",
                    "from_icon": "🤖",
                    "to_id": f"specialist_{ag}",
                    "to_label": _agent_display_name(ag),
                    "to_icon": _agent_icon(ag),
                    "kind": "delegation",
                    "badge": "הוראת ביצוע",
                    "title": f"הוראת ביצוע (צעד {step_idx}) אל {_agent_display_name(ag)}",
                    "summary": f"משימה למומחה: {task_text[:110]}",
                    "body": task_text,
                    "status": "running",
                })
        elif event in {"specialist_finished", "specialist_failed", "specialist_timeout", "step_result", "step_failed"}:
            ag = entry.get("agent", "")
            if ag and ag != "main_agent":
                active_specialists.discard(ag)
                runs = agent_invocations.get(ag, [])
                is_err = "failed" in event or "timeout" in event or not entry.get("succeeded", True)
                st = "failed" if is_err else "success"
                res_text = _safe_str(entry.get("result_text") or entry.get("result") or "")
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
                    "to_label": "סוכן ראשי",
                    "to_icon": "🤖",
                    "kind": "result",
                    "badge": "תוצאת מומחה" if st == "success" else "שגיאת מומחה",
                    "title": f"החזרת תוצאה מ-{_agent_display_name(ag)}",
                    "summary": f"תוצאה: {res_text[:110]}" if res_text else ("הפעולה נכשלה" if st == "failed" else "הפעולה הושלמה"),
                    "body": res_text or ("שגיאה בביצוע שלב המומחה" if st == "failed" else "הושלם ללא תוכן"),
                    "status": st,
                })

        # 5. Protocol Selection
        if event == "protocol_selection":
            status = entry.get("status", "")
            proto = entry.get("protocol_name") or "-"
            reason = _safe_str(entry.get("reason", ""))
            stages["protocol_selection"]["started_at"] = stages["protocol_selection"]["started_at"] or ts_raw
            if status == "selected":
                stages["protocol_selection"]["status"] = "success"
                stages["protocol_selection"]["details"] = f"נבחר: {proto} ({reason})"
            elif status == "ambiguous":
                stages["protocol_selection"]["status"] = "running"
                cand = entry.get("candidate_names") or []
                stages["protocol_selection"]["details"] = f"התלבטות בין: {', '.join(cand)}"
            else:
                stages["protocol_selection"]["status"] = "failed"
                stages["protocol_selection"]["details"] = f"לא נמצא פרוטוקול: {reason}"

        # 6. Tool Execution
        if event == "tool_call":
            stages["tool_execution"]["status"] = "running"
            tool_name = entry.get("tool", "unknown")
            ag = entry.get("agent", "unknown")
            side_effecting = bool(entry.get("side_effecting", False))
            status = entry.get("status", "success")
            dur = float(entry.get("duration_seconds") or 0.0)
            total_tools_duration_sec += dur

            # Verification determination
            # Read-only tools cannot mutate state -> "read_only"
            # Write tools must show verified ONLY if authoritative verification exists
            res_summary = _safe_str(entry.get("result_summary", ""))
            verified = "read_only"
            verification_note = "קריאה בלבד (ללא שינוי מצב)"
            if side_effecting:
                # Default for unverified write is strictly "אימות לא זמין"
                verified = "unverified"
                verification_note = "אימות לא זמין (לא בוצעה קריאת אימות)"

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
            stages["tool_execution"]["details"] = f"הופעלו {len(tool_items)} כלים"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": f"specialist_{ag}" if ag and ag != "main_agent" else "main_agent",
                "from_label": _agent_display_name(ag) if ag else "סוכן ראשי",
                "from_icon": _agent_icon(ag) if ag else "🤖",
                "to_id": f"tool_{tool_name}",
                "to_label": f"כלי: {tool_name}",
                "to_icon": "🔧",
                "kind": "tool",
                "badge": "כלי (כתיבה)" if side_effecting else "כלי (קריאה)",
                "title": f"הפעלת כלי {tool_name}",
                "summary": f"קריאה לכלי {tool_name}: {res_summary[:90]}",
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
                "summary": "הפעולה נחסמה עקב הרשאות",
                "verification": "blocked",
                "verification_note": "פעולה נחסמה",
            })
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": f"specialist_{ag}" if ag and ag != "main_agent" else "main_agent",
                "from_label": _agent_display_name(ag) if ag else "סוכן ראשי",
                "from_icon": _agent_icon(ag) if ag else "🤖",
                "to_id": f"tool_{tool_name}",
                "to_label": f"כלי: {tool_name}",
                "to_icon": "🔧",
                "kind": "tool",
                "badge": "כלי נחסם",
                "title": f"חסימת כלי {tool_name}",
                "summary": f"קריאה לכלי {tool_name} נחסמה עקב הרשאות",
                "body": "הפעולה נחסמה",
                "status": "failed",
            })

        # 7. Persistence & Holds
        if event in {"hold_created", "hold_resolved"}:
            stages["persistence_verification"]["started_at"] = stages["persistence_verification"]["started_at"] or ts_raw
            kind = entry.get("hold_kind", "approval")
            if event == "hold_created":
                stages["persistence_verification"]["status"] = "waiting"
                stages["persistence_verification"]["details"] = f"השהיה פעילה: {kind} ({_safe_str(entry.get('reason', ''))})"
                messages.append({
                    "id": f"msg_{len(messages) + 1}",
                    "time": ts_raw,
                    "from_id": "main_agent",
                    "from_label": "סוכן ראשי",
                    "from_icon": "🤖",
                    "to_id": "commander_hold",
                    "to_label": "אישור מפקד",
                    "to_icon": "🛡️",
                    "kind": "hold",
                    "badge": "השהיה לאישור",
                    "title": f"בקשת אישור ({kind})",
                    "summary": f"השהיה פעילה: {_safe_str(entry.get('reason', ''))[:90]}",
                    "body": _safe_str(entry.get('reason', '')),
                    "status": "waiting",
                })
            else:
                stages["persistence_verification"]["status"] = "success"
                stages["persistence_verification"]["details"] = f"השהיה אושרה ע\"י {entry.get('resolved_by', 'commander')}"
        elif event == "event_data_saved" or event == "attendance_cycle_opened":
            stages["persistence_verification"]["status"] = "success"
            stages["persistence_verification"]["details"] = "רשומת מצב נשמרה במסד"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "main_agent",
                "from_label": "סוכן ראשי",
                "from_icon": "🤖",
                "to_id": "persistence_store",
                "to_label": "מסד נתונים (SQLite)",
                "to_icon": "🗄️",
                "kind": "persistence",
                "badge": "שמירה במסד",
                "title": "עדכון מצב תפעולי במסד",
                "summary": "רשומת אירוע נשמרה במסד הנתונים ואומתה",
                "body": "שמירת רשומת מצב במסד הנתונים ואימות",
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
                stages["synthesis"]["details"] = f"שלב {idx} ({ag}) הסתיים בהצלחה" if succeeded else f"שלב {idx} ({ag}) נכשל"

        if event in {"picture_composed", "report_composed"}:
            stages["synthesis"]["status"] = "running"
            stages["synthesis"]["details"] = "דוח מסכם הורכב בהצלחה"

        if event == "event_outcome":
            is_terminal = True
            outcome = entry.get("outcome", "unknown")
            terminal_outcome = outcome
            terminal_reason = _safe_str(entry.get("failure_reason") or entry.get("reasoning") or "")
            stages["synthesis"]["status"] = "success" if outcome in {"succeeded", "closed_on_precedent"} else "failed"
            stages["synthesis"]["details"] = f"תוצאה סופית: {outcome}"
            if terminal_reason:
                stages["synthesis"]["details"] += f" ({terminal_reason})"
            messages.append({
                "id": f"msg_{len(messages) + 1}",
                "time": ts_raw,
                "from_id": "main_agent",
                "from_label": "סוכן ראשי",
                "from_icon": "🤖",
                "to_id": "user_client",
                "to_label": "משתמש / ערוץ דיווח",
                "to_icon": "👤",
                "kind": "outcome",
                "badge": "מענה סופי",
                "title": f"מענה מסכם למשתמש ({outcome})",
                "summary": f"תוצאה: {outcome} — {terminal_reason[:90] if terminal_reason else 'הבקשה טופלה במלואה'}",
                "body": terminal_reason or f"סטטוס: {outcome}",
                "status": "success" if outcome in {"succeeded", "closed_on_precedent"} else "failed",
            })

            # If outcome is confirmed succeeded, verify side-effecting tools that completed
            if outcome == "succeeded":
                stages["persistence_verification"]["status"] = "success"
                if not stages["persistence_verification"]["details"]:
                    stages["persistence_verification"]["details"] = "רשומת מצב תפעולית אומתה"
                for t_item in tool_items:
                    if t_item["side_effecting"] and t_item["status"] == "success":
                        t_item["verification"] = "verified"
                        t_item["verification_note"] = "אומת ברישום תוצאת אירוע מוסמכת"

        if event == "api_request_finished":
            status_code = entry.get("status_code")
            if status_code == 200 and not is_terminal and not any(e.get("event") == "queue_started" for e in entries):
                is_terminal = True
                terminal_outcome = "succeeded"
                if stages["synthesis"]["status"] == "pending":
                    stages["synthesis"]["status"] = "success"
                    stages["synthesis"]["details"] = "המענה הושלם ונמסר בהצלחה"
            elif status_code and status_code >= 400 and not is_terminal:
                is_terminal = True
                terminal_outcome = "failed"
                stages["synthesis"]["status"] = "failed"
                stages["synthesis"]["details"] = f"הבקשה הסתיימה בשגיאה ({status_code})"

        # Telemetry / Provider metrics
        if event in {"provider_request_finished", "provider_request_failed"}:
            llm_call_count += 1
            lat = float(entry.get("latency_ms") or 0.0)
            total_model_latency_ms += lat

            inp = entry.get("input_tokens")
            outp = entry.get("output_tokens")
            cch = entry.get("cache_tokens")
            tot = entry.get("total_tokens")

            if any(v is not None for v in (inp, outp, cch, tot)):
                has_token_data = True
                input_tokens += int(inp or 0)
                output_tokens += int(outp or 0)
                cache_tokens += int(cch or 0)
                total_tokens += int(tot or ((inp or 0) + (outp or 0)))

        if event == "queue_started":
            queue_wait_seconds = float(entry.get("queue_wait_seconds") or 0.0)

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
        stages["tool_execution"]["status"] = "success" if all(i["status"] == "success" for i in tool_items) else "failed"
        stages["tool_execution"]["items"] = tool_items

    # Format tokens display
    tokens_payload: dict[str, Any] | None = None
    if has_token_data:
        tokens_payload = {
            "total": total_tokens,
            "input": input_tokens,
            "output": output_tokens,
            "cache": cache_tokens,
            "display": f"{total_tokens:,} (קלט: {input_tokens:,}, פלט: {output_tokens:,}, מטמון: {cache_tokens:,})",
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
        "agent_invocations_count": sum(len(runs) for runs in agent_invocations.values()),
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
            "label": "משתמש / דיווח",
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
            "label": "קליטת דיווח",
            "message_count": 1,
            "last_message": stages["ingestion"].get("details", "")[:80],
        })

    # 1. Center: Main Agent
    graph_nodes.append({
        "id": "main_agent",
        "label": "סוכן ראשי (מתכלל)",
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
            "label": "במקביל (Parallel) ⚡" if is_parallel else "הוראת ביצוע",
            "message_count": len(spec_msgs),
            "last_message": last_spec_msg,
        })

        # 3. Sub-nodes for Tools executed by this specialist
        for idx, t_info in enumerate(ag_tools):
            tool_node_id = f"tool_{ag_name}_{idx}"
            graph_nodes.append({
                "id": tool_node_id,
                "label": t_info["tool"],
                "sublabel": "פעולת כתיבה" if t_info["side_effecting"] else "פעולת קריאה",
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
                "label": "כלי (כתיבה)" if t_info["side_effecting"] else "כלי (קריאה)",
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
            "sublabel": "כלי ישיר",
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
            "label": "כלי ישיר",
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
            "label": "מסד נתונים ואימות",
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
            "label": "שמירה / אימות",
            "message_count": 1,
            "last_message": stages["persistence_verification"].get("details", "")[:80],
        })

    # Final Outcome Delivery Node (if completed)
    if is_terminal and terminal_outcome:
        outcome_node_id = "final_outcome"
        is_succ = terminal_outcome in {"succeeded", "completed", "closed_on_precedent"}
        graph_nodes.append({
            "id": outcome_node_id,
            "label": f"מענה סופי: {terminal_outcome}",
            "sublabel": "Final Outcome Delivery",
            "icon": "✅" if is_succ else "❌",
            "type": "outcome",
            "status": "success" if is_succ else "failed",
            "details": terminal_reason or f"סטטוס סופי: {terminal_outcome}",
        })
        graph_edges.append({
            "id": "edge_main_outcome",
            "source": "main_agent",
            "target": outcome_node_id,
            "type": "outcome",
            "status": "completed" if is_succ else "failed",
            "label": "מסירת מענה",
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
    queue_stopped_on_error = not is_terminal and any(
        entry.get("event") == "stage_finished" and entry.get("stage") == "queue_execution"
        and entry.get("status") == "error" for entry in entries
    )
    if queue_stopped_on_error:
        next(node for node in graph_payload["nodes"] if node["id"] == "orchestrator")["status"] = "unknown"

    return {
        "trace_id": trace_id,
        "terminal": is_terminal,
        "outcome": terminal_outcome,
        "outcome_reason": terminal_reason,
        "diagnostic_state": "job_stopped_without_outcome" if queue_stopped_on_error else None,
        "metrics": metrics,
        "stages": list(stages.values()),
        "collaboration": collaboration,
        "tool_items": tool_items,
        "messages": causal_messages,
        "event_count": len(entries),
        "graph": graph_payload,
    }
