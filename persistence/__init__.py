"""Public persistence facade."""

import sys

from persistence import contracts
from persistence.contracts import NotFoundError, PersistenceError, PersistenceInterface, open_persistence

exceptions = contracts
interface = contracts
sys.modules[f"{__name__}.exceptions"] = contracts
sys.modules[f"{__name__}.interface"] = contracts

from persistence import sqlite

sqlite_backend = sqlite
sys.modules[f"{__name__}.sqlite_backend"] = sqlite

__all__ = [
    "NotFoundError",
    "PersistenceError",
    "PersistenceInterface",
    "open_persistence",
]
