"""Public persistence facade."""

import sys

from persistence import contracts
from persistence.contracts import EventSearchCriteria, NotFoundError, PersistenceError, PersistenceInterface, open_persistence

exceptions = contracts
interface = contracts
sys.modules[f"{__name__}.exceptions"] = contracts
sys.modules[f"{__name__}.interface"] = contracts

from persistence import sqlite_store
from persistence.team_status_contracts import (
    AttendanceCycle,
    TeamStatusPersistenceInterface,
    TeamStatusPersistenceError,
    open_team_status_persistence,
)
from persistence.surveillance_contracts import (
    CameraInfo,
    DroneInfo,
    DroneMission,
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
    open_surveillance_persistence,
)
from persistence.response_team_store import (
    NeighboringForceStore,
    NeighboringForceStoreError,
    ResponseTeamRosterStore,
    ResponseTeamSurveillanceStore,
    open_neighboring_force_store,
    open_response_team_roster_store,
    open_response_team_surveillance_store,
)

# Domain-neutral alias: the store is shared camera/drone persistence, not a
# response_team-only type. Keep the original name so existing imports stay valid.
open_surveillance_store = open_response_team_surveillance_store
from persistence.apparatus_store import (
    ApparatusStore,
    ApparatusStoreError,
    open_apparatus_store,
)
from persistence.incident_responder_store import (
    IncidentResponderStore,
    open_incident_responder_store,
)

sqlite = sqlite_store
sqlite_backend = sqlite_store
sys.modules[f"{__name__}.sqlite"] = sqlite_store
sys.modules[f"{__name__}.sqlite_backend"] = sqlite_store

__all__ = [
    "EventSearchCriteria",
    "NotFoundError",
    "PersistenceError",
    "PersistenceInterface",
    "open_persistence",
    "AttendanceCycle",
    "TeamStatusPersistenceInterface",
    "TeamStatusPersistenceError",
    "open_team_status_persistence",
    "CameraInfo",
    "DroneInfo",
    "DroneMission",
    "SurveillancePersistenceError",
    "SurveillancePersistenceInterface",
    "open_surveillance_persistence",
    "NeighboringForceStore",
    "NeighboringForceStoreError",
    "ResponseTeamRosterStore",
    "ResponseTeamSurveillanceStore",
    "open_neighboring_force_store",
    "open_response_team_roster_store",
    "open_response_team_surveillance_store",
    "open_surveillance_store",
    "ApparatusStore",
    "ApparatusStoreError",
    "open_apparatus_store",
    "IncidentResponderStore",
    "open_incident_responder_store",
]
