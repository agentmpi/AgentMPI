class AgentMPIError(Exception):
    """Base error for the protocol."""


class TimeoutError(AgentMPIError):
    """A blocking call exceeded its timeout."""


class DeadRankError(AgentMPIError):
    def __init__(self, ranks: list[int], message: str | None = None):
        self.ranks = ranks
        super().__init__(message or f"dead ranks: {ranks}")


class RevokedCommunicatorError(AgentMPIError):
    """Communicator was revoked after a failure (ULFM analog)."""


class ContextBudgetExceeded(AgentMPIError):
    """An operation would exceed the rank's context token budget."""


class MatchError(AgentMPIError):
    """No matching message and non-blocking probe returned empty."""


class ProtocolError(AgentMPIError):
    """Malformed on-wire message or inconsistent collective participation."""
