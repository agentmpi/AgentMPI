"""Budget policy: how much of a token allowance is left and what may be admitted.

A :class:`Budget` describes a token allowance with an optional reserve that is
held back for other purposes.  Given the number of tokens already used it can
report how many remain and decide whether an incoming request still fits.
:class:`BudgetExceeded` is provided for callers that prefer to raise instead of
inspecting :meth:`Budget.admits`.
"""

from __future__ import annotations

__all__ = ["Budget", "BudgetExceeded"]


class BudgetExceeded(Exception):
    """Raised when a token request does not fit inside the remaining budget."""


class Budget:
    """A token allowance of ``limit`` tokens, ``reserved`` of which are held back."""

    __slots__ = ("limit", "reserved")

    def __init__(self, limit: int, reserved: int = 0) -> None:
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        if reserved < 0:
            raise ValueError(f"reserved must not be negative, got {reserved}")
        if reserved > limit:
            raise ValueError(
                f"reserved ({reserved}) must not exceed limit ({limit})"
            )
        self.limit: int = limit
        self.reserved: int = reserved

    def remaining(self, used: int) -> int:
        """Return the spendable tokens left once ``used`` and the reserve are taken out."""
        return max(0, self.limit - self.reserved - used)

    def admits(self, used: int, incoming: int) -> bool:
        """Return whether ``incoming`` tokens still fit after ``used`` are spent."""
        return incoming <= self.remaining(used)

    def __repr__(self) -> str:
        return f"Budget(limit={self.limit}, reserved={self.reserved})"
