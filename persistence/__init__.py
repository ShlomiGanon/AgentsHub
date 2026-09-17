"""Public persistence facade."""

import sys

from persistence import contracts
from persistence.contracts import (
    ConversationEventLink,
    EventFinalization,
    EventSearchCriteria,
    NotFoundError,
    PersistenceError,
    PersistenceInterface,
    open_persistence,
)

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
    SeedReconciliationResult,
    SurveillancePersistenceError,
    SurveillancePersistenceInterface,
    open_surveillance_persistence,
)
from persistence.runtime_cleanup import CleanupReport, clean_unified_test_runtime

sqlite = sqlite_store
sqlite_backend = sqlite_store
sys.modules[f"{__name__}.sqlite"] = sqlite_store
sys.modules[f"{__name__}.sqlite_backend"] = sqlite_store

__all__ = [
    "EventSearchCriteria",
    "ConversationEventLink",
    "EventFinalization",
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
    "SeedReconciliationResult",
    "SurveillancePersistenceError",
    "SurveillancePersistenceInterface",
    "open_surveillance_persistence",
    "CleanupReport",
    "clean_unified_test_runtime",
]
