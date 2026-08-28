"""Public protocol contracts, editing, loading, and execution facade."""

import sys

from protocols import contracts
from protocols.contracts import (
    CriticalityLevel,
    Protocol,
    ProtocolEditError,
    ProtocolRunResult,
    Step,
    StepOutcome,
)

model = contracts
sys.modules[f"{__name__}.model"] = contracts

from protocols import executor, service
from protocols.executor import execute_step_with_retry, execute_steps
from protocols.service import (
    EDIT_SUCCESS_MESSAGE,
    ProtocolSet,
    add_protocol,
    load_protocols,
    read_protocols,
    remove_protocol,
    replace_protocol,
)

loader = service
editor = service
sys.modules[f"{__name__}.loader"] = service
sys.modules[f"{__name__}.editor"] = service

__all__ = [
    "CriticalityLevel",
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
