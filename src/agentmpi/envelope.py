"""Message envelopes and the payload/context plane split.

MPI's envelope is (source, destination, tag, communicator).  AgentMPI keeps
those four fields verbatim -- they are exactly the matching key -- and adds
the fields that a non-deterministic, context-bounded, priced executor needs:

``seq``
    Per (context, source, destination) sequence number.  This is how the
    matching engine enforces MPI's non-overtaking rule on a transport that
    does not itself preserve order.
``datatype`` / ``contract_ok``
    The declared contract and whether the payload satisfied it at send time.
``tokens``
    The ingest cost of the payload.  The receiver's admission control acts
    on this number *before* the payload enters its context.
``blob``
    A content address.  Payloads above the inline threshold live in the blob
    store and travel by reference: the **payload plane** is cheap and
    unbounded, the **context plane** is expensive and bounded, and AgentMPI
    is explicit about which plane a byte is on.  A rank can hold a reference
    to a gigabyte of artifacts at a cost of a few dozen tokens.
``epoch``
    The communicator epoch the message was sent in; messages from a stale
    epoch are discarded after a shrink.
``idem``
    Idempotency key.  Delivery is at-least-once, so receivers deduplicate.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .constants import Datatype, SendMode

#: Payloads whose rendered form exceeds this many characters are spilled to
#: the blob store and travel as a reference.  This is the AgentMPI analogue
#: of MPI's *eager limit*: below it, ship the data with the envelope; above
#: it, ship a handle and let the receiver pull.  The number is a control
#: variable (``ampi_eager_chars``) and is deliberately generous, because in
#: AgentMPI the expensive resource is the receiver's context, not the wire.
DEFAULT_EAGER_CHARS = 8192


def content_address(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_digest(text: str, n: int = 12) -> str:
    return content_address(text)[:n]


@dataclass
class Envelope:
    """A message envelope plus (possibly) its inline payload."""

    context: str
    source: int
    dest: int
    tag: int
    seq: int
    datatype: str = Datatype.TEXT.value
    mode: str = SendMode.STANDARD.value
    tokens: int = 0
    chars: int = 0
    inline: str | None = None
    blob: str | None = None
    epoch: int = 0
    idem: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts_send: float = field(default_factory=time.time)
    ts_recv: float | None = None
    contract_ok: bool = True
    violations: tuple[str, ...] = ()
    reduced: bool = False
    """True when the runtime digested the payload to satisfy a token bound."""
    origin_turn: int = 0
    """Turn counter of the sending rank; used for causal ordering in traces."""
    provenance: tuple[str, ...] = ()
    """Chain of rank ids the content passed through.  Broadcast trees and
    reductions rewrite content, so provenance is how a harness -- or a
    reviewer of a run -- attributes a claim back to its source."""
    meta: dict[str, Any] = field(default_factory=dict)

    # -- serialisation -----------------------------------------------------
    def to_json(self) -> str:
        d = asdict(self)
        d["violations"] = list(self.violations)
        d["provenance"] = list(self.provenance)
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Envelope":
        d = json.loads(text)
        d["violations"] = tuple(d.get("violations") or ())
        d["provenance"] = tuple(d.get("provenance") or ())
        known = cls.__dataclass_fields__.keys()  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    # -- matching ----------------------------------------------------------
    def matches(self, source: int, tag: int, context: str) -> bool:
        from .constants import ANY_SOURCE, ANY_TAG

        if self.context != context:
            return False
        if source != ANY_SOURCE and self.source != source:
            return False
        if tag != ANY_TAG and self.tag != tag:
            return False
        return True

    @property
    def key(self) -> tuple[str, int, int]:
        """The ordering key: messages sharing it must not overtake."""
        return (self.context, self.source, self.dest)

    def describe(self) -> str:
        where = "inline" if self.blob is None else f"blob:{self.blob[:8]}"
        return (
            f"<msg {self.context}#{self.epoch} {self.source}->{self.dest} "
            f"tag={self.tag} seq={self.seq} {self.datatype} "
            f"{self.tokens}tok {where}>"
        )


@dataclass
class Status:
    """The AgentMPI analogue of ``MPI_Status``."""

    source: int
    tag: int
    context: str
    tokens: int
    datatype: str
    error: int = 0
    seq: int = 0
    reduced: bool = False
    contract_ok: bool = True
    violations: tuple[str, ...] = ()
    wait_time_s: float = 0.0
    """How long the receiver blocked.  Reported because in AgentMPI the
    dominant cost is waiting for a peer's turn to finish, so wait time is
    the primary performance signal rather than a diagnostic."""

    @classmethod
    def from_envelope(cls, env: Envelope, wait_time_s: float = 0.0) -> "Status":
        return cls(
            source=env.source,
            tag=env.tag,
            context=env.context,
            tokens=env.tokens,
            datatype=env.datatype,
            seq=env.seq,
            reduced=env.reduced,
            contract_ok=env.contract_ok,
            violations=env.violations,
            wait_time_s=wait_time_s,
        )
