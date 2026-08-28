"""Public persistence facade."""

import sys

from persistence import contracts
from persistence.contracts import EventSearchCriteria, NotFoundError, PersistenceError, PersistenceInterface, open_persistence

exceptions = contracts
interface = contracts
sys.modules[f"{__name__}.exceptions"] = contracts
sys.modules[f"{__name__}.interface"] = contracts

from persistence import sqlite_store

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
]
