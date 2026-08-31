"""Budget objects that decide how much more a caller may spend.

A :class:`Budget` is a plain value object: it holds no state about what has
already been spent, and every query takes the current usage as an argument.
Tracking usage is the ledger's job.
"""


class BudgetExceeded(Exception):
    """Raised when a request cannot be admitted within a budget."""


class Budget:
    """A token allowance of ``limit``, of which ``reserved`` is held back."""

    def __init__(self, limit: int, reserved: int = 0) -> None:
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        if reserved < 0:
            raise ValueError(f"reserved must not be negative, got {reserved}")
        if reserved > limit:
            raise ValueError(f"reserved ({reserved}) must not exceed limit ({limit})")
        self.limit = limit
        self.reserved = reserved

    def __repr__(self) -> str:
        return f"Budget(limit={self.limit}, reserved={self.reserved})"

    def remaining(self, used: int) -> int:
        """Return the spendable tokens left once ``used`` have been spent."""
        return max(0, self.limit - self.reserved - used)

    def admits(self, used: int, incoming: int) -> bool:
        """Return whether ``incoming`` tokens still fit on top of ``used``."""
        return incoming <= self.remaining(used)
