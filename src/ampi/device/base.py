"""The AgentMPI Abstract Device Interface (ADI).

MPICH's portability comes from a narrow waist: everything above the Abstract
Device Interface is portable MPI semantics, everything below is a transport
(ch3/ch4, Nemesis, UCX, OFI).  AgentMPI copies that structure exactly, and for
the same reason: we want the semantics of the protocol to be arguable and
testable independently of how bytes actually move between agents.

A conforming device must provide six capabilities and nothing more:

  1. ``append``   durable, totally-ordered insertion of a record into a stream
  2. ``match``    first-fit selection of a posted message against a receive
                  predicate, atomic with respect to concurrent matchers
  3. ``cas``      atomic compare-and-swap on a named cell (the RMA substrate)
  4. ``lease``    time-bounded exclusive/shared ownership of a named cell
  5. ``scan``     predicate query over records (used by progress and by the
                  deadlock detector)
  6. ``clock``    a monotone timestamp shared by all ranks

Everything else in AgentMPI --- communicators, collectives, windows, failure
mitigation --- is built on top of these six and is therefore device
independent.  The reference implementation ships a single-file SQLite device;
a distributed deployment would swap in Redis, FoundationDB, or a durable log
without changing a line above this interface.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable
from typing import Any


class Device(abc.ABC):
    """Abstract device interface."""

    name: str = "abstract"

    # -- lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    def initialize(self) -> None:
        """Create backing state if absent.  Must be idempotent and racy-safe."""

    @abc.abstractmethod
    def close(self) -> None: ...

    # -- the six capabilities ---------------------------------------------
    @abc.abstractmethod
    def append(self, stream: str, record: dict[str, Any]) -> int | str:
        """Durably append ``record``; return its identity."""

    @abc.abstractmethod
    def match(
        self,
        stream: str,
        predicate: dict[str, Any],
        claimant: str,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        """Atomically claim the first record satisfying ``predicate``.

        Atomicity here is what gives AgentMPI its message-matching guarantee:
        two ranks posting ``AMPI_ANY_SOURCE`` receives can never be handed the
        same message, which is precisely the duplicated-work failure mode that
        ad-hoc agent harnesses hit.
        """

    @abc.abstractmethod
    def cas(
        self,
        cell: str,
        key: str,
        expected_version: int | None,
        value: Any,
        actor: int,
    ) -> tuple[bool, int, Any]:
        """Compare-and-swap on a window cell; returns (ok, version, current)."""

    @abc.abstractmethod
    def lease(
        self,
        cell: str,
        key: str,
        holder: int,
        mode: str,
        ttl: float,
    ) -> str | None:
        """Acquire a time-bounded lock; return a lock id or ``None``."""

    @abc.abstractmethod
    def release(self, lock_id: str, holder: int) -> bool: ...

    @abc.abstractmethod
    def scan(self, stream: str, predicate: dict[str, Any]) -> Iterable[dict[str, Any]]: ...

    @abc.abstractmethod
    def clock(self) -> float: ...
