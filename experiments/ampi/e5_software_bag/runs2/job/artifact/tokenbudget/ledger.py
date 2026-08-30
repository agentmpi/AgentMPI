"""Bookkeeping of token spend per agent.

The :class:`Ledger` records how many tokens each cooperating agent currently
holds against its budget. Callers ``charge`` tokens when an agent consumes
them and ``release`` tokens when the agent gives them back, and read the
aggregate with :meth:`Ledger.usage` and :meth:`Ledger.total`.
"""

from __future__ import annotations

__all__ = ["Ledger"]


class Ledger:
    """A per-agent tally of charged tokens.

    Balances are always non-negative: releasing more tokens than an agent
    holds clamps its balance at zero rather than going into debt.
    """

    def __init__(self) -> None:
        self._usage: dict[str, int] = {}

    def charge(self, agent: str, tokens: int) -> int:
        """Add ``tokens`` to ``agent``'s balance and return the new balance."""
        tokens = self._check_tokens(tokens)
        balance = self._usage.get(agent, 0) + tokens
        self._usage[agent] = balance
        return balance

    def release(self, agent: str, tokens: int) -> int:
        """Subtract ``tokens`` from ``agent``'s balance, floored at zero.

        Returns the new balance. Releasing for an agent that was never
        charged leaves it at zero.
        """
        tokens = self._check_tokens(tokens)
        balance = max(0, self._usage.get(agent, 0) - tokens)
        self._usage[agent] = balance
        return balance

    def balance(self, agent: str) -> int:
        """Return ``agent``'s current balance, or 0 if it is unknown."""
        return self._usage.get(agent, 0)

    def usage(self) -> dict[str, int]:
        """Return a copy of the per-agent balances."""
        return dict(self._usage)

    def total(self) -> int:
        """Return the sum of every agent's balance."""
        return sum(self._usage.values())

    def __repr__(self) -> str:
        return f"Ledger(total={self.total()}, agents={len(self._usage)})"

    @staticmethod
    def _check_tokens(tokens: int) -> int:
        if isinstance(tokens, bool) or not isinstance(tokens, int):
            raise TypeError(f"tokens must be an int, got {type(tokens).__name__}")
        if tokens < 0:
            raise ValueError(f"tokens must not be negative, got {tokens}")
        return tokens
