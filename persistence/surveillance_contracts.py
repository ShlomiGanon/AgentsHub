"""Storage contracts and data models for the surveillance (cameras & drones) system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

CameraStatus = Literal["active", "offline", "degraded"]
DroneStatus = Literal["ready", "in_flight", "charging", "maintenance"]
MissionStatus = Literal["dispatched", "en_route", "on_station", "completed", "aborted"]


class SurveillancePersistenceError(Exception):
    """The surveillance state request could not be completed."""


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
    def list_cameras(self, area: str | None = None, status: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_camera(self, camera_id: str) -> dict | None: ...

    @abstractmethod
    def update_camera_feed(
        self, camera_id: str, feed_summary: str, status: str | None = None, updated_at: str | None = None
    ) -> dict: ...

    @abstractmethod
    def list_drones(self, status: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_drone(self, drone_id: str) -> dict | None: ...

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
    ) -> dict: ...

    @abstractmethod
    def get_active_missions(self) -> list[dict]: ...

    @abstractmethod
    def update_mission_status(
        self,
        mission_id: str,
        status: MissionStatus,
        notes: str | None = None,
        updated_at: str | None = None,
    ) -> dict: ...

    @abstractmethod
    def surveillance_overview(self, area: str | None = None) -> dict: ...


def open_surveillance_persistence(db_path: str) -> SurveillancePersistenceInterface:
    from persistence.surveillance_store import SQLiteSurveillancePersistence

    return SQLiteSurveillancePersistence(db_path)
