"""Budget policy: deciding whether more tokens may be spent.

A :class:`Budget` is the enforcement half of the library.  It pairs an overall
``limit`` with a ``reserved`` floor that is never handed out -- the caller's own
headroom, typically ``total_budget - sum(planner.plan_fanout(...))`` -- and
answers two questions about a running total of spent tokens: how much is left,
and whether a specific incoming request still fits.

:class:`BudgetExceeded` is provided for callers that want to treat a refusal as
an error rather than a branch; nothing in this module raises it, because
:meth:`Budget.admits` is a predicate and a predicate that raises is useless.
"""

from __future__ import annotations

__all__ = ["Budget", "BudgetExceeded"]


class BudgetExceeded(Exception):
    """Raised by callers when an operation would spend more than the budget."""


class Budget:
    """An overall token limit with a reserved floor held back from spending.

    Args:
        limit: The total tokens the budget covers.  Must not be negative.
        reserved: Tokens held back from spending.  Must not be negative and
            must not exceed ``limit``.

    Raises:
        ValueError: If ``limit`` or ``reserved`` is negative, or if ``reserved``
            is greater than ``limit`` -- a budget that reserves more than it
            holds has no consistent reading.
    """

    __slots__ = ("limit", "reserved")

    def __init__(self, limit: int, reserved: int = 0) -> None:
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        if reserved < 0:
            raise ValueError(f"reserved must not be negative, got {reserved}")
        if reserved > limit:
            raise ValueError(f"reserved ({reserved}) must not exceed limit ({limit})")
        self.limit = limit
        self.reserved = reserved

    def remaining(self, used: int) -> int:
        """Tokens still spendable once ``used`` have been spent.

        Clamped at zero, so an overspend reports "nothing left" rather than a
        negative figure that a caller might accidentally treat as headroom.
        """
        return max(0, self.limit - self.reserved - used)

    def admits(self, used: int, incoming: int) -> bool:
        """Whether ``incoming`` more tokens still fit after ``used``.

        The boundary is inclusive: spending exactly what remains is admitted.
        """
        return incoming <= self.remaining(used)

    def __repr__(self) -> str:
        return f"Budget(limit={self.limit}, reserved={self.reserved})"
