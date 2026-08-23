"""Shared helpers that belong to no single subsystem.

Entry points: `tools.logging_config`, `tools.tracing`. Unlike every other
package, these two modules may be imported directly by anything, including
internal (non-entry-point) modules of other packages — see the
cross-cutting exception in docs/allowed_calls.md.
"""
