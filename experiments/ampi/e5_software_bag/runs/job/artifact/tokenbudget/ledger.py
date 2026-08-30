"""Per-agent accounting of tokens charged and released.

The ledger is the mutable half of budgeting: :mod:`tokenbudget.policy`
decides what is allowed, the ledger remembers what actually happened.
Balances are clamped at zero, so releasing more than was charged is a no-op
rather than an error.
"""


class Ledger:
    """Track how many tokens each agent currently holds."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def __repr__(self) -> str:
        return f"Ledger(total={self.total()}, agents={len(self._balances)})"

    def charge(self, agent: str, tokens: int) -> int:
        """Add ``tokens`` to ``agent``'s balance and return the new balance."""
        if tokens < 0:
            raise ValueError(f"cannot charge a negative amount, got {tokens}")
        balance = self._balances.get(agent, 0) + tokens
        self._balances[agent] = balance
        return balance

    def release(self, agent: str, tokens: int) -> int:
        """Subtract ``tokens`` from ``agent``'s balance and return the new balance."""
        if tokens < 0:
            raise ValueError(f"cannot release a negative amount, got {tokens}")
        balance = max(0, self._balances.get(agent, 0) - tokens)
        self._balances[agent] = balance
        return balance

    def usage(self) -> dict[str, int]:
        """Return a copy of the per-agent balances."""
        return dict(self._balances)

    def total(self) -> int:
        """Return the sum of every agent's balance."""
        return sum(self._balances.values())
