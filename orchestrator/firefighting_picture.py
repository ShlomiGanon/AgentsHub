"""Fresh, profile-specific FIRE situational-picture composition.

This module deliberately reads the profile stores on every call.  It is not a
scenario-step response and it does not use a previous answer as a cache.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from messages import get_catalog


def _catalog(language: str = "he"):
    return get_catalog(language)


def _label(catalog, key: str, fallback: str = "") -> str:
    try:
        return catalog.text(key)
    except Exception:
        return fallback or key.rsplit(".", 1)[-1]


def _status(catalog, value: str | None) -> str:
    value = (value or "").strip().lower()
    return _label(catalog, f"fire.picture.status.{value or 'unknown'}")


def _area(catalog, value: str | None) -> str:
    value = (value or "").strip().lower()
    return _label(catalog, f"fire.picture.area.{value or 'unknown'}")


def _force_name(catalog, force_id: str) -> str:
    return _label(catalog, f"fire.picture.force.{force_id}", force_id)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _fresh_shift_snapshot(team_agent, as_of_iso: str) -> tuple[list[dict], dict | None]:
    store = team_agent.status_store
    cycle = store.latest_cycle()
    if cycle is not None and not str(cycle.get("cycle_key", "")).startswith("shift-"):
        cycle = None
    if cycle is None:
        return [], None
    return store.availability_snapshot(as_of_iso, cycle_id=cycle["cycle_id"]), cycle


def build_fire_situational_picture(registry, request_text: str = "", *, as_of_iso: str = "") -> str:
    """Read all current FIRE domains and return a Hebrew operational answer."""

    del request_text  # The sources are always read in full; relevance is reflected in the sections below.
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
    forces = list(surveillance.operations_store.list_external_forces())
    # The external-forces specialist has its own bound store handle.  Read it
    # too and merge by stable keys so a picture cross-checks the agent view
    # against the surveillance agent's incident view instead of trusting one
    # in-memory object or a prior response.
    external_updates = list(external_agent.operations_store.list_updates())
    updates_by_id = {item.get("update_id"): item for item in updates}
    updates_by_id.update({item.get("update_id"): item for item in external_updates})
    updates = list(updates_by_id.values())
    force_by_id = {item.get("force_id"): item for item in forces}
    force_by_id.update({item.get("force_id"): item for item in external_agent.operations_store.list_external_forces()})
    forces = list(force_by_id.values())
    crew, cycle = _fresh_shift_snapshot(team, as_of)
    vehicles = list(team.status_store.list_vehicles())

    run_started = _parse((incident or {}).get("run_started_at"))
    if run_started is not None:
        updates = [u for u in updates if (_parse(u.get("occurred_at")) or run_started) >= run_started]

    current: list[str] = []
    risks: list[str] = []
    gaps: list[str] = []

    if incident:
        current.append(catalog.text(
            "fire.picture.incident",
            status=_status(catalog, incident.get("status")),
            area=_area(catalog, incident.get("area")),
            spread=_status(catalog, incident.get("spread_status")),
            hazard=_status(catalog, incident.get("hazard_status")),
        ))
    else:
        gaps.append(catalog.text("fire.picture.gap.no_incident"))

    if cameras:
        camera_details = ", ".join(
            f"{camera['camera_id']} — {_status(catalog, camera.get('status'))}"
            for camera in cameras
        )
        active = sum(1 for camera in cameras if camera.get("status") == "active")
        current.append(catalog.text("fire.picture.cameras", active=active, total=len(cameras), details=camera_details))
        for camera in cameras:
            if camera.get("status") in {"offline", "degraded"}:
                risks.append(catalog.text(
                    "fire.picture.risk.camera",
                    camera=camera.get("camera_id", _label(catalog, "fire.picture.unknown")),
                    status=_status(catalog, camera.get("status")),
                ))
    else:
        gaps.append(catalog.text("fire.picture.gap.camera"))

    if drones:
        drone_details = ", ".join(
            f"{drone['drone_id']} — {_status(catalog, drone.get('status'))}"
            for drone in drones
        )
        current.append(catalog.text("fire.picture.drones", details=drone_details))
    else:
        gaps.append(catalog.text("fire.picture.gap.camera"))
    for mission in missions:
        current.append(catalog.text(
            "fire.picture.force",
            name=mission.get("drone_id", _label(catalog, "fire.picture.unknown")),
            status=_status(catalog, mission.get("status")),
            location=_area(catalog, mission.get("target_area")),
        ))

    if crew:
        counts = {"available": 0, "unavailable": 0, "awaiting_response": 0}
        for member in crew:
            counts[member.get("availability", "awaiting_response")] = counts.get(member.get("availability"), 0) + 1
        current.append(catalog.text(
            "fire.picture.crew",
            available=counts.get("available", 0),
            unavailable=counts.get("unavailable", 0),
            awaiting=counts.get("awaiting_response", 0),
        ))
        unavailable = [member["full_name"] for member in crew if member.get("availability") == "unavailable"]
        if unavailable:
            current.append(catalog.text("fire.picture.crew_unavailable", names=", ".join(unavailable)))
        if counts.get("unavailable", 0) or counts.get("awaiting_response", 0):
            risks.append(catalog.text("fire.picture.risk.crew"))
        if counts.get("awaiting_response", 0):
            gaps.append(catalog.text("fire.picture.gap.crew"))
    else:
        current.append(catalog.text("fire.picture.crew", available=_label(catalog, "fire.picture.unknown"), unavailable=_label(catalog, "fire.picture.unknown"), awaiting=_label(catalog, "fire.picture.unknown")))
        gaps.append(catalog.text("fire.picture.gap.crew"))

    if vehicles:
        current.append(catalog.text(
            "fire.picture.vehicles",
            details=", ".join(
                f"{vehicle['vehicle_id']} — {_status(catalog, vehicle.get('status'))} ({_area(catalog, vehicle.get('current_location'))})"
                for vehicle in vehicles
            ),
        ))
    else:
        gaps.append(catalog.text("fire.picture.gap.crew"))

    if forces:
        for force in forces:
            current.append(catalog.text(
                "fire.picture.force",
                name=_force_name(catalog, force.get("force_id", "external_report")),
                status=_status(catalog, force.get("status")),
                location=_area(catalog, force.get("location")),
            ))
    else:
        gaps.append(catalog.text("fire.picture.gap.external"))

    for update in updates:
        try:
            facts = json.loads(update.get("facts_json") or "{}")
        except (TypeError, ValueError):
            facts = {}
        if facts.get("fire_ban") is True:
            current.append(catalog.text("fire.picture.directive.fire_ban"))
        if facts.get("forest_patrols"):
            current.append(catalog.text("fire.picture.directive.patrols"))

    if incident and incident.get("spread_status") == "spreading":
        risks.append(catalog.text("fire.picture.risk.spread"))
    if incident and incident.get("hazard_status") == "hazardous_materials_threat":
        risks.append(catalog.text("fire.picture.risk.hazard"))
    if any(update.get("verification_status") == "unverified" for update in updates):
        risks.append(catalog.text("fire.picture.risk.unverified"))

    changes: list[str] = []
    for update in sorted(updates, key=lambda item: (item.get("occurred_at", ""), item.get("update_id", "")), reverse=True)[:5]:
        kind = "fire.picture.change.external" if update.get("update_kind") == "external_force" else "fire.picture.change.incident"
        if update.get("update_kind") == "surveillance_report":
            kind = "fire.picture.change.surveillance"
        changes.append(catalog.text(
            "fire.picture.change_line",
            kind=_label(catalog, kind),
            verification=_status(catalog, update.get("verification_status")),
            status=_status(catalog, update.get("spread_status") or update.get("status")),
        ))

    if not changes:
        changes.append(catalog.text("fire.picture.no_changes"))
    if not risks:
        risks.append(catalog.text("fire.picture.no_risks"))
    if not gaps:
        gaps.append(catalog.text("fire.picture.no_gaps"))

    return "\n".join((
        catalog.text("fire.picture.header"),
        "",
        f"{catalog.text('fire.picture.current')}:\n" + "\n".join(f"• {line}" for line in current),
        "",
        f"{catalog.text('fire.picture.changes')}:\n" + "\n".join(f"• {line}" for line in changes),
        "",
        f"{catalog.text('fire.picture.risks')}:\n" + "\n".join(f"• {line}" for line in risks),
        "",
        f"{catalog.text('fire.picture.gaps')}:\n" + "\n".join(f"• {line}" for line in gaps),
    ))
