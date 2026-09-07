"""Public protocol contracts, editing, loading, and execution facade."""

import sys

from protocols import contracts
from protocols.contracts import (
    CriticalityLevel,
    EVENT_DATA_FIELDS,
    Protocol,
    ProtocolEditError,
    ProtocolRunResult,
    Step,
    StepOutcome,
)

model = contracts
sys.modules[f"{__name__}.model"] = contracts

from protocols import executor, repository
from protocols.executor import execute_step_with_retry, execute_steps
from protocols.repository import (
    EDIT_SUCCESS_MESSAGE,
    ProtocolSet,
    add_protocol,
    load_protocols,
    read_protocols,
    remove_protocol,
    replace_protocol,
)

service = repository
loader = repository
editor = repository
sys.modules[f"{__name__}.service"] = repository
sys.modules[f"{__name__}.loader"] = repository
sys.modules[f"{__name__}.editor"] = repository

__all__ = [
    "CriticalityLevel",
    "EVENT_DATA_FIELDS",
    "EDIT_SUCCESS_MESSAGE",
    "Protocol",
    "ProtocolEditError",
    "ProtocolRunResult",
    "ProtocolSet",
    "Step",
    "StepOutcome",
    "add_protocol",
    "execute_step_with_retry",
    "execute_steps",
    "load_protocols",
    "read_protocols",
    "remove_protocol",
    "replace_protocol",
]
