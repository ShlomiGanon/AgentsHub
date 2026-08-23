"""The single gateway to storage for every other subsystem.

Entry points: `persistence.interface`, `persistence.exceptions`. Never
import `persistence.sqlite_backend` or `persistence.schema` directly —
those are engine-specific and stay behind the interface.
"""
