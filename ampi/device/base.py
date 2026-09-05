"""The AgentMPI Abstract Device Interface (ADI).

MPICH's portability comes from a narrow waist.  Everything above the Abstract
Device Interface is portable MPI semantics; everything below is a transport ---
ch3, ch4, Nemesis, UCX, OFI.  Open MPI makes the same move with its MCA
frameworks.  The waist is what let a dozen vendors ship MPI-1 within two years of
the standard, and it is what lets the semantics be argued about independently of
how bytes actually move.

AgentMPI copies the structure exactly, and the waist is deliberately narrow:

  1. ``append``  durable, totally ordered insertion of a record into a stream
  2. ``match``   atomic first-fit claim of a record satisfying a predicate
  3. ``scan``    read-only predicate query over a stream
  4. ``cas``     compare-and-swap on a versioned cell (the RMA substrate)
  5. ``lease``   time-bounded exclusive or shared ownership of a named cell
  6. ``clock``   a monotone timestamp shared by every rank

Everything else --- communicators, matching rules, collective schedules, windows,
the context ledger, revoke/shrink/agree --- is built above these six and is
therefore device independent.  That claim is not a hope: ``conformance/`` runs one
suite against every registered device, and a device that passes it can carry any
conforming harness.  Three devices ship here (SQLite, filesystem journal, memory);
a distributed deployment would add Redis or a durable log without changing a line
above this file.

Why these six and not fewer.  ``append`` and ``scan`` alone would suffice for a
single-writer system, but agents are concurrent and a receive must not be handed
to two ranks, so first-fit claiming has to be atomic at the device --- hence
``match``.  ``cas`` cannot be built from ``append`` without a consensus round.
``lease`` cannot be built from ``cas`` without a clock that survives the holder's
death.  And ``clock`` must come from the device because ranks on different hosts
do not share one.

Why not more.  Anything else we were tempted to add turned out to be expressible:
window history is a ``scan`` over versions, the failure detector is a ``scan``
over leases, collective membership is ``append`` plus ``scan``, and the deadlock
detector is a ``scan`` over blocked receives.
"""

from __future__ import annotations

import abc
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Device",
    "Cell",
    "Lease",
    "Predicate",
    "In",
    "NotIn",
    "Lt",
    "Le",
    "Gt",
    "Ge",
    "Ne",
    "IsNull",
    "NotNull",
    "matches",
    "STREAMS",
    "register_device",
    "get_device",
    "available_devices",
    "open_device",
]


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------
# A predicate is a mapping from field name to either a literal (equality) or one
# of the comparator objects below.  Keeping the language this small is what makes
# a new device cheap to write: a device may compile predicates to its query
# language, or fall back on :func:`matches` in Python, and either is conforming.


@dataclass(frozen=True)
class In:
    values: tuple[Any, ...]

    def __init__(self, values: Sequence[Any]) -> None:
        object.__setattr__(self, "values", tuple(values))


@dataclass(frozen=True)
class NotIn:
    values: tuple[Any, ...]

    def __init__(self, values: Sequence[Any]) -> None:
        object.__setattr__(self, "values", tuple(values))


@dataclass(frozen=True)
class Lt:
    value: Any


@dataclass(frozen=True)
class Le:
    value: Any


@dataclass(frozen=True)
class Gt:
    value: Any


@dataclass(frozen=True)
class Ge:
    value: Any


@dataclass(frozen=True)
class Ne:
    value: Any


@dataclass(frozen=True)
class IsNull:
    pass


@dataclass(frozen=True)
class NotNull:
    pass


Predicate = dict[str, Any]


def matches(record: dict[str, Any], predicate: Predicate) -> bool:
    """Evaluate ``predicate`` against ``record`` in Python.

    Devices that cannot compile the whole predicate language may use this as a
    post-filter; the semantics defined here are normative.
    """
    for key, want in predicate.items():
        have = record.get(key)
        if isinstance(want, In):
            if have not in want.values:
                return False
        elif isinstance(want, NotIn):
            if have in want.values:
                return False
        elif isinstance(want, Ne):
            if have == want.value:
                return False
        elif isinstance(want, IsNull):
            if have is not None:
                return False
        elif isinstance(want, NotNull):
            if have is None:
                return False
        elif isinstance(want, (Lt, Le, Gt, Ge)):
            if have is None:
                return False
            try:
                if isinstance(want, Lt) and not have < want.value:
                    return False
                if isinstance(want, Le) and not have <= want.value:
                    return False
                if isinstance(want, Gt) and not have > want.value:
                    return False
                if isinstance(want, Ge) and not have >= want.value:
                    return False
            except TypeError:
                return False
        elif have != want:
            return False
    return True


# --------------------------------------------------------------------------
# Records returned by the device
# --------------------------------------------------------------------------


@dataclass
class Cell:
    """One version of one window cell."""

    space: str
    key: str
    version: int
    value: Any
    writer: int
    epoch: int
    ts: float
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "space": self.space,
            "key": self.key,
            "version": self.version,
            "value": self.value,
            "writer": self.writer,
            "epoch": self.epoch,
            "ts": self.ts,
            "meta": self.meta,
        }


@dataclass
class Lease:
    """A time-bounded claim on a named cell, carrying a monotone fencing token.

    The token is the point.  A lease alone is not enough: an executor cannot know
    that its lease expired while it was mid-step, so between expiry and its next
    call there is a window in which two executors believe they hold the lock.  A
    monotone token checked on every write closes that window --- the standard
    fencing argument for distributed locks, applied to an executor that can wander
    off in a way an MPI process cannot.
    """

    lock_id: str
    space: str
    key: str
    holder: int
    mode: str  # "exclusive" | "shared"
    token: int
    expires_at: float
    acquired_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_id": self.lock_id,
            "space": self.space,
            "key": self.key,
            "holder": self.holder,
            "mode": self.mode,
            "token": self.token,
            "expires_at": self.expires_at,
            "acquired_at": self.acquired_at,
        }


# --------------------------------------------------------------------------
# Stream schema
# --------------------------------------------------------------------------
# Each stream declares the fields a device must be able to filter on efficiently.
# Everything else in a record travels in an opaque body.  Declaring the index set
# here rather than per device is what keeps two devices agreeing about what a
# "first-fit match" means.

STREAMS: dict[str, tuple[str, ...]] = {
    # Rank registry.  One record per rank per epoch.
    "rank": ("rank", "epoch", "state", "run"),
    # Messages.  ``state`` is "posted" | "claimed" | "cancelled".
    "msg": ("comm", "src", "dst", "tag", "state", "run", "epoch", "handle"),
    # Posted receives, so that a receive survives its poster's replacement.
    "recvq": ("comm", "dst", "src_want", "tag_want", "state", "run", "reqid"),
    # Collective participation.  One record per (label, rank).
    "coll": ("comm", "label", "rank", "state", "run", "gen"),
    # Communicator registry.
    "comm": ("name", "gen", "state", "run"),
    # Failure notices.
    "fail": ("rank", "kind", "state", "run"),
    # Agent-operator schedule steps.
    "opstep": ("label", "comm", "step", "state", "assignee", "run"),
    # Interface declarations (S12).
    "iface": ("provider", "name", "state", "run"),
    # The event trace.
    "event": ("rank", "kind", "comm", "run"),
    # Broker invocations, for the pull-queue executor.
    "task": ("rank", "state", "campaign", "run"),
}


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------


class Device(abc.ABC):
    """A conforming AgentMPI device.

    Implementations must be safe under concurrent access from independent OS
    processes, because that is exactly how agent ranks arrive: each executor turn
    is a fresh process invoking the binding.
    """

    #: Short stable name, used in traces and by ``AMPI_DEVICE``.
    name: str = "abstract"
    #: Whether the device survives process exit.  ``memory`` does not.
    durable: bool = True
    #: How often a blocked rank should renew its lease through this device, in
    #: seconds.  A write on a shared filesystem costs microseconds; on a device
    #: whose every write is a network round trip the same renewal rate turns a
    #: barrier into a push storm, so the device says how often it can afford it.
    touch_interval_s: float = 5.0

    # -- lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    def initialize(self) -> None:
        """Create backing state if absent.  Idempotent and safe under races."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release process-local resources.  Must not destroy durable state."""

    @abc.abstractmethod
    def wipe(self) -> None:
        """Destroy all state.  Used by ``ampi init --force`` and by tests."""

    # -- 1. append ---------------------------------------------------------
    @abc.abstractmethod
    def append(self, stream: str, record: dict[str, Any]) -> int:
        """Durably append ``record``; return its monotone sequence number.

        Sequence numbers are unique and increasing *within a stream*, which is
        what the non-overtaking rule (S6.1) is defined against.
        """

    def append_nowait(self, stream: str, record: dict[str, Any]) -> None:
        """Append without waiting for durability: for trace events (spec S13).

        A device MAY acknowledge before the record lands, provided every other
        operation's acknowledgement still implies durability and the record
        lands before any later synchronous write from the same client.  The
        default is the durable append.
        """
        self.append(stream, record)

    # -- 2. match ----------------------------------------------------------
    @abc.abstractmethod
    def match(
        self,
        stream: str,
        predicate: Predicate,
        update: dict[str, Any],
        *,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        """Atomically claim the first record satisfying ``predicate``.

        The claim is expressed by applying ``update`` to the record before it is
        returned.  Atomicity here is the guarantee the whole matching chapter
        rests on: two ranks posting ``AMPI_ANY_SOURCE`` receives can never be
        handed the same message.  That is precisely the duplicated-work failure
        mode ad-hoc agent harnesses hit, and it cannot be fixed above the device.
        """

    # -- 3. scan -----------------------------------------------------------
    @abc.abstractmethod
    def scan(
        self,
        stream: str,
        predicate: Predicate,
        *,
        order_by: str = "seq",
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return records satisfying ``predicate``, without claiming them."""

    @abc.abstractmethod
    def update(self, stream: str, seq: int, fields: dict[str, Any]) -> bool:
        """Patch one record in place.  Returns False if it does not exist."""

    # -- 4. cas ------------------------------------------------------------
    @abc.abstractmethod
    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        """Read a cell, optionally at a historical version."""

    @abc.abstractmethod
    def cas(
        self,
        space: str,
        key: str,
        expect_version: int | None,
        value: Any,
        *,
        writer: int,
        epoch: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, Cell]:
        """Compare-and-swap a cell.

        ``expect_version`` of ``None`` means an unconditional write; ``0`` means
        the cell must not yet exist.  Returns ``(ok, cell)`` where ``cell`` is the
        new value on success and the current value on failure --- returning the
        loser's view is what lets a caller retry without a second read.
        """

    @abc.abstractmethod
    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        """Enumerate current cells.

        Returned cells carry ``meta`` but MAY carry ``value=None``; the caller is
        expected to have asked for an enumeration precisely because it cannot
        afford the bodies.  This is what makes a window usable by an executor with
        a bounded context: it can see *what exists* for a few tokens per key and
        then spend its budget deliberately.
        """

    @abc.abstractmethod
    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        """Every version of a cell, newest first, with writer attribution."""

    # -- 5. lease ----------------------------------------------------------
    @abc.abstractmethod
    def lease(
        self,
        space: str,
        key: str,
        *,
        holder: int,
        mode: str = "exclusive",
        ttl: float,
    ) -> Lease | None:
        """Acquire a leased lock, reclaiming an expired one.  ``None`` if busy."""

    @abc.abstractmethod
    def release(self, lock_id: str, holder: int) -> bool:
        """Release a lock.  Returns False if the caller no longer holds it."""

    @abc.abstractmethod
    def leases(self, space: str = "", *, include_expired: bool = False) -> list[Lease]:
        """Enumerate locks, for diagnostics and for reclaiming after a death."""

    # -- 6. clock ----------------------------------------------------------
    @abc.abstractmethod
    def clock(self) -> float:
        """A timestamp shared by all ranks, in seconds.

        It need not be wall-clock accurate, but it MUST be monotone as observed by
        any single rank and MUST be comparable across ranks, because leases and
        deadlines are compared across ranks.
        """

    # -- atomicity envelope ------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several operations atomically.

        Devices that cannot offer this may inherit the default, which provides no
        isolation.  Nothing in the portable layer *requires* multi-operation
        atomicity --- every invariant is maintained by a single ``match`` or
        ``cas`` --- but the SQLite device offers it and the collective engine uses
        it to make traces easier to read.
        """
        yield

    # -- introspection -----------------------------------------------------
    def stats(self) -> dict[str, Any]:
        """Device-specific counters, surfaced by ``ampi doctor``."""
        return {"device": self.name, "durable": self.durable}


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

_REGISTRY: dict[str, type[Device]] = {}


def register_device(cls: type[Device]) -> type[Device]:
    """Class decorator registering a device under its ``name``."""
    _REGISTRY[cls.name] = cls
    return cls


def get_device(name: str) -> type[Device]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown device {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def available_devices() -> list[str]:
    return sorted(_REGISTRY)


def open_device(name: str, root: str, **kw: Any) -> Device:
    """Construct and initialise a device rooted at ``root``."""
    dev = get_device(name)(root, **kw)  # type: ignore[call-arg]
    dev.initialize()
    return dev
