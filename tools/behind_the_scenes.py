"""Behind-The-Scenes (מאחורי הקלעים) trace aggregation and diagnostics engine.

Extracts real execution stages, agent collaboration graphs, tool side-effects,
and performance metrics from raw SQLite log_entries without altering any core logic.
Strictly read-only and safe for operator diagnosis.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

        if event == "specialist_started":
            ag = entry.get("agent", "")
            if ag:
                active_specialists.add(ag)
                agent_invocations.setdefault(ag, []).append({
                    "run_index": len(agent_invocations[ag]) + 1,
                    "parent": entry.get("parent_agent", "main_agent"),
                    "status": "running",
                    "started_at": ts_raw,
                    "task": _safe_str(entry.get("task", "")),
                })
                current_parallel_batch.append(ag)
        elif event in {"specialist_finished", "specialist_failed", "specialist_timeout"}:
            ag = entry.get("agent", "")
            if ag:
                active_specialists.discard(ag)
                runs = agent_invocations.get(ag, [])
                st = "failed" if "failed" in event or "timeout" in event else entry.get("status", "success")
                if runs:
                    runs[-1]["status"] = st
                    runs[-1]["duration_ms"] = entry.get("duration_ms")
                else:
                    agent_invocations[ag] = [{
                        "run_index": 1,
                        "parent": entry.get("parent_agent", "main_agent"),
                        "status": st,
                        "started_at": ts_raw,
                    }]

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

        # 7. Persistence & Holds
        if event in {"hold_created", "hold_resolved"}:
            stages["persistence_verification"]["started_at"] = stages["persistence_verification"]["started_at"] or ts_raw
            kind = entry.get("hold_kind", "approval")
            if event == "hold_created":
                stages["persistence_verification"]["status"] = "waiting"
                stages["persistence_verification"]["details"] = f"השהיה פעילה: {kind} ({_safe_str(entry.get('reason', ''))})"
            else:
                stages["persistence_verification"]["status"] = "success"
                stages["persistence_verification"]["details"] = f"השהיה אושרה ע\"י {entry.get('resolved_by', 'commander')}"
        elif event == "event_data_saved" or event == "attendance_cycle_opened":
            stages["persistence_verification"]["status"] = "success"
            stages["persistence_verification"]["details"] = "רשומת מצב נשמרה במסד"

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
    def _agent_display_name(name: str) -> str:
        hebrew_names = {
            "main_agent": "סוכן ראשי",
            "security_agent": "מומחה אבטחה",
            "medical_agent": "מומחה רפואה",
            "fire_agent": "מומחה כיבוי אש",
            "engineering_agent": "מומחה הנדסה",
            "surveillance_agent": "מומחה תצפית ורחפנים",
            "insights_agent": "סוכן תובנות",
            "report_composer": "מרכיב דוחות",
            "persistence_store": "מסד נתונים ואימות",
        }
        return hebrew_names.get(name, name)

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

    # 1. Center: Main Agent
    graph_nodes.append({
        "id": "main_agent",
        "label": "סוכן ראשי",
        "sublabel": "Main Agent Orchestrator",
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
        ag_tools = [t for t in tool_items if t["agent"] == ag_name]
        retries = sum(1 for r in runs if r.get("status") == "retry")

        node_id = f"specialist_{ag_name}"
        graph_nodes.append({
            "id": node_id,
            "label": _agent_display_name(ag_name),
            "sublabel": ag_name,
            "type": "specialist",
            "status": ag_status,
            "is_parallel": is_parallel,
            "duration_ms": round(ag_dur, 1) if ag_dur > 0 else None,
            "call_count": len(runs),
            "tasks": ag_tasks,
            "tools": ag_tools,
            "retries": retries,
        })

        # Connecting edge from Main Agent to Specialist
        edge_status = "active" if ag_status == "running" else ("completed" if ag_status == "success" else ("failed" if ag_status == "failed" else "pending"))
        graph_edges.append({
            "id": f"edge_main_{ag_name}",
            "source": "main_agent",
            "target": node_id,
            "type": "delegation",
            "status": edge_status,
            "is_parallel": is_parallel,
            "label": "במקביל (Parallel)" if is_parallel else "הפעלה",
        })

        # 3. Sub-nodes for Tools executed by this specialist
        for idx, t_info in enumerate(ag_tools):
            tool_node_id = f"tool_{ag_name}_{idx}"
            graph_nodes.append({
                "id": tool_node_id,
                "label": t_info["tool"],
                "sublabel": "פעולת כתיבה" if t_info["side_effecting"] else "פעולת קריאה",
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
            })

    # Tools called directly by Main Agent or unknown
    direct_tools = [t for t in tool_items if t["agent"] in {"main_agent", "unknown", ""}]
    for idx, t_info in enumerate(direct_tools):
        tool_node_id = f"tool_direct_{idx}"
        graph_nodes.append({
            "id": tool_node_id,
            "label": t_info["tool"],
            "sublabel": "כלי ישיר",
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
        })

    # Persistence / Database Node (if write tools or persistence verification ran)
    persist_status = stages["persistence_verification"]["status"]
    has_write_tools = any(t["side_effecting"] for t in tool_items)
    if persist_status != "pending" or has_write_tools:
        store_node_id = "persistence_store"
        graph_nodes.append({
            "id": store_node_id,
            "label": "שמירה ואימות נתונים",
            "sublabel": "SQLite Store & Verification",
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
        })

    graph_payload = {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "has_parallel": any(e.get("is_parallel") for e in graph_edges),
        "parallel_batches_count": len(parallel_batches),
    }

    return {
        "trace_id": trace_id,
        "terminal": is_terminal,
        "outcome": terminal_outcome,
        "outcome_reason": terminal_reason,
        "metrics": metrics,
        "stages": list(stages.values()),
        "collaboration": collaboration,
        "tool_items": tool_items,
        "event_count": len(entries),
        "graph": graph_payload,
    }
