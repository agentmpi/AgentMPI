from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Lifecycle(str, Enum):
    """Executor lifecycle. Analog of MPI process state plus agent-specific pauses."""

    UNINITIALIZED = "uninitialized"
    INIT = "init"
    ACTIVE = "active"
    SUSPENDED = "suspended"  # compacting context / waiting on rendezvous
    FAILED = "failed"
    FINALIZED = "finalized"


class Op(str, Enum):
    """Reduction operators. Scalar ops match MPI; the last three are agent-native."""

    SUM = "sum"
    PROD = "prod"
    MIN = "min"
    MAX = "max"
    LAND = "land"
    LOR = "lor"
    BAND = "band"
    BOR = "bor"
    CONCAT = "concat"
    MERGE = "merge"
    SYNTHESIZE = "synthesize"


@dataclass
class Envelope:
    """MPI envelope: (source, tag, communicator) plus agent-specific fields."""

    protocol: str
    kind: str
    src: int
    dst: int
    tag: int
    comm: str
    cid: int
    msg_id: str
    ts: float
    nbytes: int
    eager: bool
    artifact: str | None = None
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class Message:
    envelope: Envelope
    payload: Any

    def to_dict(self) -> dict[str, Any]:
        return {"envelope": self.envelope.to_dict(), "payload": self.payload}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(envelope=Envelope.from_dict(data["envelope"]), payload=data["payload"])


@dataclass
class RankStatus:
    rank: int
    comm: str
    state: str
    last_heartbeat: float
    context_tokens: int
    context_budget: int
    pid: int | None = None
    role: str = "worker"
    note: str = ""
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankStatus:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class CollectiveCall:
    name: str
    cid: int
    root: int | None = None
    op: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
