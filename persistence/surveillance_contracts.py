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
    """One cameras row as a typed snapshot."""

    camera_id: str
    name: str
    area: str
    status: CameraStatus
    azimuth_degrees: int
    feed_summary: str
    last_updated: str


@dataclass(frozen=True)
class DroneInfo:
    """One drones row as a typed snapshot."""

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
    """One drone_missions row as a typed snapshot."""

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
    """Contract for camera, drone, and mission rows on a dedicated surveillance database."""

    @abstractmethod
    def list_cameras(self, area: str | None = None, status: str | None = None) -> list[dict]:
        """Return cameras rows, optionally filtered by area and status."""

    @abstractmethod
    def get_camera(self, camera_id: str) -> dict | None:
        """Return one cameras row, or None."""

    @abstractmethod
    def update_camera_feed(
        self, camera_id: str, feed_summary: str, status: str | None = None, updated_at: str | None = None
    ) -> dict:
        """Write feed_summary and optional status on a cameras row."""

    @abstractmethod
    def list_drones(self, status: str | None = None) -> list[dict]:
        """Return drones rows, optionally filtered by status."""

    @abstractmethod
    def get_drone(self, drone_id: str) -> dict | None:
        """Return one drones row, or None."""

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
    ) -> dict:
        """Create a mission and mark the chosen ready drone in_flight."""

    @abstractmethod
    def get_active_missions(self) -> list[dict]:
        """Return in-progress drone_missions rows joined with drone details."""

    @abstractmethod
    def recall_drone(self, identifier: str | None = None, now_iso: str | None = None) -> dict:
        """Abort exactly one active mission and return that drone to its home area."""

    @abstractmethod
    def recall_all_drones(self, now_iso: str | None = None) -> dict:
        """Abort every active mission and return those drones to the home area."""

    @abstractmethod
    def update_mission_status(
        self,
        mission_id: str,
        status: MissionStatus,
        notes: str | None = None,
        updated_at: str | None = None,
    ) -> dict:
        """Write a drone_missions status and free the drone when the mission ends."""

    @abstractmethod
    def surveillance_overview(self, area: str | None = None) -> dict:
        """Return cameras, drones, and active missions plus counts for one area or all."""


def open_surveillance_persistence(db_path: str) -> SurveillancePersistenceInterface:
    """Construct the dedicated surveillance store for this database path."""

    from persistence.surveillance_store import SQLiteSurveillancePersistence

    return SQLiteSurveillancePersistence(db_path)
