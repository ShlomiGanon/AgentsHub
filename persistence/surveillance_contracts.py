"""Storage contracts and data models for the surveillance (cameras & drones) system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal
from persistence.operational_scope import OperationalScope

CameraStatus = Literal["active", "offline", "degraded"]
DroneStatus = Literal["ready", "in_flight", "charging", "maintenance"]
MissionStatus = Literal["dispatched", "en_route", "on_station", "completed", "aborted"]


class SurveillancePersistenceError(Exception):
    """The surveillance state request could not be completed."""


@dataclass(frozen=True)
class SeedReconciliationResult:
    """Compact outcome of an additive canonical surveillance-seed pass."""

    examined: int
    inserted: int
    preserved: int
    skipped: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CameraInfo:
    camera_id: str
    name: str
    area: str
    status: CameraStatus
    azimuth_degrees: int
    feed_summary: str
    last_updated: str


@dataclass(frozen=True)
class DroneInfo:
    drone_id: str
    callsign: str
    model: str
    status: DroneStatus
    battery_percent: int
    current_area: str
    assigned_mission_id: str | None
    last_updated: str


@dataclass(frozen=True)
class DroneMission:
    mission_id: str
    drone_id: str
    target_area: str
    mission_type: str
    incident_description: str
    status: MissionStatus
    dispatched_by: str
    dispatched_at: str
    eta_seconds: int
    notes: str
    updated_at: str


class SurveillancePersistenceInterface(ABC):
    @abstractmethod
    def ensure_scope(self, scope: OperationalScope, baseline: dict | None = None) -> None: ...

    @abstractmethod
    def reconcile_camera_seed(
        self, seed: tuple[tuple, ...] | None = None, *, scope: OperationalScope | None = None
    ) -> SeedReconciliationResult: ...

    @abstractmethod
    def list_cameras(
        self, area: str | None = None, status: str | None = None, *, scope: OperationalScope | None = None
    ) -> list[dict]: ...

    @abstractmethod
    def get_camera(self, camera_id: str, *, scope: OperationalScope | None = None) -> dict | None: ...

    @abstractmethod
    def update_camera_feed(
        self, camera_id: str, feed_summary: str, status: str | None = None, updated_at: str | None = None,
        *, scope: OperationalScope | None = None
    ) -> dict: ...

    @abstractmethod
    def list_drones(self, status: str | None = None, *, scope: OperationalScope | None = None) -> list[dict]: ...

    @abstractmethod
    def get_drone(self, drone_id: str, *, scope: OperationalScope | None = None) -> dict | None: ...

    @abstractmethod
    def dispatch_drone(
        self,
        *,
        target_area: str,
        incident_description: str,
        mission_type: str = "recon",
        dispatched_by: str = "commander",
        specific_drone_id: str | None = None,
        now_iso: str | None = None,
        scope: OperationalScope | None = None,
    ) -> dict: ...

    @abstractmethod
    def get_active_missions(self, *, scope: OperationalScope | None = None) -> list[dict]: ...

    @abstractmethod
    def recall_drone(self, identifier: str | None = None, now_iso: str | None = None, *, scope: OperationalScope | None = None) -> dict: ...

    @abstractmethod
    def recall_all_drones(self, now_iso: str | None = None, *, scope: OperationalScope | None = None) -> dict: ...

    @abstractmethod
    def update_mission_status(
        self,
        mission_id: str,
        status: MissionStatus,
        notes: str | None = None,
        updated_at: str | None = None,
        *, scope: OperationalScope | None = None,
    ) -> dict: ...

    @abstractmethod
    def surveillance_overview(self, area: str | None = None, *, scope: OperationalScope | None = None) -> dict: ...

    def clear_runtime_state(self, *, scope: OperationalScope | None = None) -> dict[str, int]:
        """Remove runtime missions and restore mutable demo state."""

        raise NotImplementedError


def open_surveillance_persistence(
    db_path: str,
    *,
    seed_demo_data: bool = True,
    seed_profile: str = "",
) -> SurveillancePersistenceInterface:
    from persistence.surveillance_store import SQLiteSurveillancePersistence

    return SQLiteSurveillancePersistence(
        db_path,
        seed_demo_data=seed_demo_data,
        seed_profile=seed_profile,
    )
