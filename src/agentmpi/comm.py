"""Communicators: group + context, and the point-to-point layer.

The communicator is MPI's best idea.  A communicator pairs a group with an
opaque *communication context*, and the context guarantees that a message
sent on one communicator can never be received on another.  That single
property is what made it possible to write MPI *libraries*: a library that
duplicates the communicator it is handed cannot have its messages stolen by
the application, and the application cannot have its messages stolen by the
library, no matter what tags either of them chooses.  Before contexts, the
standard workaround was for libraries to reserve tag ranges by convention,
which fails silently the moment two libraries disagree.

Multi-agent systems are in the pre-context era today.  The dominant designs
are a shared conversation buffer that every participant reads and writes, or
direct handoffs addressed by agent name.  Both make composition unsafe in
precisely the way MPI's designers identified in 1993: two independently
written agent components that share a conversation will read each other's
messages, and the failure is silent, intermittent, and blamed on the model.
:meth:`Communicator.dup` is the fix, and it costs nothing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    CONTEXT_WORLD,
    PROC_NULL,
    TAG_UB,
    CommSplitType,
    Datatype,
    SendMode,
)
from .datatypes import NULL, TEXT, TypeDescriptor, lookup
from .envelope import DEFAULT_EAGER_CHARS, Envelope, Status
from .errors import (
    AmpiError,
    ArgError,
    CommError,
    ContractError,
    ProcFailedError,
    RankError,
    RevokedError,
    TagError,
    TimeoutError_,
    ERRORS_RETURN,
    Errhandler,
)
from .group import Group, RankSpec
from .tokens import count_tokens, message_tokens
from .trace import Event

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Runtime


@dataclass
class Request:
    """A handle to an incomplete operation (``MPI_Request``)."""

    comm: "Communicator"
    kind: str                       # "send" | "recv" | "coll"
    source: int = ANY_SOURCE
    tag: int = ANY_TAG
    dest: int = -1
    datatype: TypeDescriptor = TEXT
    envelope: Envelope | None = None
    value: Any = None
    status: Status | None = None
    complete: bool = False
    cancelled: bool = False
    persistent: bool = False
    started: bool = False
    created_at: float = field(default_factory=time.time)
    #: For partitioned communication (MPI-4 ``MPI_Psend_init``).
    partitions: list[Any] | None = None
    ready_partitions: set[int] = field(default_factory=set)
    on_complete: Callable[["Request"], None] | None = None

    def test(self) -> tuple[bool, Status | None]:
        if self.complete:
            return True, self.status
        return self.comm._progress_request(self)

    def wait(self, timeout: float | None = None) -> tuple[Any, Status | None]:
        return self.comm._wait_request(self, timeout=timeout)

    def cancel(self) -> None:
        self.cancelled = True
        self.complete = True

    def free(self) -> None:
        self.complete = True


class Communicator:
    """A group plus an isolated communication context."""

    def __init__(
        self,
        runtime: "Runtime",
        context: str,
        group: Group,
        *,
        name: str = "",
        parent: "Communicator | None" = None,
        epoch: int = 0,
        remote_group: Group | None = None,
    ) -> None:
        self.runtime = runtime
        self.context = context
        self.group = group
        self.name = name or context
        self.parent = parent
        self.epoch = epoch
        self.remote_group = remote_group  # non-None => intercommunicator
        self.errhandler: Errhandler = ERRORS_RETURN
        self.attrs: dict[str, Any] = {}
        self.freed = False
        self.revoked = False
        #: Ranks known to have failed, in *this* communicator's numbering.
        self.failed: set[int] = set()
        self.acknowledged: set[int] = set()
        self._send_seq: dict[int, int] = {}
        self._topology: Any = None
        self._coll_counter = 0

    # -- identity ----------------------------------------------------------
    @property
    def rank(self) -> int:
        """``AMPI_Comm_rank`` -- this rank's index *within this communicator*."""
        r = self.group.rank_of(self.runtime.world_rank)
        if r < 0:
            raise CommError("this rank is not a member of the communicator",
                            comm=self.name, world_rank=self.runtime.world_rank)
        return r

    @property
    def size(self) -> int:
        return self.group.size

    @property
    def is_inter(self) -> bool:
        return self.remote_group is not None

    def spec(self, rank: int | None = None) -> RankSpec:
        return self.group.spec(self.rank if rank is None else rank)

    def world(self, rank: int) -> int:
        """Translate a communicator-local rank to the world rank."""
        return self.group.world_rank(rank)

    def __repr__(self) -> str:
        kind = "intercomm" if self.is_inter else "comm"
        return f"<{kind} {self.name} rank={self._safe_rank()}/{self.size} epoch={self.epoch}>"

    def _safe_rank(self) -> int:
        try:
            return self.rank
        except CommError:
            return -1

    # -- communicator management ------------------------------------------
    def dup(self, name: str | None = None) -> "Communicator":
        """``AMPI_Comm_dup`` -- same group, fresh context.

        The operation is collective and, as in MPI, must be called by every
        member in the same order relative to other collectives, because the
        new context id has to be agreed on.  We derive it deterministically
        from the parent context and a per-communicator collective counter,
        which makes it agreement-free: every rank computes the same id
        without an extra round trip.  That is legitimate precisely because
        MPI already requires collectives to be issued in a consistent order,
        and it removes what would otherwise be a full barrier from the most
        common composition primitive.
        """
        self._coll_counter += 1
        ctx = f"{self.context}/dup{self._coll_counter}"
        return self.runtime._register_comm(
            Communicator(
                self.runtime, ctx, self.group,
                name=name or f"{self.name}.dup{self._coll_counter}",
                parent=self, epoch=self.epoch,
            )
        )

    def dup_with_info(self, info: dict[str, Any], name: str | None = None) -> "Communicator":
        comm = self.dup(name)
        comm.attrs.update(info)
        return comm

    def create(self, group: Group, name: str | None = None) -> "Communicator | None":
        """``AMPI_Comm_create`` -- a new communicator over a subgroup."""
        self._coll_counter += 1
        ctx = f"{self.context}/sub{self._coll_counter}"
        if self.runtime.world_rank not in group.members:
            return None
        return self.runtime._register_comm(
            Communicator(self.runtime, ctx, group,
                         name=name or f"{self.name}.sub{self._coll_counter}",
                         parent=self, epoch=self.epoch)
        )

    def split(self, color: int | None, key: int = 0) -> "Communicator | None":
        """``AMPI_Comm_split``.

        Implemented with an allgather of ``(color, key, world_rank)``, exactly
        as MPI implementations do.  We keep the collective rather than
        computing it locally because ``color`` is a runtime value that only
        each rank knows.
        """
        from .collectives import allgather_raw

        payload = {"color": color, "key": key, "world": self.runtime.world_rank}
        gathered = allgather_raw(self, payload)
        self._coll_counter += 1
        if color is None:
            return None
        peers = [g for g in gathered if g.get("color") == color]
        peers.sort(key=lambda g: (g["key"], g["world"]))
        members = tuple(g["world"] for g in peers)
        sub = Group(members, tuple(
            s for s in self.group.specs if s.rank in members
        ) if self.group.specs else ())
        ctx = f"{self.context}/split{self._coll_counter}c{color}"
        return self.runtime._register_comm(
            Communicator(self.runtime, ctx, sub,
                         name=f"{self.name}.split{color}", parent=self, epoch=self.epoch)
        )

    def split_type(
        self, kind: CommSplitType | str, key: int = 0
    ) -> "Communicator | None":
        """``AMPI_Comm_split_type``.

        MPI-3 added ``MPI_COMM_TYPE_SHARED`` so a program could discover which
        ranks share memory and specialise for it.  The agent analogue is
        discovering which ranks share a *coherent resource*: the same model
        (so a hand-off costs no re-grounding), the same provider quota (so
        they contend), or the same context store (so they can exchange
        references rather than content).  Harnesses use this to place the
        chatty parts of a computation inside a cheap-communication island,
        which is the same optimisation as MPI's shared-memory specialisation.
        """
        k = CommSplitType(kind) if isinstance(kind, str) else kind
        attr = self.spec().attribute(k.value)
        colors = sorted({self.group.spec(i).attribute(k.value) for i in range(self.size)})
        return self.split(colors.index(attr), key)

    def free(self) -> None:
        self.freed = True
        self.runtime._unregister_comm(self)

    def set_errhandler(self, handler: Errhandler) -> None:
        self.errhandler = handler

    def set_name(self, name: str) -> None:
        self.name = name

    # -- intercommunicators ------------------------------------------------
    def intercomm_create(
        self, remote: Group, name: str = "intercomm"
    ) -> "Communicator":
        """``AMPI_Intercomm_create`` -- a channel between two disjoint teams.

        The agent reading is a *team boundary*: a reviewer pool and an author
        pool exchange work across the intercommunicator, and each pool keeps
        its own intracommunicator for internal coordination.  Ranks are
        addressed by their index in the *remote* group, which is what makes
        the two teams independently reorganisable.
        """
        ctx = f"inter:{self.context}|{uuid.uuid4().hex[:8]}"
        return self.runtime._register_comm(
            Communicator(self.runtime, ctx, self.group, name=name,
                         parent=self, epoch=self.epoch, remote_group=remote)
        )

    def intercomm_merge(self, high: bool = False) -> "Communicator":
        if not self.is_inter:
            raise CommError("intercomm_merge on an intracommunicator")
        assert self.remote_group is not None
        first, second = (self.remote_group, self.group) if high else (self.group, self.remote_group)
        merged = first.union(second)
        ctx = f"{self.context}/merged"
        return self.runtime._register_comm(
            Communicator(self.runtime, ctx, merged, name=f"{self.name}.merged",
                         parent=self, epoch=self.epoch)
        )

    # ----------------------------------------------------------------------
    # Point to point
    # ----------------------------------------------------------------------
    def _next_seq(self, dest: int) -> int:
        """Next per-(context, source, destination) sequence number.

        Held on the runtime rather than the communicator object so that it
        survives across processes.  That matters because an AgentMPI rank is
        frequently *not* a long-lived process: an agent invokes the ``ampi``
        command once per operation, so the sequence counter that enforces
        non-overtaking has to live in durable state, not in an object.
        """
        key = f"{self.context}|{dest}"
        seq = self.runtime.send_seq.get(key, 0)
        self.runtime.send_seq[key] = seq + 1
        return seq

    def _check_peer(self, rank: int, what: str) -> None:
        if rank == PROC_NULL:
            return
        if not 0 <= rank < self.size:
            raise RankError(f"{what} rank out of range", rank=rank, size=self.size)
        if rank in self.failed:
            raise ProcFailedError(f"{what} rank has failed", failed=(rank,))

    def _check_tag(self, tag: int) -> None:
        # Tags in ``[0, TAG_UB)`` belong to the application; the reserved
        # range above it belongs to the runtime's own collective and control
        # traffic.  Both are valid on the wire, which is why the bound here
        # is the wider one; harnesses are told about ``TAG_UB``.
        if tag != ANY_TAG and not 0 <= tag < (1 << 24):
            raise TagError("tag outside the valid range", tag=tag, tag_ub=TAG_UB)

    def _check_live(self) -> None:
        if self.freed:
            raise CommError("operation on a freed communicator", comm=self.name)
        if self.revoked:
            raise RevokedError("operation on a revoked communicator", comm=self.name)

    def send(
        self,
        value: Any,
        dest: int,
        tag: int = 0,
        datatype: TypeDescriptor | str = TEXT,
        mode: SendMode | str = SendMode.STANDARD,
        *,
        timeout: float | None = None,
        provenance: Sequence[str] = (),
        meta: dict[str, Any] | None = None,
    ) -> Status:
        """``AMPI_Send`` and friends."""
        self._check_live()
        self._check_peer(dest, "destination")
        self._check_tag(tag)
        if dest == PROC_NULL:
            return Status(PROC_NULL, tag, self.context, 0, "null")
        dt = lookup(datatype) if isinstance(datatype, str) else datatype
        md = SendMode(mode) if isinstance(mode, str) else mode

        dt.validate(value)
        value, reduced = dt.fit(value)
        text = dt.render(value)
        tokens = message_tokens(text)

        env = Envelope(
            context=self.context,
            source=self.rank,
            dest=dest,
            tag=tag,
            seq=self._next_seq(dest),
            datatype=dt.name,
            mode=md.value,
            tokens=tokens,
            chars=len(text),
            epoch=self.epoch,
            reduced=reduced,
            origin_turn=self.runtime.turn,
            provenance=tuple(provenance) or (f"r{self.rank}",),
            meta=dict(meta or {}),
        )
        payload = text
        if len(text) > self.runtime.cvars["ampi_eager_chars"] or md is SendMode.BUFFERED:
            env.blob = self.runtime.device.put_blob(text)
            env.inline = None
            payload = ""

        self.runtime.budget.emit(tokens)
        self.runtime.device.post(env, payload)
        self.runtime.profiler.emit(
            Event(kind="send", ts=time.time(), rank=self.runtime.world_rank, op="send",
                  context=self.context, peer=dest, tag=tag, tokens=tokens,
                  bytes_=len(text), seq=env.seq, idem=env.idem,
                  turn=self.runtime.turn,
                  detail={"mode": md.value, "datatype": dt.name, "reduced": reduced})
        )

        if md is SendMode.SYNCHRONOUS:
            self._await_ack(env, timeout=timeout)
        return Status.from_envelope(env)

    def _await_ack(self, env: Envelope, timeout: float | None) -> None:
        """Block until the destination has ingested the message.

        ``AMPI_Ssend`` means "completes when the receiver has read it *into
        its context*".  This is stronger than MPI's synchronous send, which
        completes when the matching receive has *begun*.  The stronger form
        is the useful one for agents: knowing that a peer has been told
        something, rather than that a buffer was drained, is what a handoff
        protocol actually needs.
        """
        deadline = None if timeout is None else time.time() + timeout
        interval = 0.05
        while not self.runtime.device.acked(env):
            if deadline is not None and time.time() > deadline:
                raise TimeoutError_("synchronous send was never ingested",
                                    dest=env.dest, tag=env.tag)
            if env.dest in self.failed:
                raise ProcFailedError("destination failed before ingesting",
                                      failed=(env.dest,))
            self.runtime.check_failures(self)
            time.sleep(interval)
            interval = min(interval * 1.5, 1.0)

    def ssend(self, value: Any, dest: int, tag: int = 0, **kw: Any) -> Status:
        return self.send(value, dest, tag, mode=SendMode.SYNCHRONOUS, **kw)

    def bsend(self, value: Any, dest: int, tag: int = 0, **kw: Any) -> Status:
        return self.send(value, dest, tag, mode=SendMode.BUFFERED, **kw)

    def isend(self, value: Any, dest: int, tag: int = 0, **kw: Any) -> Request:
        """``AMPI_Isend``.

        The device accepts the payload immediately, so a nonblocking send is
        genuinely nonblocking; the request exists so that a harness can use
        the same completion vocabulary for sends and receives, and so that
        synchronous-mode sends have somewhere to report ingestion.
        """
        mode = kw.pop("mode", SendMode.STANDARD)
        status = self.send(value, dest, tag, mode=SendMode.STANDARD, **kw)
        req = Request(self, "send", dest=dest, tag=tag, status=status, complete=True)
        if SendMode(mode) is SendMode.SYNCHRONOUS:
            req.complete = False
        return req

    def recv(
        self,
        source: int = ANY_SOURCE,
        tag: int = ANY_TAG,
        datatype: TypeDescriptor | str | None = None,
        *,
        timeout: float | None = None,
        admit: bool = True,
    ) -> tuple[Any, Status]:
        """``AMPI_Recv``.

        Beyond MPI's receive this performs two extra steps, both forced by
        the agent setting: the payload is charged against this rank's context
        budget before it is materialised (admission control), and it is
        checked against the declared contract on arrival.
        """
        self._check_live()
        if source == PROC_NULL:
            return None, Status(PROC_NULL, tag, self.context, 0, "null")
        if source != ANY_SOURCE:
            self._check_peer(source, "source")
        self._check_tag(tag)
        t0 = time.time()
        engine = self.runtime.matching
        env = engine.wait_match(
            source, tag, self.context,
            timeout=timeout,
            on_poll=lambda _iv: self.runtime.check_failures(self),
        )
        if env is None:
            raise TimeoutError_("receive timed out", source=source, tag=tag,
                                comm=self.name, waited_s=round(time.time() - t0, 2))
        return self._deliver(env, datatype, t0, admit=admit)

    def _deliver(
        self,
        env: Envelope,
        datatype: TypeDescriptor | str | None,
        t0: float,
        *,
        admit: bool = True,
    ) -> tuple[Any, Status]:
        dt = (
            lookup(datatype) if isinstance(datatype, str)
            else (datatype if datatype is not None else self.runtime.types_by_name(env.datatype))
        )
        # Admission control runs *before* the payload is materialised, and may
        # rewrite the envelope to a digested form.  Reading the text first
        # would hand the receiver the oversized version the runtime just
        # decided it could not afford.
        if admit:
            self.runtime.admit(env.tokens, datatype=dt, env=env)

        text = env.inline
        if text is None and env.blob is not None:
            text = self.runtime.device.get_blob(env.blob)
        text = text or ""

        value = dt.parse(text) if dt.base is not Datatype.TEXT else text
        violations = dt.check(value)
        env.ts_recv = time.time()
        self.runtime.device.ack(self.runtime.world_rank, env)
        self.runtime.profiler.emit(
            Event(kind="recv", ts=env.ts_recv, rank=self.runtime.world_rank, op="recv",
                  context=self.context, peer=env.source, tag=env.tag,
                  tokens=env.tokens, bytes_=len(text), seq=env.seq, idem=env.idem,
                  turn=self.runtime.turn,
                  detail={"datatype": dt.name, "wait_s": round(env.ts_recv - t0, 3),
                          "violations": list(violations)})
        )
        status = Status.from_envelope(env, wait_time_s=env.ts_recv - t0)
        status.contract_ok = not violations
        status.violations = violations
        if violations and self.runtime.cvars["ampi_strict_contracts"]:
            raise ContractError("received payload violates its contract",
                                violations=violations, source=env.source, tag=env.tag)
        return value, status

    def irecv(
        self, source: int = ANY_SOURCE, tag: int = ANY_TAG,
        datatype: TypeDescriptor | str | None = None,
    ) -> Request:
        return Request(self, "recv", source=source, tag=tag,
                       datatype=lookup(datatype) if isinstance(datatype, str) else (datatype or TEXT))

    def sendrecv(
        self,
        value: Any,
        dest: int,
        recv_source: int = ANY_SOURCE,
        sendtag: int = 0,
        recvtag: int = ANY_TAG,
        datatype: TypeDescriptor | str = TEXT,
        *,
        timeout: float | None = None,
    ) -> tuple[Any, Status]:
        """``AMPI_Sendrecv`` -- the deadlock-free exchange primitive.

        Every collective algorithm in :mod:`agentmpi.collectives` is written
        on top of this, for the same reason MPI programs are: a hand-rolled
        send-then-receive pair between two ranks deadlocks whenever the
        transport declines to buffer, and the equivalent hazard in an agent
        harness -- two agents each waiting for the other to speak first -- is
        both common and hard to diagnose.
        """
        self.send(value, dest, sendtag, datatype)
        return self.recv(recv_source, recvtag, datatype, timeout=timeout)

    # -- probing -----------------------------------------------------------
    def iprobe(self, source: int = ANY_SOURCE, tag: int = ANY_TAG) -> Status | None:
        self.runtime.matching.progress()
        env = self.runtime.matching.peek(source, tag, self.context)
        return None if env is None else Status.from_envelope(env)

    def probe(
        self, source: int = ANY_SOURCE, tag: int = ANY_TAG, timeout: float | None = None
    ) -> Status:
        deadline = None if timeout is None else time.time() + timeout
        while True:
            st = self.iprobe(source, tag)
            if st is not None:
                return st
            if deadline is not None and time.time() > deadline:
                raise TimeoutError_("probe timed out", source=source, tag=tag)
            self.runtime.check_failures(self)
            time.sleep(0.05)

    def mprobe(
        self, source: int = ANY_SOURCE, tag: int = ANY_TAG, timeout: float | None = None
    ) -> tuple[Envelope, Status]:
        """``AMPI_Mprobe`` -- probe *and remove*, so a concurrent receive cannot
        steal the message between the probe and the receive."""
        env = self.runtime.matching.wait_match(source, tag, self.context, timeout=timeout)
        if env is None:
            raise TimeoutError_("mprobe timed out", source=source, tag=tag)
        return env, Status.from_envelope(env)

    def mrecv(
        self, message: Envelope, datatype: TypeDescriptor | str | None = None
    ) -> tuple[Any, Status]:
        return self._deliver(message, datatype, time.time())

    # -- requests ----------------------------------------------------------
    def _progress_request(self, req: Request) -> tuple[bool, Status | None]:
        if req.complete:
            return True, req.status
        if req.kind == "recv":
            env = self.runtime.matching.try_match(req.source, req.tag, self.context)
            if env is None:
                self.runtime.matching.progress()
                env = self.runtime.matching.try_match(req.source, req.tag, self.context)
            if env is None:
                return False, None
            value, status = self._deliver(env, req.datatype, req.created_at)
            req.value, req.status, req.complete = value, status, True
            if req.on_complete:
                req.on_complete(req)
            return True, status
        if req.kind == "send" and req.envelope is not None:
            if self.runtime.device.acked(req.envelope):
                req.complete = True
                return True, req.status
            return False, None
        return req.complete, req.status

    def _wait_request(
        self, req: Request, timeout: float | None = None
    ) -> tuple[Any, Status | None]:
        deadline = None if timeout is None else time.time() + timeout
        interval = 0.02
        while not req.complete:
            done, status = self._progress_request(req)
            if done:
                break
            if deadline is not None and time.time() > deadline:
                raise TimeoutError_("wait timed out", kind=req.kind, tag=req.tag)
            self.runtime.check_failures(self)
            time.sleep(interval)
            interval = min(interval * 1.5, 1.0)
        return req.value, req.status

    def waitall(
        self, requests: Sequence[Request], timeout: float | None = None
    ) -> list[tuple[Any, Status | None]]:
        return [r.wait(timeout=timeout) for r in requests]

    def waitany(
        self, requests: Sequence[Request], timeout: float | None = None
    ) -> tuple[int, Any, Status | None]:
        deadline = None if timeout is None else time.time() + timeout
        interval = 0.02
        while True:
            for i, req in enumerate(requests):
                done, status = req.test()
                if done:
                    return i, req.value, status
            if deadline is not None and time.time() > deadline:
                raise TimeoutError_("waitany timed out")
            self.runtime.check_failures(self)
            time.sleep(interval)
            interval = min(interval * 1.5, 1.0)

    def waitsome(
        self, requests: Sequence[Request], timeout: float | None = None
    ) -> list[int]:
        """``AMPI_Waitsome`` -- return every request that is ready now.

        This is the natural completion primitive for agent harnesses:
        agent turn times have a heavy right tail, so an orchestrator that
        waits for *all* workers pays the maximum, while one that consumes
        results as they land pays close to the mean.
        """
        deadline = None if timeout is None else time.time() + timeout
        while True:
            ready = [i for i, r in enumerate(requests) if r.test()[0]]
            if ready:
                return ready
            if deadline is not None and time.time() > deadline:
                return []
            self.runtime.check_failures(self)
            time.sleep(0.05)

    # -- partitioned communication (MPI-4) ---------------------------------
    def psend_init(
        self, partitions: int, dest: int, tag: int = 0,
        datatype: TypeDescriptor | str = TEXT,
    ) -> Request:
        """``AMPI_Psend_init`` -- a send whose payload becomes ready in pieces.

        MPI-4 added partitioned communication so that a multithreaded sender
        could mark parts of a buffer ready independently.  The agent analogue
        is exact and important: a generating agent produces its output
        incrementally, and a consumer that waits for the whole document idles
        for the entire generation.  With partitions the consumer starts on
        section one while section two is still being written, which converts
        a serial dependency into a pipeline.
        """
        req = Request(self, "send", dest=dest, tag=tag, persistent=True,
                      datatype=lookup(datatype) if isinstance(datatype, str) else datatype)
        req.partitions = [None] * partitions
        return req

    def pready(self, partition: int, req: Request) -> None:
        if req.partitions is None:
            raise ArgError("pready on a non-partitioned request")
        req.ready_partitions.add(partition)
        self.send(
            {"partition": partition, "of": len(req.partitions), "data": req.partitions[partition]},
            req.dest, req.tag, "json",
        )

    def parrived(self, req: Request, partition: int) -> bool:
        return partition in req.ready_partitions

    # -- collectives (implemented in agentmpi.collectives) -----------------
    def barrier(self, **kw: Any):
        from .collectives import barrier

        return barrier(self, **kw)

    def bcast(self, value: Any = None, root: int = 0, **kw: Any):
        from .collectives import bcast

        return bcast(self, value, root, **kw)

    def scatter(self, values: Sequence[Any] | None = None, root: int = 0, **kw: Any):
        from .collectives import scatter

        return scatter(self, values, root, **kw)

    def scatterv(self, values: Sequence[Any] | None = None, root: int = 0, **kw: Any):
        from .collectives import scatterv

        return scatterv(self, values, root, **kw)

    def gather(self, value: Any, root: int = 0, **kw: Any):
        from .collectives import gather

        return gather(self, value, root, **kw)

    def allgather(self, value: Any, **kw: Any):
        from .collectives import allgather

        return allgather(self, value, **kw)

    def reduce(self, value: Any, op: Any, root: int = 0, **kw: Any):
        from .collectives import reduce as _reduce

        return _reduce(self, value, op, root, **kw)

    def allreduce(self, value: Any, op: Any, **kw: Any):
        from .collectives import allreduce

        return allreduce(self, value, op, **kw)

    def scan(self, value: Any, op: Any, **kw: Any):
        from .collectives import scan

        return scan(self, value, op, **kw)

    def exscan(self, value: Any, op: Any, **kw: Any):
        from .collectives import exscan

        return exscan(self, value, op, **kw)

    def alltoall(self, values: Sequence[Any], **kw: Any):
        from .collectives import alltoall

        return alltoall(self, values, **kw)

    def reduce_scatter(self, values: Sequence[Any], op: Any, **kw: Any):
        from .collectives import reduce_scatter

        return reduce_scatter(self, values, op, **kw)

    # -- fault tolerance (implemented in agentmpi.ft) ----------------------
    def revoke(self) -> None:
        from .ft import comm_revoke

        comm_revoke(self)

    def shrink(self) -> "Communicator":
        from .ft import comm_shrink

        return comm_shrink(self)

    def agree(self, value: Any, **kw: Any):
        from .ft import comm_agree

        return comm_agree(self, value, **kw)

    def failure_ack(self) -> None:
        from .ft import comm_failure_ack

        comm_failure_ack(self)

    def failure_get_acked(self) -> Group:
        from .ft import comm_failure_get_acked

        return comm_failure_get_acked(self)

    def replace(self, **kw: Any) -> "Communicator":
        from .ft import comm_replace

        return comm_replace(self, **kw)
