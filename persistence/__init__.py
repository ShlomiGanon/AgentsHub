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
from persistence.operational_scope import (
    OperationalScope,
    current_operational_scope,
    operational_scope_context,
    resolve_operational_scope,
    scoped_conversation_id,
    scope_from_event,
    scope_from_simulation_context,
)
from persistence.operational_unit_store import (
    FIRE_STATION,
    RESPONSE_TEAM,
    ROLE_CATALOGUE,
    LiveOperationalContext,
    OperationalUnit,
    OperationalUnitError,
    open_operational_unit_persistence,
    resolve_live_operational_context,
)
from persistence.runtime_context import (
    RuntimeOperationalContext,
    current_runtime_context,
    resolve_runtime_context,
    runtime_context,
)
from persistence.dispatch_store import (
    DispatchPersistenceError,
    SQLiteOperationalDispatchStore,
    open_operational_dispatch_store,
)
from persistence.telegram_simulation_binding_store import (
    TelegramSimulationBindingError,
    SQLiteTelegramSimulationBindingStore,
    open_telegram_simulation_binding_store,
)
from persistence.operational_time import (
    OperationalTimeError,
    current_operational_time,
    operational_now,
    operational_time_context,
    operational_time_of_event,
    operational_timestamp_of_event,
    parse_operational_timestamp,
    runtime_now,
)

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
    "OperationalScope",
    "current_operational_scope",
    "operational_scope_context",
    "resolve_operational_scope",
    "scoped_conversation_id",
    "scope_from_event",
    "scope_from_simulation_context",
    "OperationalTimeError",
    "current_operational_time",
    "operational_now",
    "operational_time_context",
    "operational_time_of_event",
    "operational_timestamp_of_event",
    "parse_operational_timestamp",
    "runtime_now",
    "FIRE_STATION",
    "RESPONSE_TEAM",
    "ROLE_CATALOGUE",
    "LiveOperationalContext",
    "OperationalUnit",
    "OperationalUnitError",
    "open_operational_unit_persistence",
    "resolve_live_operational_context",
    "RuntimeOperationalContext",
    "current_runtime_context",
    "resolve_runtime_context",
    "runtime_context",
    "DispatchPersistenceError",
    "SQLiteOperationalDispatchStore",
    "open_operational_dispatch_store",
    "TelegramSimulationBindingError",
    "SQLiteTelegramSimulationBindingStore",
    "open_telegram_simulation_binding_store",
]
