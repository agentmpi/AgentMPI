"""The AgentMPI device interface.

This is the direct analogue of MPICH's Abstract Device Interface: everything
above this line (matching, communicators, collectives, RMA, fault tolerance)
is transport-independent, and everything below it is a swappable device.
MPICH's repeated ADI refactors (ADI-2, CH3, Nemesis, CH4) all argue the same
lesson, which we adopt as a design constraint: the matching engine must not
know how bytes move.

A device provides five capabilities:

* an ordered, at-least-once **message** channel keyed by
  ``(context, source, dest, tag)``;
* a content-addressed **blob** store for the payload plane;
* a **key-value** store with atomic read-modify-write, used for RMA windows,
  heartbeats, group membership and the run's control plane;
* an append-only **journal** used for tracing and for recovery;
* a **clock**.

Note what is *not* required: reliable ordered streams, connection state, or
a broadcast medium.  Those are precisely the assumptions that make MPI
devices hard to port; AgentMPI devices need only a shared, eventually
consistent namespace, which every plausible agent substrate already has.
"""

from __future__ import annotations

import abc
from typing import Any, Iterable, Iterator, Sequence

from ..envelope import Envelope


class Device(abc.ABC):
    """Abstract transport device."""

    name: str = "abstract"

    #: Whether the device can deliver a message to a rank that has not yet
    #: started.  Filesystem devices can; a pure in-process device cannot.
    supports_late_join: bool = False

    # -- messages ----------------------------------------------------------
    @abc.abstractmethod
    def post(self, env: Envelope, payload: str) -> None:
        """Deliver ``env`` to ``env.dest``.  Must be atomic and idempotent."""

    @abc.abstractmethod
    def poll(self, rank: int) -> Iterator[tuple[Envelope, str | None]]:
        """Yield messages newly available for ``rank``.

        A device must never yield the same ``idem`` twice to the same rank
        unless :meth:`requeue` was called for it.
        """

    @abc.abstractmethod
    def requeue(self, rank: int, env: Envelope) -> None:
        """Return a message that was polled but not matched to the queue.

        Used when the matching engine restarts (rank resurrection).
        """

    @abc.abstractmethod
    def ack(self, rank: int, env: Envelope) -> None:
        """Mark ``env`` as ingested by ``rank``.

        This is what makes :class:`~agentmpi.constants.SendMode.SYNCHRONOUS`
        implementable: the sender waits for the acknowledgement, so the send
        completes on *ingestion*, not on delivery.
        """

    @abc.abstractmethod
    def acked(self, env: Envelope) -> bool:
        """Whether ``env`` has been acknowledged by its destination."""

    # -- blobs -------------------------------------------------------------
    @abc.abstractmethod
    def put_blob(self, text: str) -> str:
        """Store ``text``, return its content address."""

    @abc.abstractmethod
    def get_blob(self, address: str) -> str: ...

    @abc.abstractmethod
    def has_blob(self, address: str) -> bool: ...

    # -- key/value ---------------------------------------------------------
    @abc.abstractmethod
    def kv_get(self, key: str) -> str | None: ...

    @abc.abstractmethod
    def kv_put(self, key: str, value: str) -> None: ...

    @abc.abstractmethod
    def kv_cas(self, key: str, expected: str | None, value: str) -> bool:
        """Atomic compare-and-swap.  The primitive under every AgentMPI lock."""

    @abc.abstractmethod
    def kv_list(self, prefix: str) -> Sequence[str]: ...

    @abc.abstractmethod
    def kv_delete(self, key: str) -> None: ...

    def kv_update(self, key: str, fn, *, retries: int = 256) -> str:
        """Atomic read-modify-write built on :meth:`kv_cas`."""
        import time

        for attempt in range(retries):
            current = self.kv_get(key)
            new = fn(current)
            if self.kv_cas(key, current, new):
                return new
            time.sleep(min(0.001 * (2 ** min(attempt, 8)), 0.2))
        from ..errors import RmaConflictError

        raise RmaConflictError("kv_update exhausted retries", key=key)

    # -- journal -----------------------------------------------------------
    @abc.abstractmethod
    def append_journal(self, stream: str, record: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def read_journal(self, stream: str) -> Iterable[dict[str, Any]]: ...

    # -- lifecycle ---------------------------------------------------------
    def barrier_hint(self) -> None:
        """Optional hook letting a device flush caches at synchronisation points."""

    def close(self) -> None:
        """Release device resources."""
