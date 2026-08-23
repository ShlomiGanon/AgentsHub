"""Bot-specific error types (work_plan.md §8).

`ApiNotImplementedError` is the one error type this mission introduces
that is not really "a bug" — it is the deliberate, explicit marker for
every capability that is real on the bot side but cannot complete because
the API Layer (work_plan.md §7) has not been built yet. Every raise site
names the exact work_plan.md subtask it is blocked on, so grepping for
this class finds the complete, current list of Mission-7 gaps. See
`bot/api_client.py`.
"""


class BotError(Exception):
    """Base class for every error this package raises."""


class BotStartupError(BotError):
    """The bot could not start — a missing/rejected token, or a second
    instance already running for this deployment (work_plan.md §8.1).
    """


class ApiNotImplementedError(BotError, NotImplementedError):
    """The API Layer (work_plan.md §7) does not implement this operation
    yet. Raised by `bot.api_client.UnimplementedApiClient` — the seam a
    real HTTP-backed client will replace once §7 lands (see that module's
    docstring). Deliberately a subclass of `NotImplementedError` too, so
    existing "not implemented" handling still catches it.
    """

    def __init__(self, operation: str, blocked_on: str):
        self.operation = operation
        self.blocked_on = blocked_on
        super().__init__(
            f"'{operation}' is not available: it depends on {blocked_on} "
            f"(work_plan.md §7 — API Layer), which has not been built yet."
        )
