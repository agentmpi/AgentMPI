"""Groups, communicators, and point-to-point communication.

Why communicators
-----------------
Of everything MPI invented, the communicator is the piece most worth stealing
and the piece every existing agent framework lacks.  Pre-MPI message-passing
libraries addressed messages with ``(destination, tag)``, which meant that a
library performing its own communication could have its messages intercepted by
the application, or vice versa, whenever the two happened to pick the same
integer tag.  MPI's answer was to make a communicator the pair *(group,
context)* — an ordered set of participants together with an opaque
communication universe — and to give ``MPI_Comm_dup`` the job of manufacturing
a fresh context so a library could communicate in private.

The agent analogue of a tag collision is severe and familiar to anyone who has
built a group chat of agents: a reviewer's critique intended for the author of
module A is read by the author of module B, a supervisor's control message is
mistaken for work, and a "broadcast to everyone" costs *p* times more context
than it needed to.  A communicator gives a harness scoped, named, addressable
subsets of the agent population with private message spaces, so a review
sub-protocol and a build sub-protocol can run concurrently over the same agents
without interference.  ``split`` gives dynamic team formation; ``dup`` gives a
library its own universe; ``create_group`` gives a non-collective way to form a
subset from a known membership, which matters when some ranks are dead.

Point-to-point semantics
------------------------
AgentMPI keeps MPI's guarantees where they are cheap and meaningful:

* **Non-overtaking.**  Two messages sent by the same rank to the same rank on
  the same communicator that both match a given receive are received in send
  order.  Enforced by a per-``(ctx, src, dst)`` sequence number.
* **Match on ``(ctx, source, tag)``** with ``ANY_SOURCE``/``ANY_TAG``
  wildcards, and *contract matching* layered on top (see
  :mod:`agentmpi.schema`).
* **Progress.**  A send that has been matched completes; a receive for a
  message that has been sent completes.  There is no requirement that anything
  happen unless some rank calls into the library.

And it departs where the agent setting demands it:

* **Transport mode is visible.**  ``Mode.EAGER`` delivers the payload;
  ``Mode.RENDEZVOUS`` delivers only an envelope, and the receiver decides what
  to materialise.  MPI hides this choice because both modes deliver the same
  bytes; AgentMPI exposes it because they do *not* have the same effect on the
  receiving agent's context.
* **Eager back-pressure is explicit.**  A rank publishes an *unexpected
  message* budget.  Senders that would exceed it block.  This converts the
  classic MPI failure — an unsafe program that works until the eager buffer
  runs out — into a bounded, reported condition.
* **Every blocking call takes a deadline.**  A peer that never arrives is the
  normal case, not the exceptional one.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import algorithms, tokens as _tokens
from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    INTERNAL_TAG_PREFIX,
    UNDEFINED,
    WORLD_CTX,
    BarrierPolicy,
    MessageState,
    Mode,
)
from .errors import (
    AmpiCommError,
    AmpiContextOverflow,
    AmpiProcFailed,
    AmpiRankError,
    AmpiRevoked,
    AmpiTagError,
    AmpiTimeout,
    AmpiUsageError,
)
from .fabric import Fabric
from .ops import Op, get_op
from .rank import RankRuntime
from .schema import Contract, View, check_match

#: How long a blocking call sleeps between fabric polls.  Small enough that
#: host-side collectives over dozens of ranks complete in milliseconds, large
#: enough that 100 concurrent pollers do not saturate the SQLite write lock.
POLL_INTERVAL = 0.01
MAX_POLL_INTERVAL = 0.25

#: How often a blocked call re-reads the communicator's revocation flag.  Small
#: enough that a revoke frees peers promptly, large enough that many blocked
#: ranks do not turn the check into the dominant fabric load.
REVOKE_CHECK_INTERVAL = 0.2


@dataclass
class Status:
    """Metadata about a message, without its payload.

    Returned by :meth:`Communicator.probe`.  The point of a probe in MPI is to
    learn a message's size before committing a buffer to it; the point here is
    identical but the stakes are higher, because the "buffer" is the agent's
    context and there is no way to grow it.  ``tokens`` is therefore the field
    a context-aware harness branches on.
    """

    source: int
    tag: str
    ctx: int
    mid: int
    seq: int
    mode: str
    tokens: int
    nbytes: int
    digest: str
    kind: str
    contract: Contract | None = None
    synopsis: str = ""
    sent_at: float = 0.0

    @property
    def is_rendezvous(self) -> bool:
        return self.mode == Mode.RENDEZVOUS.value


@dataclass
class Message(Status):
    """A received message.

    ``payload`` is ``None`` for a rendezvous receive that has not been
    materialised; call :meth:`Communicator.fetch` with an optional
    :class:`~agentmpi.schema.View` to obtain some projection of it.
    """

    payload: Any = None
    admitted: bool = False

    @property
    def materialised(self) -> bool:
        return self.payload is not None


@dataclass
class Request:
    """Handle on an incomplete operation, the analogue of ``MPI_Request``.

    Nonblocking operations exist for the same reason as in MPI — to overlap
    communication with computation — but the "computation" being overlapped is
    an agent invocation that takes seconds to minutes, so the pay-off is far
    larger.  A harness that gathers results with blocking receives in rank order
    idles behind its slowest rank; one that uses ``irecv`` plus ``waitany``
    processes results as they land.  ``waitany`` is consequently the single most
    useful nonblocking primitive in an agent harness, because straggler spread
    across agent ranks is enormous compared to across CPU cores.
    """

    comm: Communicator
    kind: str
    #: Populated for completed sends.
    mid: int | None = None
    _criteria: dict[str, Any] = field(default_factory=dict)
    _result: Message | None = None
    _done: bool = False
    started_at: float = field(default_factory=time.time)

    def test(self) -> Message | None:
        """Poll once.  Returns the message if complete, else ``None``."""
        if self._done:
            return self._result
        if self.kind == "send":
            if self.comm._send_complete(self.mid):
                self._done = True
            return None if not self._done else self._result
        msg = self.comm._try_recv(**self._criteria)
        if msg is not None:
            self._result = msg
            self._done = True
        return self._result

    def wait(self, timeout: float | None = None) -> Message | None:
        deadline = None if timeout is None else time.time() + timeout
        backoff = POLL_INTERVAL
        while True:
            out = self.test()
            if self._done:
                return out
            if deadline is not None and time.time() > deadline:
                raise AmpiTimeout("request did not complete", kind=self.kind, criteria=self._criteria)
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.4)

    def cancel(self) -> None:
        self._done = True


class Group:
    """An ordered set of world ranks, the analogue of ``MPI_Group``.

    Groups are separated from communicators for the same reason as in MPI: set
    algebra over participants is a pure, local, cheap computation, while
    creating a communicator requires allocating a context and is collective.
    Keeping them apart lets a harness compute "the ranks that are still alive
    and own a module that failed its tests" locally, then create one
    communicator from the answer.
    """

    __slots__ = ("ranks",)

    def __init__(self, ranks: Iterable[int]) -> None:
        seen: dict[int, None] = {}
        for r in ranks:
            seen.setdefault(int(r), None)
        self.ranks: tuple[int, ...] = tuple(seen)

    @property
    def size(self) -> int:
        return len(self.ranks)

    def rank_of(self, wrank: int) -> int:
        try:
            return self.ranks.index(wrank)
        except ValueError:
            return UNDEFINED

    def translate(self, other: Group) -> tuple[int, ...]:
        """``MPI_Group_translate_ranks``: this group's ranks in ``other``."""
        return tuple(other.rank_of(r) for r in self.ranks)

    def incl(self, indices: Sequence[int]) -> Group:
        return Group(self.ranks[i] for i in indices)

    def excl(self, indices: Sequence[int]) -> Group:
        drop = set(indices)
        return Group(r for i, r in enumerate(self.ranks) if i not in drop)

    def union(self, other: Group) -> Group:
        return Group(list(self.ranks) + list(other.ranks))

    def intersection(self, other: Group) -> Group:
        keep = set(other.ranks)
        return Group(r for r in self.ranks if r in keep)

    def difference(self, other: Group) -> Group:
        drop = set(other.ranks)
        return Group(r for r in self.ranks if r not in drop)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Group({list(self.ranks)})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Group) and self.ranks == other.ranks

    def __hash__(self) -> int:
        return hash(self.ranks)


class Communicator:
    """(group, context) pair scoping all communication.

    Instances are per-rank: ``comm.rank`` is *this* rank's index within the
    communicator's group.  The same context id is shared by every rank's
    instance, and the membership table in the fabric is the single source of
    truth about who belongs, so two processes constructing a communicator with
    the same id observe the same group.
    """

    def __init__(
        self,
        fabric: Fabric,
        ctx: int,
        rank_rt: RankRuntime,
        *,
        name: str = "",
        generation: int = 0,
    ) -> None:
        self.fabric = fabric
        self.ctx = ctx
        self.rt = rank_rt
        self.name = name or f"comm{ctx}"
        self.generation = generation
        self._members: tuple[int, ...] = ()
        self._crank: int = UNDEFINED
        #: Per-communicator collective counter.  Collectives must be invoked in
        #: the same order on every member (MPI's rule); the counter is what lets
        #: successive collectives use disjoint internal tag spaces without any
        #: agreement step.
        self._coll_epoch = 0
        self._freed = False
        self._revoked = False
        self._last_revoke_check = 0.0
        self.refresh()

    # ------------------------------------------------------------- membership

    def refresh(self) -> None:
        rows = self.fabric.query(
            "SELECT crank, wrank, state FROM comm_members WHERE ctx=? ORDER BY crank", (self.ctx,)
        )
        if not rows:
            raise AmpiCommError("communicator has no members", ctx=self.ctx)
        self._members = tuple(int(r["wrank"]) for r in rows)
        self._active = tuple(int(r["wrank"]) for r in rows if r["state"] == "active")
        row = self.fabric.query_one("SELECT generation, revoked, name FROM comms WHERE ctx=?", (self.ctx,))
        if row is not None:
            self.generation = int(row["generation"])
            self._revoked = bool(row["revoked"])
            self.name = row["name"]
        else:
            self._revoked = False
        self._crank = self._members.index(self.rt.wrank) if self.rt.wrank in self._members else UNDEFINED

    @property
    def rank(self) -> int:
        """This rank's index within the communicator."""
        return self._crank

    @property
    def size(self) -> int:
        return len(self._members)

    @property
    def group(self) -> Group:
        return Group(self._members)

    @property
    def members(self) -> tuple[int, ...]:
        """World ranks of the members, indexed by communicator rank."""
        return self._members

    def wrank(self, crank: int) -> int:
        if not 0 <= crank < len(self._members):
            raise AmpiRankError("rank out of range", rank=crank, size=self.size, ctx=self.ctx)
        return self._members[crank]

    def crank(self, wrank: int) -> int:
        return self._members.index(wrank) if wrank in self._members else UNDEFINED

    def _check_live(self) -> None:
        """Raise if this communicator is no longer usable.

        Called from inside every blocking poll loop, and it must consult the
        *fabric* rather than a cached flag: the whole purpose of ``revoke`` is
        that a rank which has noticed a failure can release peers who are already
        blocked, and a peer that only checked its local cache would never learn.
        The fabric read is rate-limited so that a hundred blocked ranks polling
        concurrently do not contend on it.
        """
        if self._freed:
            raise AmpiCommError("communicator has been freed", ctx=self.ctx)
        now = time.time()
        if not self._revoked and now - self._last_revoke_check >= REVOKE_CHECK_INTERVAL:
            self._last_revoke_check = now
            row = self.fabric.query_one("SELECT revoked FROM comms WHERE ctx=?", (self.ctx,))
            self._revoked = bool(row and int(row["revoked"]))
        if self._revoked:
            raise AmpiRevoked("communicator revoked", ctx=self.ctx)

    # ----------------------------------------------------------- construction

    def _new_ctx(self, members: Sequence[int], *, name: str, kind: str = "intra") -> int:
        now = time.time()
        with self.fabric.write() as cur:
            ctx = self.fabric.next_counter("ctx", cur=cur)
            cur.execute(
                "INSERT INTO comms(ctx, name, parent_ctx, kind, generation, revoked, created_at)"
                " VALUES(?,?,?,?,0,0,?)",
                (ctx, name, self.ctx, kind, now),
            )
            cur.executemany(
                "INSERT INTO comm_members(ctx, crank, wrank, state) VALUES(?,?,?,'active')",
                [(ctx, i, w) for i, w in enumerate(members)],
            )
            self.fabric.emit(
                "comm.create", rank=self.rt.wrank, ctx=ctx, cur=cur, name=name, parent=self.ctx, members=list(members)
            )
        return ctx

    def dup(self, name: str | None = None) -> Communicator:
        """``MPI_Comm_dup``: same group, fresh context.

        The point is isolation, not membership: a harness hands a duplicated
        communicator to a sub-protocol so that the sub-protocol's traffic cannot
        match the harness's.  Every collective in :mod:`agentmpi.algorithms`
        relies on this discipline via reserved internal tags, which is the
        cheaper mechanism; ``dup`` is for *user* libraries.
        """
        ctx = self._new_ctx(self._members, name=name or f"{self.name}.dup")
        return Communicator(self.fabric, ctx, self.rt, name=name or f"{self.name}.dup")

    def split(self, color: int | None, key: int | None = None, *, name: str | None = None) -> Communicator | None:
        """``MPI_Comm_split``: partition into sub-communicators by colour.

        Collective over the parent.  Ranks passing ``color=None``
        (``MPI_UNDEFINED``) are in no resulting communicator and get ``None``.

        This is dynamic team formation, and it is how AgentMPI expresses the
        pattern every agent framework hand-rolls: divide the population into a
        writers' team and a reviewers' team, or one team per module, and let
        each team run its own protocol.  Because the split is collective, all
        members agree on the resulting groups without a coordinator.
        """
        key = self.rank if key is None else key
        payload = {"color": color, "key": key, "wrank": self.rt.wrank}
        allinfo = self.allgather(payload, tag_hint="split")
        mine = [(d["key"], d["wrank"]) for d in allinfo if d["color"] is not None and d["color"] == color]
        if color is None:
            return None
        mine.sort()
        members = [w for _, w in mine]
        # Every member of the new group computes the same membership, but only
        # one may create the row.  The lowest world rank in the group does it,
        # then the rest discover it -- the same "one rank creates, others join"
        # pattern MPI implementations use for context-id agreement.
        cname = name or f"{self.name}.split{color}"
        leader = members[0]
        if self.rt.wrank == leader:
            ctx = self._new_ctx(members, name=cname)
            self._publish_split(color, ctx)
        else:
            ctx = self._await_split(color, leader)
        return Communicator(self.fabric, ctx, self.rt, name=cname)

    def _publish_split(self, color: int, ctx: int) -> None:
        with self.fabric.write() as cur:
            cur.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"split:{self.ctx}:{self.generation}:{self._coll_epoch}:{color}", str(ctx)),
            )

    def _await_split(self, color: int, leader: int, timeout: float = 120.0) -> int:
        key = f"split:{self.ctx}:{self.generation}:{self._coll_epoch}:{color}"
        deadline = time.time() + timeout
        backoff = POLL_INTERVAL
        while time.time() < deadline:
            row = self.fabric.query_one("SELECT value FROM meta WHERE key=?", (key,))
            if row is not None:
                return int(row["value"])
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.5)
        raise AmpiTimeout("split leader never published context", color=color, leader=leader)

    def create_group(self, group: Group, *, name: str | None = None) -> Communicator | None:
        """``MPI_Comm_create_group``: form a communicator from a known subset.

        Non-collective over the parent — only the members participate.  MPI
        added this in 3.0 precisely because ``MPI_Comm_create`` requires *all*
        ranks of the parent to call it, which is impossible when some of them
        are dead.  For the same reason it is the primitive a fault-tolerant
        agent harness reaches for after a failure.
        """
        if self.rt.wrank not in group.ranks:
            return None
        cname = name or f"{self.name}.grp"
        members = list(group.ranks)
        leader = members[0]
        tag = f"group:{self.ctx}:{','.join(map(str, members))}"
        if self.rt.wrank == leader:
            row = self.fabric.query_one("SELECT value FROM meta WHERE key=?", (tag,))
            if row is None:
                ctx = self._new_ctx(members, name=cname)
                self.fabric.set_meta(tag, str(ctx))
            else:
                ctx = int(row["value"])
        else:
            deadline = time.time() + 120.0
            ctx = -1
            while time.time() < deadline:
                row = self.fabric.query_one("SELECT value FROM meta WHERE key=?", (tag,))
                if row is not None:
                    ctx = int(row["value"])
                    break
                time.sleep(POLL_INTERVAL)
            if ctx < 0:
                raise AmpiTimeout("group leader never published context", members=members)
        return Communicator(self.fabric, ctx, self.rt, name=cname)

    def free(self) -> None:
        self._freed = True

    # ------------------------------------------------------------ tag hygiene

    @staticmethod
    def _check_tag(tag: str, *, internal: bool) -> str:
        tag = str(tag)
        if not internal and tag.startswith(INTERNAL_TAG_PREFIX):
            raise AmpiTagError("tags beginning with the internal prefix are reserved", tag=tag)
        if tag == ANY_TAG and not internal:
            return tag
        if len(tag) > 200:
            raise AmpiTagError("tag too long", tag=tag[:40])
        return tag

    # ------------------------------------------------------------ send / recv

    def send(
        self,
        payload: Any,
        dest: int,
        tag: str = "default",
        *,
        contract: Contract | None = None,
        mode: Mode | str = Mode.AUTO,
        synopsis: str = "",
        timeout: float | None = 300.0,
        _internal: bool = False,
    ) -> int:
        """Blocking send.  Returns the message id.

        Completion means "the payload is in the fabric and the envelope is in
        the destination's mailbox", which is MPI's *standard mode*: local
        completion, no statement about the receiver.  ``Mode.SYNCHRONOUS``
        additionally waits for a matching receive, giving the ``MPI_Ssend``
        guarantee that is the only way to bound in-flight work.
        """
        self._check_live()
        tag = self._check_tag(tag, internal=_internal)
        if not 0 <= dest < self.size:
            raise AmpiRankError("destination out of range", dest=dest, size=self.size)
        if contract is not None:
            contract.validate(payload, where=f"send(dest={dest}, tag={tag})")

        blob = self.fabric.blobs.put(payload)
        chosen = self._choose_mode(mode, blob.tokens)
        if not synopsis:
            synopsis = _synopsis(payload)

        if chosen is Mode.EAGER:
            self._await_eager_credit(dest, blob.tokens, timeout=timeout)

        wsrc, wdst = self.rt.wrank, self.wrank(dest)
        now = time.time()
        with self.fabric.write() as cur:
            cur.execute(
                "INSERT INTO seqs(ctx,src,dst,next) VALUES(?,?,?,0) ON CONFLICT(ctx,src,dst) DO NOTHING",
                (self.ctx, wsrc, wdst),
            )
            cur.execute("UPDATE seqs SET next = next + 1 WHERE ctx=? AND src=? AND dst=?", (self.ctx, wsrc, wdst))
            seq = int(cur.execute("SELECT next FROM seqs WHERE ctx=? AND src=? AND dst=?", (self.ctx, wsrc, wdst)).fetchone()["next"])
            cur.execute(
                "INSERT INTO messages(ctx, src, dst, tag, seq, mode, contract, digest, kind, tokens, nbytes,"
                " synopsis, state, sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.ctx,
                    wsrc,
                    wdst,
                    tag,
                    seq,
                    chosen.value,
                    json.dumps(contract.to_json()) if contract else None,
                    blob.digest,
                    blob.kind,
                    blob.tokens,
                    blob.n_bytes,
                    synopsis[:400],
                    MessageState.QUEUED.value,
                    now,
                ),
            )
            mid = int(cur.lastrowid or 0)
            self.fabric.emit(
                "msg.send",
                rank=wsrc,
                ctx=self.ctx,
                cur=cur,
                mid=mid,
                dst=dest,
                wdst=wdst,
                tag=tag,
                seq=seq,
                mode=chosen.value,
                tokens=blob.tokens,
                digest=blob.digest[:12],
                contract=contract.name if contract else None,
            )
        self.rt.cost.n_messages_sent += 1
        self.rt.cost.tokens_sent += blob.tokens
        if chosen is Mode.RENDEZVOUS:
            self.rt.cost.tokens_deferred += blob.tokens

        if chosen is Mode.SYNCHRONOUS:
            self._await_ack(mid, timeout=timeout)
        return mid

    def ssend(self, payload: Any, dest: int, tag: str = "default", **kwargs: Any) -> int:
        """``MPI_Ssend``: does not complete until a matching receive is posted."""
        kwargs["mode"] = Mode.SYNCHRONOUS
        return self.send(payload, dest, tag, **kwargs)

    def isend(self, payload: Any, dest: int, tag: str = "default", **kwargs: Any) -> Request:
        mid = self.send(payload, dest, tag, **kwargs)
        req = Request(comm=self, kind="send", mid=mid)
        return req

    def _choose_mode(self, mode: Mode | str, n_tokens: int) -> Mode:
        mode = Mode(mode) if not isinstance(mode, Mode) else mode
        if mode is not Mode.AUTO:
            return mode
        return Mode.EAGER if n_tokens <= self.rt.eager_limit else Mode.RENDEZVOUS

    def _await_eager_credit(self, dest: int, n_tokens: int, *, timeout: float | None) -> None:
        """Flow control on the destination's unexpected-message budget.

        MPI implementations either block here or run out of memory; the
        standard's position is that a program which depends on buffering is
        *unsafe* and its behaviour is not guaranteed.  AgentMPI makes the
        resource explicit and blocks, so that an unsafe program manifests as a
        reported deadlock with an attributable cause rather than as an OOM kill
        or a silent quality collapse.
        """
        wdst = self.wrank(dest)
        row = self.fabric.query_one("SELECT unexpected_limit FROM ranks WHERE rank=?", (wdst,))
        limit = int(row["unexpected_limit"]) if row else self.rt.unexpected_limit
        if n_tokens > limit:
            raise AmpiContextOverflow(
                "single eager payload exceeds destination unexpected-message budget; use mode=rendezvous",
                tokens=n_tokens,
                limit=limit,
                dest=dest,
            )
        deadline = None if timeout is None else time.time() + timeout
        backoff = POLL_INTERVAL
        waited_from = time.time()
        reported = False
        while True:
            row = self.fabric.query_one(
                "SELECT COALESCE(SUM(tokens),0) AS t FROM messages WHERE ctx=? AND dst=? AND state=? AND mode=?",
                (self.ctx, wdst, MessageState.QUEUED.value, Mode.EAGER.value),
            )
            outstanding = int(row["t"]) if row else 0
            if outstanding + n_tokens <= limit:
                if reported:
                    self.fabric.emit(
                        "transport.credit_granted",
                        rank=self.rt.wrank,
                        ctx=self.ctx,
                        dest=dest,
                        waited_s=round(time.time() - waited_from, 3),
                    )
                return
            if not reported:
                reported = True
                self.fabric.emit(
                    "transport.credit_stall",
                    rank=self.rt.wrank,
                    ctx=self.ctx,
                    dest=dest,
                    outstanding=outstanding,
                    limit=limit,
                    tokens=n_tokens,
                )
            if deadline is not None and time.time() > deadline:
                raise AmpiContextOverflow(
                    "destination unexpected-message budget exhausted (unsafe all-eager program?)",
                    dest=dest,
                    outstanding=outstanding,
                    limit=limit,
                    waited_s=round(time.time() - waited_from, 1),
                )
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.3)

    def _await_ack(self, mid: int, *, timeout: float | None) -> None:
        deadline = None if timeout is None else time.time() + timeout
        backoff = POLL_INTERVAL
        while True:
            row = self.fabric.query_one("SELECT ack FROM messages WHERE mid=?", (mid,))
            if row is not None and int(row["ack"]):
                return
            if deadline is not None and time.time() > deadline:
                raise AmpiTimeout("synchronous send never matched", mid=mid, ctx=self.ctx)
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.4)

    def _send_complete(self, mid: int | None) -> bool:
        return True if mid is None else True

    # ---------------------------------------------------------------- receive

    def recv(
        self,
        source: int = ANY_SOURCE,
        tag: str = ANY_TAG,
        *,
        contract: Contract | None = None,
        view: View | None = None,
        admit: bool | None = None,
        timeout: float | None = 600.0,
        _internal: bool = False,
    ) -> Message:
        """Blocking receive.

        ``admit`` controls whether the payload is charged against this rank's
        context budget.  It defaults to ``True`` for eager messages and
        ``False`` for rendezvous ones, which makes the transport mode chosen by
        the *sender* the default policy for the *receiver*'s context — the
        agent-world equivalent of MPI's rule that the sender's mode determines
        whether the receiver's unexpected-message pool is consumed.
        """
        deadline = None if timeout is None else time.time() + timeout
        backoff = POLL_INTERVAL
        while True:
            msg = self._try_recv(source=source, tag=tag, contract=contract, view=view, admit=admit, _internal=_internal)
            if msg is not None:
                return msg
            self._check_live()
            if deadline is not None and time.time() > deadline:
                raise AmpiTimeout(
                    "receive timed out", ctx=self.ctx, rank=self.rank, source=source, tag=tag, waited_s=timeout
                )
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.3)

    def irecv(
        self,
        source: int = ANY_SOURCE,
        tag: str = ANY_TAG,
        *,
        contract: Contract | None = None,
        view: View | None = None,
        admit: bool | None = None,
    ) -> Request:
        return Request(
            comm=self,
            kind="recv",
            _criteria={"source": source, "tag": tag, "contract": contract, "view": view, "admit": admit},
        )

    def _try_recv(
        self,
        *,
        source: int = ANY_SOURCE,
        tag: str = ANY_TAG,
        contract: Contract | None = None,
        view: View | None = None,
        admit: bool | None = None,
        _internal: bool = False,
    ) -> Message | None:
        row = self._claim(source=source, tag=tag)
        if row is None:
            return None
        return self._deliver(row, contract=contract, view=view, admit=admit)

    def _claim(self, *, source: int, tag: str, peek: bool = False) -> dict[str, Any] | None:
        """Atomically select and mark the earliest matching message.

        Ordering by ``mid`` gives FIFO over the whole mailbox, which implies
        MPI's per-pair non-overtaking guarantee and is in fact stronger.  The
        stronger guarantee is worth having: agent harnesses are debugged by
        reading traces, and a globally FIFO mailbox makes a trace explicable.
        """
        sql = "SELECT * FROM messages WHERE ctx=? AND dst=? AND state=?"
        params: list[Any] = [self.ctx, self.rt.wrank, MessageState.QUEUED.value]
        if source != ANY_SOURCE:
            params.append(self.wrank(source))
            sql += " AND src=?"
        if tag != ANY_TAG:
            params.append(tag)
            sql += " AND tag=?"
        sql += " ORDER BY mid ASC LIMIT 1"
        if peek:
            row = self.fabric.query_one(sql, params)
            return dict(row) if row else None
        with self.fabric.write() as cur:
            row = cur.execute(sql, tuple(params)).fetchone()
            if row is None:
                return None
            cur.execute(
                "UPDATE messages SET state=?, recv_at=?, ack=1 WHERE mid=?",
                (MessageState.DELIVERED.value, time.time(), row["mid"]),
            )
            return dict(row)

    def _deliver(
        self,
        row: dict[str, Any],
        *,
        contract: Contract | None,
        view: View | None,
        admit: bool | None,
    ) -> Message:
        sent_contract = Contract.from_json(row["contract"]) if row["contract"] else None
        check_match(sent_contract, contract, ctx=self.ctx, src=int(row["src"]), dst=int(row["dst"]), tag=row["tag"])
        mode = row["mode"]
        if admit is None:
            admit = mode != Mode.RENDEZVOUS.value

        payload: Any = None
        n_admitted = int(row["tokens"])
        if admit or view is not None:
            payload = self.fabric.blobs.get(row["digest"], row["kind"])
            if view is not None:
                payload = view.apply(payload)
                n_admitted = _tokens.count(payload)
        if admit:
            self.rt.admit(row["digest"] if view is None else f"{row['digest']}:{view.describe()}", n_admitted)
        else:
            self.rt.cost.tokens_deferred += int(row["tokens"])

        self.rt.cost.n_messages_recv += 1
        self.rt.cost.tokens_recv += n_admitted if admit else 0
        src_crank = self.crank(int(row["src"]))
        self.fabric.emit(
            "msg.recv",
            rank=self.rt.wrank,
            ctx=self.ctx,
            mid=int(row["mid"]),
            src=src_crank,
            tag=row["tag"],
            mode=mode,
            tokens=int(row["tokens"]),
            admitted=bool(admit),
            admitted_tokens=n_admitted if admit else 0,
            view=view.describe() if view else None,
            latency_s=round(time.time() - float(row["sent_at"]), 3),
        )
        return Message(
            source=src_crank,
            tag=row["tag"],
            ctx=self.ctx,
            mid=int(row["mid"]),
            seq=int(row["seq"]),
            mode=mode,
            tokens=int(row["tokens"]),
            nbytes=int(row["nbytes"]),
            digest=row["digest"],
            kind=row["kind"],
            contract=sent_contract,
            synopsis=row["synopsis"],
            sent_at=float(row["sent_at"]),
            payload=payload,
            admitted=bool(admit),
        )

    def sendrecv(
        self,
        payload: Any,
        dest: int,
        source: int | None = None,
        *,
        sendtag: str = "default",
        recvtag: str | None = None,
        **kwargs: Any,
    ) -> Message:
        """``MPI_Sendrecv``: the deadlock-free exchange primitive.

        Exists for exactly the reason it exists in MPI: the naive
        "everyone sends then everyone receives" ring exchange deadlocks under
        bounded buffering, and ``sendrecv`` lets the implementation order the
        two halves safely.  Agent harnesses hit this in halo exchange between
        neighbouring work units, which is why the book-translation experiment
        uses it for chapter-boundary context.
        """
        source = dest if source is None else source
        recvtag = sendtag if recvtag is None else recvtag
        recv_kwargs = {k: kwargs.pop(k) for k in ("contract", "view", "admit", "timeout") if k in kwargs}
        req = self.irecv(source=source, tag=recvtag, **{k: v for k, v in recv_kwargs.items() if k != "timeout"})
        self.send(payload, dest, sendtag, **kwargs)
        msg = req.wait(timeout=recv_kwargs.get("timeout", 600.0))
        assert msg is not None
        return msg

    # ------------------------------------------------------------------ probe

    def probe(self, source: int = ANY_SOURCE, tag: str = ANY_TAG, *, timeout: float | None = 600.0) -> Status:
        """Blocking probe: learn a message's size and shape before receiving it.

        In a context-budgeted world this is not a convenience but the primary
        defence against overflow.  A harness that probes, reads ``tokens``, and
        then chooses between a full receive and a narrowed
        :class:`~agentmpi.schema.View` can guarantee it never exceeds budget;
        one that receives blind cannot.
        """
        deadline = None if timeout is None else time.time() + timeout
        backoff = POLL_INTERVAL
        while True:
            st = self.iprobe(source, tag)
            if st is not None:
                return st
            self._check_live()
            if deadline is not None and time.time() > deadline:
                raise AmpiTimeout("probe timed out", source=source, tag=tag)
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.3)

    def iprobe(self, source: int = ANY_SOURCE, tag: str = ANY_TAG) -> Status | None:
        row = self._claim(source=source, tag=tag, peek=True)
        if row is None:
            return None
        return Status(
            source=self.crank(int(row["src"])),
            tag=row["tag"],
            ctx=self.ctx,
            mid=int(row["mid"]),
            seq=int(row["seq"]),
            mode=row["mode"],
            tokens=int(row["tokens"]),
            nbytes=int(row["nbytes"]),
            digest=row["digest"],
            kind=row["kind"],
            contract=Contract.from_json(row["contract"]) if row["contract"] else None,
            synopsis=row["synopsis"],
            sent_at=float(row["sent_at"]),
        )

    def mprobe(self, source: int = ANY_SOURCE, tag: str = ANY_TAG, *, timeout: float | None = 600.0) -> Status:
        """Matched probe (``MPI_Mprobe``): claim the message, receive it later.

        MPI added matched probes in 3.0 because a plain probe followed by a
        receive is racy when more than one thread receives on a communicator:
        another thread can take the probed message in between.  AgentMPI has
        the same race whenever several workers drain one rank's mailbox, so the
        matched form is provided and is what the worker loop uses.
        """
        deadline = None if timeout is None else time.time() + timeout
        backoff = POLL_INTERVAL
        sql = "SELECT * FROM messages WHERE ctx=? AND dst=? AND state=?"
        params: list[Any] = [self.ctx, self.rt.wrank, MessageState.QUEUED.value]
        if source != ANY_SOURCE:
            params.append(self.wrank(source))
            sql += " AND src=?"
        if tag != ANY_TAG:
            params.append(tag)
            sql += " AND tag=?"
        sql += " ORDER BY mid ASC LIMIT 1"
        while True:
            with self.fabric.write() as cur:
                row = cur.execute(sql, tuple(params)).fetchone()
                if row is not None:
                    cur.execute(
                        "UPDATE messages SET state=? WHERE mid=?", (MessageState.MATCHED.value, row["mid"])
                    )
            if row is not None:
                d = dict(row)
                return Status(
                    source=self.crank(int(d["src"])),
                    tag=d["tag"],
                    ctx=self.ctx,
                    mid=int(d["mid"]),
                    seq=int(d["seq"]),
                    mode=d["mode"],
                    tokens=int(d["tokens"]),
                    nbytes=int(d["nbytes"]),
                    digest=d["digest"],
                    kind=d["kind"],
                    contract=Contract.from_json(d["contract"]) if d["contract"] else None,
                    synopsis=d["synopsis"],
                    sent_at=float(d["sent_at"]),
                )
            self._check_live()
            if deadline is not None and time.time() > deadline:
                raise AmpiTimeout("mprobe timed out", source=source, tag=tag)
            time.sleep(backoff)
            backoff = min(MAX_POLL_INTERVAL, backoff * 1.3)

    def mrecv(self, status: Status, *, view: View | None = None, admit: bool | None = None) -> Message:
        with self.fabric.write() as cur:
            row = cur.execute("SELECT * FROM messages WHERE mid=?", (status.mid,)).fetchone()
            if row is None:
                raise AmpiCommError("matched message vanished", mid=status.mid)
            cur.execute(
                "UPDATE messages SET state=?, recv_at=?, ack=1 WHERE mid=?",
                (MessageState.DELIVERED.value, time.time(), status.mid),
            )
        return self._deliver(dict(row), contract=None, view=view, admit=admit)

    # ------------------------------------------------------------------ fetch

    def fetch(self, handle: str | Status | Message, *, view: View | None = None, admit: bool = True) -> Any:
        """Materialise (part of) an artifact previously announced by handle.

        This is the receiving half of the rendezvous protocol.  MPI's
        rendezvous handshake is invisible: the library sends a
        request-to-send, the receiver replies clear-to-send, and the payload
        lands in a buffer the application already sized.  AgentMPI exposes it
        because the receiver is an agent whose "buffer" is finite, unresizable
        and shared with its reasoning, so *what to materialise* is a decision
        only the harness can make.
        """
        digest = handle if isinstance(handle, str) else handle.digest
        kind = "json" if isinstance(handle, str) else handle.kind
        payload = self.fabric.blobs.get(digest, kind)
        if view is not None:
            payload = view.apply(payload)
        n = _tokens.count(payload)
        if admit:
            key = digest if view is None else f"{digest}:{view.describe()}"
            self.rt.admit(key, n)
        self.fabric.emit(
            "msg.fetch",
            rank=self.rt.wrank,
            ctx=self.ctx,
            digest=digest[:12],
            view=view.describe() if view else None,
            tokens=n,
            admitted=admit,
        )
        return payload

    def release(self, handle: str | Status | Message, *, view: View | None = None) -> int:
        """Evict an artifact from this rank's context.

        There is no MPI analogue because an MPI receive buffer is reused by the
        application at will.  It exists here because an agent's context is only
        freed by an explicit decision, and a harness that never releases will
        eventually overflow no matter how careful its transport modes are.
        """
        digest = handle if isinstance(handle, str) else handle.digest
        key = digest if view is None else f"{digest}:{view.describe()}"
        return self.rt.evict(key)

    # ---------------------------------------------- internal collective plumbing

    def _next_epoch(self, op: str) -> int:
        self._coll_epoch += 1
        return self._coll_epoch

    def _itag(self, op: str, epoch: int, extra: str = "") -> str:
        return f"{INTERNAL_TAG_PREFIX}{op}:{self.generation}:{epoch}{':' + extra if extra else ''}"

    def _csend(self, payload: Any, dest: int, itag: str, *, mode: Mode | str = Mode.AUTO, timeout: float | None = 600.0) -> int:
        return self.send(payload, dest, itag, mode=mode, timeout=timeout, _internal=True)

    def _crecv(self, source: int, itag: str, *, timeout: float | None = 600.0, admit: bool = False) -> Any:
        msg = self.recv(source=source, tag=itag, timeout=timeout, admit=admit, _internal=True)
        if msg.payload is None:
            return self.fabric.blobs.get(msg.digest, msg.kind)
        return msg.payload

    def _record_collective(self, op: str, epoch: int, algorithm: str, root: int | None, params: dict[str, Any]) -> int:
        with self.fabric.write() as cur:
            cur.execute(
                "INSERT INTO collectives(ctx, generation, epoch, op, algorithm, root, state, opened_at, params)"
                " VALUES(?,?,?,?,?,?, 'open', ?, ?) ON CONFLICT(ctx, generation, epoch, op) DO NOTHING",
                (self.ctx, self.generation, epoch, op, algorithm, root, time.time(), json.dumps(params, default=str)),
            )
            row = cur.execute(
                "SELECT cid FROM collectives WHERE ctx=? AND generation=? AND epoch=? AND op=?",
                (self.ctx, self.generation, epoch, op),
            ).fetchone()
            cid = int(row["cid"])
            cur.execute(
                "INSERT INTO coll_parts(cid, crank, arrived_at) VALUES(?,?,?)"
                " ON CONFLICT(cid, crank) DO NOTHING",
                (cid, self.rank, time.time()),
            )
        return cid

    # ------------------------------------------------------------- collectives

    def barrier(
        self,
        *,
        timeout: float | None = 900.0,
        policy: BarrierPolicy | str = BarrierPolicy.RAISE,
        algorithm: str = "dissemination",
        label: str = "",
    ) -> algorithms.BarrierResult:
        return algorithms.barrier(self, timeout=timeout, policy=BarrierPolicy(policy), algorithm=algorithm, label=label)

    def bcast(self, payload: Any = None, root: int = 0, *, algorithm: str | None = None, **kw: Any) -> Any:
        return algorithms.bcast(self, payload, root, algorithm=algorithm, **kw)

    def scatter(self, payloads: Sequence[Any] | None = None, root: int = 0, *, algorithm: str | None = None, **kw: Any) -> Any:
        return algorithms.scatter(self, payloads, root, algorithm=algorithm, **kw)

    def scatterv(self, payloads: Sequence[Sequence[Any]] | None = None, root: int = 0, **kw: Any) -> Any:
        return algorithms.scatterv(self, payloads, root, **kw)

    def gather(self, payload: Any, root: int = 0, *, algorithm: str | None = None, **kw: Any) -> list[Any] | None:
        return algorithms.gather(self, payload, root, algorithm=algorithm, **kw)

    def allgather(self, payload: Any, *, algorithm: str | None = None, tag_hint: str = "allgather", **kw: Any) -> list[Any]:
        return algorithms.allgather(self, payload, algorithm=algorithm, tag_hint=tag_hint, **kw)

    def reduce(
        self,
        payload: Any,
        op: Op | str = "SUM",
        root: int = 0,
        *,
        algorithm: str | None = None,
        fanin: int | None = None,
        **kw: Any,
    ) -> Any:
        return algorithms.reduce(self, payload, get_op(op), root, algorithm=algorithm, fanin=fanin, **kw)

    def allreduce(self, payload: Any, op: Op | str = "SUM", *, algorithm: str | None = None, **kw: Any) -> Any:
        return algorithms.allreduce(self, payload, get_op(op), algorithm=algorithm, **kw)

    def scan(self, payload: Any, op: Op | str = "SUM", *, exclusive: bool = False, **kw: Any) -> Any:
        return algorithms.scan(self, payload, get_op(op), exclusive=exclusive, **kw)

    def exscan(self, payload: Any, op: Op | str = "SUM", **kw: Any) -> Any:
        return algorithms.scan(self, payload, get_op(op), exclusive=True, **kw)

    def alltoall(self, payloads: Sequence[Any], *, algorithm: str | None = None, **kw: Any) -> list[Any]:
        return algorithms.alltoall(self, payloads, algorithm=algorithm, **kw)

    def reduce_scatter(self, payloads: Sequence[Any], op: Op | str = "SUM", **kw: Any) -> Any:
        return algorithms.reduce_scatter(self, payloads, get_op(op), **kw)

    # --------------------------------------------------------------- agent ops

    def agent(self, prompt: str, **kwargs: Any) -> Any:
        """Invoke this rank's agent.  See :meth:`agentmpi.rank.RankRuntime.agent`."""
        kwargs.setdefault("ctx", self.ctx)
        return self.rt.agent(prompt, **kwargs)

    def agent_fn(self) -> Callable[..., Any]:
        def _call(prompt: str, **kw: Any) -> Any:
            return self.agent(prompt, **kw)

        return _call

    # ------------------------------------------------------------------- repr

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Communicator {self.name} ctx={self.ctx} rank={self.rank}/{self.size} gen={self.generation}>"


def _synopsis(payload: Any, limit: int = 200) -> str:
    """One-line description of a payload, carried in rendezvous envelopes.

    A rendezvous envelope that says only "18,400 tokens, sha256:1f3c…" forces
    the receiver to fetch in order to decide whether to fetch.  A one-line
    synopsis breaks that regress cheaply, and is the minimum metadata that
    makes a probe actionable.
    """
    if isinstance(payload, str):
        first = payload.strip().splitlines()[0] if payload.strip() else ""
        return first[:limit]
    if isinstance(payload, dict):
        keys = ", ".join(sorted(payload)[:8])
        return f"object with keys: {keys}"[:limit]
    if isinstance(payload, list):
        return f"array of {len(payload)} items"[:limit]
    return str(payload)[:limit]


def make_world(
    fabric: Fabric,
    rank_rt: RankRuntime,
    size: int | None = None,
) -> Communicator:
    """Create or join the world communicator."""
    row = fabric.query_one("SELECT ctx FROM comms WHERE ctx=?", (WORLD_CTX,))
    if row is None:
        if size is None:
            raise AmpiUsageError("world communicator does not exist; pass size to create it")
        with fabric.write() as cur:
            cur.execute(
                "INSERT INTO comms(ctx, name, parent_ctx, kind, generation, revoked, created_at)"
                " VALUES(?,?,NULL,'intra',0,0,?) ON CONFLICT(ctx) DO NOTHING",
                (WORLD_CTX, "world", time.time()),
            )
            cur.executemany(
                "INSERT INTO comm_members(ctx, crank, wrank, state) VALUES(?,?,?,'active')"
                " ON CONFLICT(ctx, crank) DO NOTHING",
                [(WORLD_CTX, i, i) for i in range(size)],
            )
    return Communicator(fabric, WORLD_CTX, rank_rt, name="world")


def failed_ranks(fabric: Fabric, ctx: int) -> tuple[int, ...]:
    rows = fabric.query("SELECT rank FROM failures WHERE ctx=? ORDER BY rank", (ctx,))
    return tuple(int(r["rank"]) for r in rows)


def raise_if_failed(comm: Communicator) -> None:
    failed = failed_ranks(comm.fabric, comm.ctx)
    if failed:
        raise AmpiProcFailed("peers have failed", failed=failed, ctx=comm.ctx)
