"""Protocol data types shared by AgentMPI runtimes and transports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ANY_SOURCE = -1
ANY_TAG = "\x00agentmpi:any-tag"


class AgentState(StrEnum):
    JOINING = "joining"
    ACTIVE = "active"
    SUSPECT = "suspect"
    FAILED = "failed"
    FINALIZED = "finalized"


class DeliveryMode(StrEnum):
    STANDARD = "standard"
    SYNCHRONOUS = "synchronous"
    BUFFERED = "buffered"
    READY = "ready"


class MessageState(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    ACKED = "acked"
    CANCELLED = "cancelled"


class RequestState(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CollectiveOp(StrEnum):
    BARRIER = "barrier"
    BCAST = "bcast"
    SCATTER = "scatter"
    GATHER = "gather"
    ALLGATHER = "allgather"
    REDUCE = "reduce"
    ALLREDUCE = "allreduce"
    AGREE = "agree"


class ReduceOp(StrEnum):
    SUM = "sum"
    PRODUCT = "product"
    MIN = "min"
    MAX = "max"
    CONCAT = "concat"
    MERGE = "merge"
    SET_UNION = "set_union"
    ALL = "all"
    ANY = "any"


@dataclass(frozen=True)
class Status:
    source: int
    tag: str
    count: int
    message_id: str
    sequence: int
    artifact_ref: str | None = None
    payload_tokens: int = 0


@dataclass(frozen=True)
class Received:
    payload: Any
    status: Status


@dataclass(frozen=True)
class Communicator:
    id: str
    session_id: str
    generation: int
    members: tuple[int, ...]
    name: str
    revoked: bool = False

    def rank(self, world_rank: int) -> int:
        return self.members.index(world_rank)

    def world_rank(self, local_rank: int) -> int:
        if local_rank < 0 or local_rank >= self.size:
            raise ProtocolViolation(
                f"local rank {local_rank} is outside communicator size {self.size}"
            )
        return self.members[local_rank]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class AgentInfo:
    rank: int
    state: AgentState
    heartbeat_at: float
    lease_until: float
    context_budget: int
    context_used: int
    incarnation: int


class AgentMPIError(RuntimeError):
    """Base class for protocol errors."""


class Timeout(AgentMPIError):
    pass


class WouldBlock(AgentMPIError):
    pass


class CommunicatorRevoked(AgentMPIError):
    pass


class ProcessFailed(AgentMPIError):
    pass


class ResourceExhausted(AgentMPIError):
    pass


class ProtocolViolation(AgentMPIError):
    pass


class LockUnavailable(AgentMPIError):
    pass
