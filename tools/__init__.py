"""Public observability facade and executable support package."""

import importlib
import importlib.abc
import importlib.util
import sys

from tools import observability
from tools.observability import (
    configure_logging,
    get_current_stage,
    get_trace_id,
    log_ai_interaction,
    new_trace_id,
    set_trace_id,
    stage_context,
    trace_context,
    verbose_logging_enabled,
)

logging_config = observability
tracing = observability
sys.modules[f"{__name__}.logging_config"] = observability
sys.modules[f"{__name__}.tracing"] = observability

class _TerminalAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    aliases = {f"{__name__}.terminal", f"{__name__}._terminal_client_shared"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.aliases:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        module = importlib.import_module("tools.terminal_support")
        for alias in self.aliases:
            sys.modules[alias] = module
        return module

    def exec_module(self, module):
        return None


if not any(isinstance(finder, _TerminalAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _TerminalAliasFinder())

__all__ = [
    "configure_logging",
    "get_current_stage",
    "get_trace_id",
    "log_ai_interaction",
    "new_trace_id",
    "set_trace_id",
    "stage_context",
    "trace_context",
    "verbose_logging_enabled",
]
