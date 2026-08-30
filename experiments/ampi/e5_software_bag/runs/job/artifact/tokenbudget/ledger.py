"""Per-agent accounting of tokens charged and released.

Skeleton placeholder: the owning implementer replaces this whole file.
"""


class Ledger:
    """Track how many tokens each agent currently holds."""

    def __init__(self) -> None:
        raise NotImplementedError("ledger.Ledger is not implemented yet")

    def charge(self, agent: str, tokens: int) -> int:
        """Add ``tokens`` to ``agent`` and return the new balance."""
        raise NotImplementedError("ledger.Ledger.charge is not implemented yet")

    def release(self, agent: str, tokens: int) -> int:
        """Subtract ``tokens`` from ``agent`` and return the new balance."""
        raise NotImplementedError("ledger.Ledger.release is not implemented yet")

    def usage(self) -> dict[str, int]:
        """Return a copy of the per-agent balances."""
        raise NotImplementedError("ledger.Ledger.usage is not implemented yet")

    def total(self) -> int:
        """Return the sum of all agent balances."""
        raise NotImplementedError("ledger.Ledger.total is not implemented yet")
