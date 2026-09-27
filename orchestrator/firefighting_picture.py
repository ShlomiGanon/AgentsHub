"""Fresh FIRE status from the existing specialist stores."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from messages import get_catalog


JERUSALEM = ZoneInfo("Asia/Jerusalem")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _label(catalog, prefix: str, value: str | None) -> str:
    key = f"{prefix}.{(value or '').strip().lower() or 'unknown'}"
    try:
        return catalog.text(key)
    except Exception:
        return value or catalog.text("fire.picture.unknown")


def _local_time(value: str | None) -> str:
    parsed = _parse(value)
    return parsed.astimezone(JERUSALEM).strftime("%H:%M") if parsed else "לא ידועה"


def _shift_snapshot(team_agent, as_of_iso: str) -> tuple[list[dict], dict | None, list[dict]]:
    store = team_agent.status_store
    as_of = _parse(as_of_iso) or datetime.now(timezone.utc)
    cycle_key = f"shift-{as_of.astimezone(JERUSALEM).date().isoformat()}"
    cycle = store.find_cycle(cycle_key)
    if cycle is None:
        return [], None, []
    responses = [
        response for response in store.list_responses(cycle_id=cycle["cycle_id"])
        if response.get("approval_status") == "accepted"
    ]
    return store.availability_snapshot(as_of.isoformat(), cycle_id=cycle["cycle_id"]), cycle, responses


def _run_updates(operations_store, run_started: datetime | None) -> list[dict]:
    updates = operations_store.list_updates()
    if run_started is not None:
        updates = [
            update for update in updates
            if (_parse(update.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= run_started
        ]
    return updates


def _source(update: dict, facts: dict, force_by_id: dict, catalog) -> str:
    if facts.get("reported_by"):
        return str(facts["reported_by"])
    force = force_by_id.get(facts.get("force_id"))
    if force:
        return _label(catalog, "fire.picture.force", force["force_id"])
    return "דיווח שטח"


def _format_update(update: dict, force_by_id: dict, catalog) -> str:
    try:
        facts = json.loads(update.get("facts_json") or "{}")
    except (TypeError, ValueError):
        facts = {}
    source = _source(update, facts, force_by_id, catalog)
    verification = _label(catalog, "fire.picture.status", update.get("verification_status"))
    summary = str(update.get("summary") or "").strip()
    return catalog.text("fire.picture.change_line", source=source, summary=summary, verification=verification)


def _section(catalog, title_key: str, lines: list[str]) -> list[str]:
    return [f"{catalog.text(title_key)}:", *(f"• {line}" for line in lines)] if lines else []


def build_fire_situational_picture(registry, request_text: str = "", *, as_of_iso: str = "") -> str:
    """Read the current FIRE stores and compose a source-aware Hebrew answer."""
    catalog = get_catalog("he")
    normalized_request = request_text.casefold()
    asks_crew_or_vehicles = any(term in normalized_request for term in ("סד\"כ", "כוח", "כוחות", "צוות", "רכב", "רכבים"))
    asks_other_context = any(
        term in normalized_request
        for term in ("אש", "שריפה", "מצלמ", "רחפ", "חוץ", "כוננות", "תצפית", "חומס", "לא אומת", "סיכון", "אירוע", "דחוף", "שרב")
    )
    crew_vehicle_only = asks_crew_or_vehicles and not asks_other_context
    surveillance = registry.get("surveillance_agent")
    team_agent = registry.get("team_status_agent")
    operations = surveillance.operations_store
    as_of = _parse(as_of_iso) or datetime.now(timezone.utc)
    run_started = _parse((operations.get_incident() or {}).get("run_started_at"))

    cameras = surveillance.surveillance_store.list_cameras()
    drones = surveillance.surveillance_store.list_drones()
    missions = surveillance.surveillance_store.get_active_missions()
    updates = _run_updates(operations, run_started) if not crew_vehicle_only else []
    forces = (
        {row["force_id"]: row for row in operations.list_external_forces()}
        if not crew_vehicle_only else {}
    )
    if crew_vehicle_only:
        cameras, drones, missions = [], [], []
    crew, cycle, responses = _shift_snapshot(team_agent, as_of.isoformat())
    vehicles = team_agent.status_store.list_vehicles()

    current: list[str] = []
    changes: list[str] = []
    risks: list[str] = []
    gaps: list[str] = []
    conclusions: list[str] = []
    recommendations: list[str] = []

    local_as_of = as_of.astimezone(JERUSALEM)
    current.append(catalog.text("fire.picture.as_of", date=local_as_of.strftime("%d.%m.%Y"), time=local_as_of.strftime("%H:%M")))

    incident_updates = [row for row in updates if row.get("update_kind") == "fire_incident"]
    incident_states = {
        row["incident_id"]: row for row in operations.list_incidents()
        if row.get("incident_id") != "EVT-FIRE-444-BRUSH"
        and (run_started is None or (_parse(row.get("run_started_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= run_started)
    }
    for update in incident_updates:
        try:
            facts = json.loads(update.get("facts_json") or "{}")
        except (TypeError, ValueError):
            facts = {}
        state = incident_states.get(update.get("incident_id"), {})
        source = _source(update, facts, forces, catalog)
        current.append(catalog.text(
            "fire.picture.incident", source=source,
            status=_label(catalog, "fire.picture.status", state.get("status")),
            area=_label(catalog, "fire.picture.area", facts.get("reported_area")),
            spread=_label(catalog, "fire.picture.status", facts.get("spread_status")),
            hazard=_label(catalog, "fire.picture.status", facts.get("hazard_status")),
            verification=_label(catalog, "fire.picture.status", update.get("verification_status")),
        ))
        direction = facts.get("reported_direction")
        if direction:
            current.append(catalog.text("fire.picture.direction", area=_label(catalog, "fire.picture.area", direction)))
        changes.append(_format_update(update, forces, catalog))
        if update.get("verification_status") in {"reported", "unverified"}:
            risks.append(f"דיווח האש של {source} טרם אומת במקור נוסף.")
            recommendations.append(catalog.text("fire.picture.recommendation.verify"))
    if not incident_updates and not forces and not crew_vehicle_only:
        gaps.append("לא נשמר דיווח אש בריצה הנוכחית.")

    asks_exact_location = (
        ("מדויק" in request_text and any(term in request_text for term in ("מיקום", "נקודה", "איפה")))
        or "קואורדינט" in request_text
    )
    if asks_exact_location:
        current.append(catalog.text("fire.picture.location.precise_unknown"))

    if cameras:
        active_cameras = sum(camera.get("status") == "active" for camera in cameras)
        current.append(catalog.text("fire.picture.cameras", active=active_cameras, total=len(cameras)))
        cycle_opened = _parse((cycle or {}).get("opened_at"))
        for camera in cameras:
            summary = str(camera.get("feed_summary") or "").strip()
            is_reset = summary.startswith("FIRE simulation reset:")
            area = _label(catalog, "fire.picture.area", camera.get("area"))
            observation = summary if summary and not is_reset else "לא התקבל עדכון תצפית נוסף."
            if "\u05d7\u05d5\u05dd" in summary and "\u05e0\u05de\u05d5\u05db\u05d4" in summary:
                observation += " " + catalog.text("fire.picture.camera_heat")
            current.append(catalog.text(
                "fire.picture.camera", camera=camera["camera_id"], area=area,
                status=_label(catalog, "fire.picture.status", camera.get("status")), observation=observation,
            ))
            updated = _parse(camera.get("last_updated"))
            if not is_reset and updated and cycle_opened and updated > cycle_opened:
                changes.append(f"{camera['camera_id']} ({area}): {summary}")
            if camera.get("status") in {"offline", "degraded"}:
                risks.append(catalog.text(
                    "fire.picture.risk.camera", camera=camera["camera_id"],
                    status=_label(catalog, "fire.picture.status", camera.get("status")),
                ))
                gaps.append(catalog.text("fire.picture.gap.camera_coverage", camera=camera["camera_id"]))
                recommendations.append(catalog.text("fire.picture.recommendation.coverage"))
    else:
        gaps.append("לא זמינים נתוני מצלמות מהסוכן.")

    mission_by_drone = {mission.get("drone_id"): mission for mission in missions}
    if drones:
        drone_lines = []
        for drone in drones:
            mission = mission_by_drone.get(drone["drone_id"])
            if mission:
                drone_lines.append(catalog.text(
                    "fire.picture.mission", drone=drone["drone_id"],
                    status=_label(catalog, "fire.picture.status", mission.get("status")),
                    location=_label(catalog, "fire.picture.area", mission.get("target_area")),
                ))
            else:
                drone_lines.append(f"{drone['drone_id']}: {_label(catalog, 'fire.picture.status', drone.get('status'))}.")
        current.append(catalog.text("fire.picture.drones", details="; ".join(drone_lines)))

    if crew:
        counts = {"available": 0, "unavailable": 0, "planned_return": 0, "awaiting_response": 0}
        for member in crew:
            state = member.get("availability", "awaiting_response")
            counts[state] = counts.get(state, 0) + 1
        opening = {}
        for response in responses:
            if response.get("availability") == "available":
                opening.setdefault(response["telegram_identity"], response)
        current.append(catalog.text(
            "fire.picture.crew", opening=len(opening), total=len(crew),
            available=counts["available"], unavailable=counts["unavailable"],
            planned_return=counts["planned_return"], awaiting=counts["awaiting_response"],
        ))
        for member in crew:
            if member.get("availability") in {"unavailable", "planned_return"}:
                current.append(catalog.text(
                    "fire.picture.crew_unavailable", name=member["full_name"],
                    from_time=_local_time(member.get("unavailable_from")),
                    until=_local_time(member.get("unavailable_until")),
                ))
                risks.append(f"{member['full_name']}: החזרה המתוכננת לא אומתה.")
                changes.append(f"{member['full_name']}: {member.get('original_text') or 'דווחה היעדרות מתוכננת'}")
        if counts["awaiting_response"]:
            gaps.append(catalog.text("fire.picture.gap.crew_pending"))
    else:
        gaps.append("מחזור המשמרת של התאריך המבוקש אינו זמין.")

    if vehicles:
        vehicle_lines = []
        for vehicle in vehicles:
            vehicle_id = str(vehicle.get("vehicle_id") or "")
            name = _label(catalog, "fire.picture.vehicle", vehicle_id.lower().replace("-", "_"))
            location = _label(catalog, "fire.picture.area", vehicle.get("current_location"))
            vehicle_lines.append(f"{name}: {_label(catalog, 'fire.picture.status', vehicle.get('status'))} ({location})")
            if vehicle.get("status") not in {"available", "ready"}:
                changes.append(f"{name}: {_label(catalog, 'fire.picture.status', vehicle.get('status'))}; מיקום: {location}.")
        current.append(catalog.text("fire.picture.vehicles", details="; ".join(vehicle_lines)))
    else:
        gaps.append("לא נשמר מצב עדכני לרכבי הכיבוי.")

    for force in forces.values():
        source = _label(catalog, "fire.picture.force", force["force_id"])
        location = _label(catalog, "fire.picture.area", force.get("location"))
        current.append(catalog.text(
            "fire.picture.force_detail", source=source,
            summary=force.get("notes") or "לא נמסר פירוט נוסף.", location=location,
            status=_label(catalog, "fire.picture.status", force.get("status")),
            verification=_label(catalog, "fire.picture.status", "reported"),
        ))
        if force.get("status") in {"reported", "reported_on_scene", "en_route"}:
            risks.append(f"דיווח {source} טרם אומת בנפרד.")
            recommendations.append(catalog.text("fire.picture.recommendation.verify"))
    for update in updates:
        try:
            facts = json.loads(update.get("facts_json") or "{}")
        except (TypeError, ValueError):
            facts = {}
        if facts.get("reported_by") or update.get("update_kind") == "external_force":
            if update.get("update_kind") == "external_force":
                changes.append(_format_update(update, forces, catalog))
        if facts.get("fire_ban"):
            current.append("קק״ל דיווחה על איסור הדלקת אש בשטחים הפתוחים.")
        if facts.get("forest_patrols"):
            current.append("קק״ל דיווחה על סיורי יער.")

    if any(force.get("status") == "en_route" for force in forces.values()):
        conclusions.append("כוח שדווח בדרך טרם נחשב לכוח שהגיע; נדרשת הודעת הגעה לפני הקצאתו בזירה.")
        recommendations.append("לתאם גזרות ולוודא הגעה מול הכוח החיצוני.")
    if any(vehicle.get("status") == "dispatched" for vehicle in vehicles) and any(
        member.get("availability") in {"unavailable", "planned_return"} for member in crew
    ):
        conclusions.append("רכב יצא בזמן שחבר צוות בהיעדרות מתוכננת; הרישום אינו מציין היכן נמצאים יתר אנשי הצוות.")
        recommendations.append(catalog.text("fire.picture.recommendation.resources"))
    if any(camera.get("status") in {"offline", "degraded"} for camera in cameras) and any(
        update.get("spread_status") == "spreading" for update in incident_updates
    ):
        conclusions.append("התפשטות דווחה בזמן שכיסוי מצלמה מוגבל; תמונת המצב מהגזרה אינה מלאה.")

    heat_cameras = [
        camera for camera in cameras
        if "\u05d7\u05d5\u05dd" in str(camera.get("feed_summary") or "")
        and "\u05e0\u05de\u05d5\u05db\u05d4" in str(camera.get("feed_summary") or "")
    ]
    reported_areas = set()
    for update in incident_updates:
        try:
            reported_areas.add(json.loads(update.get("facts_json") or "{}").get("reported_area") or "unknown")
        except (TypeError, ValueError):
            reported_areas.add("unknown")
    reported_areas.update(force.get("location") for force in forces.values() if force.get("location"))
    if heat_cameras and reported_areas:
        separate_areas = [
            _label(catalog, "fire.picture.area", camera.get("area"))
            for camera in heat_cameras
            if camera.get("area") not in reported_areas
        ]
        if separate_areas:
            conclusions.append(
                f"אין כרגע מידע המקשר בין התראת החום ב{', '.join(separate_areas)} לדיווחי האש והכוחות החיצוניים."
            )

    if not changes and cycle:
        changes.append("לא נשמר שינוי משמעותי מאז פתיחת המשמרת.")

    sections = [
        catalog.text("fire.picture.header"),
        *_section(catalog, "fire.picture.current", list(dict.fromkeys(current))),
        *_section(catalog, "fire.picture.changes", list(dict.fromkeys(changes))),
        *_section(catalog, "fire.picture.risks", list(dict.fromkeys(risks))),
        *_section(catalog, "fire.picture.gaps", list(dict.fromkeys(gaps))),
        *_section(catalog, "fire.picture.conclusions", list(dict.fromkeys(conclusions))),
        *_section(catalog, "fire.picture.recommendations", list(dict.fromkeys(recommendations))),
    ]
    return "\n\n".join(sections)
