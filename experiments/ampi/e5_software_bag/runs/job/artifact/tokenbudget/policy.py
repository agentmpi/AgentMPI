"""Budget objects that decide how much more a caller may spend.

Skeleton placeholder: the owning implementer replaces this whole file.
"""


class BudgetExceeded(Exception):
    """Raised when a request cannot be admitted within a budget."""


class Budget:
    """A token allowance with an optional reserved headroom."""

    def __init__(self, limit: int, reserved: int = 0) -> None:
        raise NotImplementedError("policy.Budget is not implemented yet")

    def remaining(self, used: int) -> int:
        """Return the tokens still available after ``used`` have been spent."""
        raise NotImplementedError("policy.Budget.remaining is not implemented yet")

    def admits(self, used: int, incoming: int) -> bool:
        """Return whether ``incoming`` tokens still fit after ``used``."""
        raise NotImplementedError("policy.Budget.admits is not implemented yet")
