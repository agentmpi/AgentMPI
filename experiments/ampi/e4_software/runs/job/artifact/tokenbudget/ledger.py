"""Token accounting: what each agent has actually spent.

Where :mod:`tokenbudget.planner` decides what agents *may* spend and
:mod:`tokenbudget.policy` decides what they may spend *next*, a :class:`Ledger`
records what they *did* spend.  Charges accumulate per agent, releases hand
tokens back, and :meth:`Ledger.total` is the figure to feed to
:meth:`tokenbudget.policy.Budget.remaining`.

Balances are floored at zero: an over-release is a bookkeeping slip, and
letting it push a balance negative would silently manufacture budget that the
enforcement side would then hand out.
"""

from __future__ import annotations

__all__ = ["Ledger"]


class Ledger:
    """A per-agent record of tokens spent.

    A fresh ledger is empty: :meth:`usage` returns ``{}`` and :meth:`total`
    returns ``0``.
    """

    __slots__ = ("_usage",)

    def __init__(self) -> None:
        self._usage: dict[str, int] = {}

    def charge(self, agent: str, tokens: int) -> int:
        """Add ``tokens`` to ``agent``'s balance and return the new balance.

        Charging zero is a no-op that still reports the balance.

        Raises:
            ValueError: If ``tokens`` is negative -- a negative charge is a
                release, and conflating the two hides accounting errors.  The
                ledger is left untouched.
        """
        if tokens < 0:
            raise ValueError(f"cannot charge a negative number of tokens: {tokens}")
        balance = self._usage.get(agent, 0) + tokens
        self._usage[agent] = balance
        return balance

    def release(self, agent: str, tokens: int) -> int:
        """Subtract ``tokens`` from ``agent``'s balance and return the new one.

        The balance floors at zero, so releasing more than was ever charged
        settles at zero rather than going negative.  Releasing against an agent
        that was never charged returns ``0`` and adds nothing to the books.

        Raises:
            ValueError: If ``tokens`` is negative.  The ledger is left
                untouched.
        """
        if tokens < 0:
            raise ValueError(f"cannot release a negative number of tokens: {tokens}")
        if agent not in self._usage:
            return 0
        balance = max(0, self._usage[agent] - tokens)
        self._usage[agent] = balance
        return balance

    def usage(self) -> dict[str, int]:
        """A snapshot of every agent's balance.

        A fresh dict on every call, so a caller mutating the result cannot
        corrupt the ledger.
        """
        return dict(self._usage)

    def total(self) -> int:
        """The sum of every agent's balance."""
        return sum(self._usage.values())

    def __repr__(self) -> str:
        return f"Ledger(agents={len(self._usage)}, total={self.total()})"
