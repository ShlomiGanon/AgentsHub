"""Interface-level errors (work_plan.md §2.7).

Nothing above persistence.interface may see a SQLite exception or any
other engine-specific error type — everything is translated into one of
these before it bubbles up.
"""


class PersistenceError(Exception):
    """Base class for every error the persistence interface raises."""


class NotFoundError(PersistenceError):
    """Raised when an operation targets a record that does not exist —
    e.g. resolving a hold that is not held, or deleting an unknown user.
    """
