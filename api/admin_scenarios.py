"""Bundled legacy scenario fixtures and their strict mapping to simulator requests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "admin_scenarios"
SCENARIO_FILES = (
    "כיתת כוננת - חלק 1.json",
    "כיתת כוננת - חלק 2.json",
    "כיתת כוננת - חלק 3.json",
    "מכבי אש - חלק 1.json",
    "מכבי אש - חלק 2.json",
    "מכבי אש - חלק 3.json",
)
GROUP_SOURCES = (
    "TELEGRAM_GROUP_RESPONSE_TEAM",
    "TELEGRAM_GROUP_CAMERAS",
    "TELEGRAM_GROUP_EXTERNAL_FORCES",
)
DIRECT_SOURCE = "TELEGRAM_DIRECT_COMMANDER"
SEMANTIC_AGENT_MAP = {
    "personnel_agent": "team_status_agent",
    "vision_agent": "surveillance_agent",
    "external_comm_agent": "friendly_forces_agent",
}


class ScenarioMappingError(ValueError):
    pass


def _read(filename: str) -> dict:
    value = json.loads((SCENARIO_DIR / filename).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("event_stream"), list):
        raise ValueError(f"invalid bundled scenario: {filename}")
    return value


def scenario_catalog() -> list[dict]:
    result = []
    for filename in SCENARIO_FILES:
        raw = _read(filename)
        metadata = raw["scenario_metadata"]
        personas = list(dict.fromkeys(str(item["payload"]["sender"]) for item in raw["event_stream"]))
        group_sources = list(dict.fromkeys(
            str(item["source_chat"]) for item in raw["event_stream"] if item.get("source_chat") in GROUP_SOURCES
        ))
        result.append({
            "key": filename.removesuffix(".json"),
            "filename": filename,
            "title": metadata.get("title") or filename.removesuffix(".json"),
            "description": metadata.get("description", ""),
            "step_count": len(raw["event_stream"]),
            "personas": personas,
            "group_sources": group_sources,
            "raw": raw,
        })
    return result


def _positive_identity(value: object) -> str:
    normalized = str(value).strip()
    if not normalized.isdigit() or int(normalized) <= 0:
        raise ScenarioMappingError("every persona must map to a positive Telegram ID")
    return normalized


def _negative_chat(value: object) -> str:
    normalized = str(value).strip()
    if not normalized.startswith("-") or not normalized[1:].isdigit() or int(normalized) >= 0:
        raise ScenarioMappingError("every group must map to a negative Telegram chat ID")
    return normalized


def map_legacy_scenario(
    raw: Mapping,
    persona_ids: Mapping[str, object],
    group_ids: Mapping[str, object],
    registered_users: Mapping[str, Mapping],
    *,
    unregistered_label: str = "לא רשום",
    missing_name_label: str = "שם חסר",
) -> dict:
    """Convert one bundled schema to the canonical simulator schema.

    Agent hints are retained only as display metadata.  No hint is put in the request payload;
    the actual destination is therefore determined solely by the real group registration.
    """

    metadata = raw.get("scenario_metadata")
    stream = raw.get("event_stream")
    if not isinstance(metadata, Mapping) or not isinstance(stream, list) or not stream:
        raise ScenarioMappingError("scenario_metadata and event_stream are required")

    personas = list(dict.fromkeys(str(item["payload"]["sender"]) for item in stream))
    sources = list(dict.fromkeys(str(item["source_chat"]) for item in stream))
    if set(persona_ids) != set(personas):
        raise ScenarioMappingError("a Telegram ID mapping is required for every persona")
    group_sources = [source for source in sources if source in GROUP_SOURCES]
    if any(source not in (*GROUP_SOURCES, DIRECT_SOURCE) for source in sources):
        raise ScenarioMappingError("scenario contains an unsupported source chat")
    if set(group_ids) != set(group_sources):
        raise ScenarioMappingError("a Telegram chat ID mapping is required for every group channel")

    identities = {persona: _positive_identity(persona_ids[persona]) for persona in personas}
    chats: list[dict] = []
    for source in sources:
        if source == DIRECT_SOURCE:
            chats.append({"key": source, "kind": "message", "label": source, "telegram_chat_type": "private"})
        else:
            chats.append({
                "key": source,
                "kind": "message",
                "label": source,
                "telegram_chat_type": "supergroup",
                "telegram_chat_id": _negative_chat(group_ids[source]),
                "semantic_agent": SEMANTIC_AGENT_MAP.get(next(
                    (str(item.get("target_agent")) for item in stream if item.get("source_chat") == source), ""
                )),
            })

    steps = []
    for item in stream:
        payload = item["payload"]
        persona = str(payload["sender"])
        identity = identities[persona]
        user = registered_users.get(identity)
        name = unregistered_label if user is None else (str(user.get("full_name", "")).strip() or missing_name_label)
        steps.append({
            "step": int(item["step"]),
            "chat": str(item["source_chat"]),
            "sender_identity": identity,
            "sender_name": name,
            "text": str(payload["message"]),
            "timestamp": item.get("timestamp"),
            "semantic_agent": SEMANTIC_AGENT_MAP.get(str(item.get("target_agent"))),
        })
    steps.sort(key=lambda item: item["step"])
    if [item["step"] for item in steps] != sorted({item["step"] for item in steps}):
        raise ScenarioMappingError("scenario step numbers must be unique")

    return {
        "scenario": {
            "id": metadata.get("scenario_id", ""),
            "title": metadata.get("title", ""),
            "description": metadata.get("description", ""),
            "tags": [metadata.get("domain", ""), metadata.get("phase", "")],
            "expected_agent_actions": list(raw.get("expected_agent_actions", [])),
        },
        "chats": chats,
        "steps": steps,
    }
