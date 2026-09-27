"""Fresh, source-aware FIRE situational-picture composition.

Every call reads the current profile stores.  The answer is assembled from the
stored state and current-run updates; it is not a scenario-step response and it
never reuses a previously rendered picture.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from messages import get_catalog


def _catalog(language: str = "he"):
    return get_catalog(language)


def _label(catalog, key: str, fallback: str = "") -> str:
    try:
        return catalog.text(key)
    except Exception:
        return fallback or key.rsplit(".", 1)[-1]


def _status(catalog, value: str | None) -> str:
    return _label(catalog, f"fire.picture.status.{(value or '').strip().lower() or 'unknown'}")


def _area(catalog, value: str | None) -> str:
    return _label(catalog, f"fire.picture.area.{(value or '').strip().lower() or 'unknown'}")


def _vehicle_name(catalog, vehicle: dict) -> str:
    vehicle_id = str(vehicle.get("vehicle_id") or "").upper()
    key = {
        "ASHED-3": "fire.picture.vehicle.ashed_3",
        "CARMEL-1": "fire.picture.vehicle.carmel_1",
    }.get(vehicle_id)
    return _label(catalog, key, vehicle.get("display_name", vehicle_id)) if key else vehicle.get("display_name", vehicle_id)


def _force_name(catalog, force_id: str) -> str:
    return _label(catalog, f"fire.picture.force.{force_id}", force_id)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _fresh_shift_snapshot(team_agent, as_of_iso: str) -> tuple[list[dict], dict | None, list[dict]]:
    """Select the FIRE shift cycle, never an unrelated scheduler cycle."""
    store = team_agent.status_store
    as_of = _parse(as_of_iso) or datetime.now(timezone.utc)
    expected_key = f"shift-{as_of.date().isoformat()}"
    cycle = store.find_cycle(expected_key)
    if cycle is None:
        cycle = next(
            (item for item in store.list_cycles() if str(item.get("cycle_key", "")).startswith("shift-")),
            None,
        )
    if cycle is None:
        return [], None, []
    return (
        store.availability_snapshot(as_of.isoformat(), cycle_id=cycle["cycle_id"]),
        cycle,
        store.list_responses(cycle_id=cycle["cycle_id"]),
    )


def _facts(update: dict) -> dict:
    try:
        value = json.loads(update.get("facts_json") or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _current_run_updates(updates: list[dict], run_started: datetime | None) -> list[dict]:
    if run_started is None:
        return updates
    # Receipt time is the execution boundary.  Scenario timestamps repeat when
    # the demo is rerun, so filtering by occurred_at would leak the old run.
    return [item for item in updates if (_parse(item.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= run_started]


def _dedupe_updates(updates: list[dict]) -> list[dict]:
    """Collapse the incident mirror of an external-force report."""
    chosen: dict[str, dict] = {}
    for item in updates:
        source = str(item.get("source_message_id") or item.get("update_id") or "")
        base = source.removesuffix(":incident")
        previous = chosen.get(base)
        if previous is None or item.get("update_kind") == "external_force":
            chosen[base] = item
    return list(chosen.values())


def build_fire_situational_picture(registry, request_text: str = "", *, as_of_iso: str = "") -> str:
    """Read every current FIRE source and return one operational Hebrew answer."""

    del request_text  # The same fresh cross-source read serves text and button requests.
    catalog = _catalog("he")
    surveillance = registry.get("surveillance_agent")
    team = registry.get("team_status_agent")
    external_agent = registry.get("friendly_forces_agent")
    as_of = as_of_iso or datetime.now(timezone.utc).isoformat()

    cameras = list(surveillance.surveillance_store.list_cameras())
    drones = list(surveillance.surveillance_store.list_drones())
    missions = list(surveillance.surveillance_store.get_active_missions())
    incident = surveillance.operations_store.get_incident()
    updates = list(surveillance.operations_store.list_updates())
    updates += list(external_agent.operations_store.list_updates())
    forces = list(surveillance.operations_store.list_external_forces())
    forces += list(external_agent.operations_store.list_external_forces())

    run_started = _parse((incident or {}).get("run_started_at"))
    updates = _dedupe_updates(_current_run_updates(updates, run_started))
    force_by_id = {item.get("force_id"): item for item in forces}
    forces = list(force_by_id.values())
    crew, cycle, responses = _fresh_shift_snapshot(team, as_of)
    vehicles = list(team.status_store.list_vehicles())

    current: list[str] = []
    changes: list[str] = []
    risks: list[str] = []
    gaps: list[str] = []
    conclusions: list[str] = []
    recommendations: list[str] = []

    latest_incident_update = max(
        (item for item in updates if item.get("update_kind") == "fire_incident"),
        key=lambda item: item.get("received_at", ""),
        default=None,
    )
    incident_verification = (latest_incident_update or {}).get("verification_status") or "reported"
    if incident:
        current.append(catalog.text(
            "fire.picture.incident",
            status=_status(catalog, incident.get("status")),
            area=_area(catalog, incident.get("area")),
            spread=_status(catalog, incident.get("spread_status")),
            hazard=_status(catalog, incident.get("hazard_status")),
            verification=_status(catalog, incident_verification),
        ))
    else:
        gaps.append(catalog.text("fire.picture.gap.no_incident"))

    active_cameras = sum(1 for camera in cameras if camera.get("status") == "active")
    if cameras:
        current.append(catalog.text("fire.picture.cameras", active=active_cameras, total=len(cameras)))
        for camera in cameras:
            camera_id = camera.get("camera_id", "")
            status = camera.get("status")
            summary = str(camera.get("feed_summary") or "")
            if camera_id == "CAM-01" and "\u05d7\u05d5\u05dd" in summary:
                current.append(catalog.text("fire.picture.camera_heat"))
                changes.append(catalog.text("fire.picture.change.camera_heat"))
                conclusions.append(catalog.text("fire.picture.conclusion.no_link"))
            elif camera_id == "CAM-02" and status in {"offline", "degraded"}:
                current.append(catalog.text("fire.picture.camera_degraded", camera=camera_id, status=_status(catalog, status)))
                changes.append(catalog.text("fire.picture.change.camera_degraded", camera=camera_id))
                risks.append(catalog.text("fire.picture.risk.camera", camera=camera_id, status=_status(catalog, status)))
                gaps.append(catalog.text("fire.picture.gap.camera_coverage", camera=camera_id))
    else:
        gaps.append(catalog.text("fire.picture.gap.camera"))

    if drones:
        current.append(catalog.text(
            "fire.picture.drones",
            details=", ".join(f"{drone['drone_id']} — {_status(catalog, drone.get('status'))}" for drone in drones),
        ))
    else:
        gaps.append(catalog.text("fire.picture.gap.camera"))
    for mission in missions:
        current.append(catalog.text(
            "fire.picture.mission",
            drone=mission.get("drone_id", _label(catalog, "fire.picture.unknown")),
            status=_status(catalog, mission.get("status")),
            location=_area(catalog, mission.get("target_area")),
        ))

    if crew:
        counts = {"available": 0, "unavailable": 0, "awaiting_response": 0}
        for member in crew:
            state = member.get("availability", "awaiting_response")
            counts[state] = counts.get(state, 0) + 1
        opening_by_member: dict[str, dict] = {}
        for response in responses:
            opening_by_member.setdefault(response["telegram_identity"], response)
        opening_available = sum(1 for response in opening_by_member.values() if response.get("availability") == "available")
        current.append(catalog.text(
            "fire.picture.crew",
            opening=opening_available if opening_by_member else _label(catalog, "fire.picture.unknown"),
            total=len(crew),
            available=counts.get("available", 0),
            unavailable=counts.get("unavailable", 0),
            awaiting=counts.get("awaiting_response", 0),
        ))
        for member in crew:
            if member.get("availability") == "unavailable":
                until = _parse(member.get("unavailable_until"))
                until_text = until.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%H:%M") if until else _label(catalog, "fire.picture.unknown")
                current.append(catalog.text("fire.picture.crew_unavailable", name=member["full_name"], until=until_text))
                risks.append(catalog.text("fire.picture.risk.crew", name=member["full_name"]))
                conclusions.append(catalog.text("fire.picture.conclusion.crew", available=counts.get("available", 0)))
                recommendations.append(catalog.text("fire.picture.recommendation.crew", name=member["full_name"]))
        if counts.get("awaiting_response", 0):
            gaps.append(catalog.text("fire.picture.gap.crew_pending"))
    else:
        gaps.append(catalog.text("fire.picture.gap.crew"))

    if vehicles:
        current.append(catalog.text(
            "fire.picture.vehicles",
            details=", ".join(
                f"{_vehicle_name(catalog, vehicle)} — {_status(catalog, vehicle.get('status'))} ({_area(catalog, vehicle.get('current_location'))})"
                for vehicle in vehicles
            ),
        ))
    else:
        gaps.append(catalog.text("fire.picture.gap.vehicles"))

    known_force_ids = {"police", "kkl_tractors", "district_support", "citizen_trapped_report"}
    for force in forces:
        force_id = force.get("force_id")
        if force_id not in known_force_ids:
            continue
        if force_id == "police":
            current.append(catalog.text("fire.picture.force.police_detail", status=_status(catalog, "reported"), location=_area(catalog, force.get("location"))))
            changes.append(catalog.text("fire.picture.change.police"))
        elif force_id == "kkl_tractors":
            current.append(catalog.text("fire.picture.force.kkl_detail", count=force.get("count", 0), status=_status(catalog, force.get("status"))))
            changes.append(catalog.text("fire.picture.change.kkl"))
        else:
            current.append(catalog.text("fire.picture.force", name=_force_name(catalog, force_id), status=_status(catalog, force.get("status")), location=_area(catalog, force.get("location"))))
    for update in updates:
        facts = _facts(update)
        if facts.get("fire_ban") is True:
            current.append(catalog.text("fire.picture.directive.fire_ban"))
        if facts.get("forest_patrols"):
            current.append(catalog.text("fire.picture.directive.patrols"))

    if incident and incident.get("spread_status") == "spreading":
        risks.append(catalog.text("fire.picture.risk.spread"))
    if incident and incident.get("hazard_status") == "hazardous_materials_threat":
        risks.append(catalog.text("fire.picture.risk.hazard"))
    if any(update.get("verification_status") in {"reported", "unverified"} for update in updates):
        risks.append(catalog.text("fire.picture.risk.unverified"))
    if forces and any(force.get("force_id") == "police" for force in forces):
        conclusions.append(catalog.text("fire.picture.conclusion.police"))
        recommendations.append(catalog.text("fire.picture.recommendation.police"))
    if any(force.get("force_id") == "kkl_tractors" for force in forces):
        conclusions.append(catalog.text("fire.picture.conclusion.kkl"))

    # Keep only commander-relevant changes; external-force incident mirrors were
    # already collapsed above, and generic unknown reports are intentionally not
    # rendered as a change.
    if not changes:
        changes.append(catalog.text("fire.picture.no_changes"))
    if not risks:
        risks.append(catalog.text("fire.picture.no_risks"))
    if not gaps:
        gaps.append(catalog.text("fire.picture.no_gaps"))
    if not conclusions:
        conclusions.append(catalog.text("fire.picture.conclusion.no_extra"))
    if not recommendations:
        recommendations.append(catalog.text("fire.picture.recommendation.no_extra"))

    return "\n".join((
        catalog.text("fire.picture.header"),
        "",
        f"{catalog.text('fire.picture.current')}:\n" + "\n".join(f"• {line}" for line in current),
        "",
        f"{catalog.text('fire.picture.changes')}:\n" + "\n".join(f"• {line}" for line in dict.fromkeys(changes)),
        "",
        f"{catalog.text('fire.picture.risks')}:\n" + "\n".join(f"• {line}" for line in dict.fromkeys(risks)),
        "",
        f"{catalog.text('fire.picture.gaps')}:\n" + "\n".join(f"• {line}" for line in dict.fromkeys(gaps)),
        "",
        f"{catalog.text('fire.picture.conclusions')}:\n" + "\n".join(f"• {line}" for line in dict.fromkeys(conclusions)),
        "",
        f"{catalog.text('fire.picture.recommendations')}:\n" + "\n".join(f"• {line}" for line in dict.fromkeys(recommendations)),
    ))
