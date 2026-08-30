"""Collective algorithms, implemented over point-to-point.

Every collective here is a real distributed algorithm expressed in terms of
:meth:`Communicator.send` and :meth:`Communicator.recv`, in the same way
MPICH's ``coll`` layer is built on its point-to-point layer.  Nothing is
short-circuited through the fabric as a shared variable, because the whole
point of the exercise is that the *shape* of the communication pattern
determines an agent harness's latency, token cost and output quality, and a
centralised implementation would hide exactly those effects.

The cost model
--------------
MPI's algorithm selection is driven by the Hockney model: a message of *n*
bytes costs :math:`\\alpha + n\\beta`, where α is per-message latency and β is
inverse bandwidth.  AgentMPI reuses the model with reinterpreted parameters,

.. math::
   T(n) = \\alpha + n\\beta, \\qquad C(n) = n\\gamma

where *n* is measured in **tokens**, α is the per-invocation latency of an agent
(queueing, cold start, time to first token — tens of seconds in practice), β is
the marginal time per token, and γ is the marginal *price* per token.  Two
consequences differ sharply from HPC:

1. **α is enormous.**  In MPI, α is microseconds and β⁻¹ is gigabytes per
   second, so the α/β ratio favours algorithms that reduce message *volume*.
   For agent ranks α is seconds and effective β⁻¹ is on the order of 10²
   tokens/s, so the ratio favours algorithms that reduce the number of
   *rounds*.  Latency-optimal (logarithmic-depth) algorithms therefore win over
   a wider range of message sizes than in MPI.

2. **A third term exists: fidelity.**  For a reduction whose operator is a
   language model, the result's quality degrades with the *depth* of the
   reduction tree, because each fold composes already-lossy inputs.  A
   collective's cost is thus a triple (time, price, fidelity), and selecting a
   logarithmic algorithm trades the third against the first two.  Every
   reduction below reports the fold depth it induced so a harness can measure
   the trade rather than guess at it.

Algorithm selection is *policy*, exposed as an explicit ``algorithm=`` argument
with a documented default, rather than hidden behind a tuning table.  MPI
implementations hide it and are routinely mis-tuned; here the choice changes
output quality, so hiding it would be indefensible.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import tokens as _tokens
from .constants import Associativity, BarrierPolicy, Mode
from .errors import AmpiProcFailed, AmpiTimeout, AmpiUsageError
from .ops import Op, ReduceContext

if TYPE_CHECKING:  # pragma: no cover
    from .comm import Communicator


def _ilog2_ceil(n: int) -> int:
    return 0 if n <= 1 else (n - 1).bit_length()


@dataclass
class CollStats:
    """What a collective did, for the cost model and the trace."""

    op: str
    algorithm: str
    size: int
    rounds: int = 0
    messages_sent: int = 0
    tokens_sent: int = 0
    #: Maximum number of successive operator applications on any path from a
    #: leaf to the result.  The fidelity-relevant quantity for lossy operators.
    fold_depth: int = 0
    wall_s: float = 0.0
    root: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "op": self.op,
            "algorithm": self.algorithm,
            "size": self.size,
            "rounds": self.rounds,
            "messages_sent": self.messages_sent,
            "tokens_sent": self.tokens_sent,
            "fold_depth": self.fold_depth,
            "wall_s": round(self.wall_s, 4),
            "root": self.root,
        }
        d.update(self.extra)
        return d


class _Tracker:
    """Accumulates per-rank message statistics for one collective."""

    def __init__(self, comm: Communicator, op: str, algorithm: str, root: int | None = None) -> None:
        self.comm = comm
        self.stats = CollStats(op=op, algorithm=algorithm, size=comm.size, root=root)
        self.t0 = time.time()

    def sent(self, tokens: int) -> None:
        self.stats.messages_sent += 1
        self.stats.tokens_sent += tokens

    def finish(self, **extra: Any) -> CollStats:
        self.stats.wall_s = time.time() - self.t0
        self.stats.extra.update(extra)
        self.comm.fabric.emit(
            f"coll.{self.stats.op}",
            rank=self.comm.rt.wrank,
            ctx=self.comm.ctx,
            **self.stats.as_dict(),
        )
        return self.stats


#: Where the last collective's statistics are left, so benchmarks can read them
#: without threading a return value through every call site.
LAST_STATS: dict[int, CollStats] = {}


def _record(comm: Communicator, stats: CollStats) -> None:
    LAST_STATS[comm.rt.wrank] = stats


# ============================================================================
# Barrier
# ============================================================================


@dataclass
class BarrierResult:
    arrived: tuple[int, ...]
    absent: tuple[int, ...]
    algorithm: str
    rounds: int
    wall_s: float

    @property
    def complete(self) -> bool:
        return not self.absent

    def __bool__(self) -> bool:
        return self.complete


def barrier(
    comm: Communicator,
    *,
    timeout: float | None = 900.0,
    policy: BarrierPolicy = BarrierPolicy.RAISE,
    algorithm: str = "dissemination",
    label: str = "",
) -> BarrierResult:
    """Synchronise all members.

    A barrier is the operation that most needs rethinking for agents.  In MPI it
    is nearly free and always completes, because a process that entered the
    barrier will leave it.  With agent ranks, the probability that all *p*
    members arrive within any fixed window falls off with *p*, so an
    unconditional barrier is a liveness bug waiting to happen — the pathology
    reported over and over in multi-agent postmortems as "the agents are waiting
    for each other".

    AgentMPI's barrier therefore always carries a deadline and a
    :class:`~agentmpi.constants.BarrierPolicy`.  ``WAIT`` reproduces MPI
    semantics and is useful only when debugging.  ``PROCEED`` implements the
    *partial barrier*: continue with whoever arrived and report the absentees,
    which is what a book-translation harness wants at a glossary-exchange point
    (a missing chapter is a degraded glossary, not a dead job).  ``SHRINK`` and
    ``REVOKE`` escalate to the fault-tolerance layer, which is what a build
    harness wants at an integration point (a missing module is a dead build).

    Algorithms
    ----------
    ``dissemination``
        ⌈log₂ p⌉ rounds; in round *k* rank *r* sends to *(r+2ᵏ) mod p* and
        receives from *(r−2ᵏ) mod p*.  ``p⌈log₂ p⌉`` messages, latency
        ``⌈log₂ p⌉·α``.  Correct for any *p*, not just powers of two, which is
        why MPI implementations favour it.
    ``linear``
        Non-roots notify rank 0, which releases them.  ``2(p−1)`` messages and
        ``2(p−1)α`` latency *at the root*, but only ``2α`` at the leaves.  Loses
        badly at scale, and is included because it is the pattern every
        hand-written "wait for all workers" agent harness actually implements.
    ``central``
        Arrival is registered in the fabric and every rank polls the count.
        ``2p`` fabric round-trips, and — uniquely — it can *name* the absentees,
        which the message-passing algorithms cannot.  Required for the
        ``PROCEED``/``SHRINK``/``REVOKE`` policies.
    """
    if policy in (BarrierPolicy.PROCEED, BarrierPolicy.SHRINK, BarrierPolicy.REVOKE):
        algorithm = "central"
    epoch = comm._next_epoch("barrier")
    tr = _Tracker(comm, "barrier", algorithm)
    p, r = comm.size, comm.rank
    if p <= 1:
        stats = tr.finish(label=label)
        _record(comm, stats)
        return BarrierResult((r,), (), algorithm, 0, stats.wall_s)

    absent: tuple[int, ...] = ()
    rounds = 0
    if algorithm == "dissemination":
        n_rounds = _ilog2_ceil(p)
        for k in range(n_rounds):
            dst = (r + (1 << k)) % p
            src = (r - (1 << k)) % p
            itag = comm._itag("barrier", epoch, str(k))
            comm._csend(1, dst, itag, mode=Mode.EAGER, timeout=timeout)
            tr.sent(1)
            comm._crecv(src, itag, timeout=timeout)
        rounds = n_rounds
    elif algorithm == "linear":
        itag_up = comm._itag("barrier", epoch, "up")
        itag_dn = comm._itag("barrier", epoch, "dn")
        if r == 0:
            for _ in range(p - 1):
                comm._crecv(-1, itag_up, timeout=timeout)
            for d in range(1, p):
                comm._csend(1, d, itag_dn, mode=Mode.EAGER, timeout=timeout)
                tr.sent(1)
        else:
            comm._csend(1, 0, itag_up, mode=Mode.EAGER, timeout=timeout)
            tr.sent(1)
            comm._crecv(0, itag_dn, timeout=timeout)
        rounds = 2
    elif algorithm == "central":
        absent = _central_barrier(comm, epoch, timeout=timeout, policy=policy, label=label)
        rounds = 2
    else:
        raise AmpiUsageError("unknown barrier algorithm", algorithm=algorithm)

    stats = tr.finish(label=label, absent=list(absent))
    _record(comm, stats)
    arrived = tuple(x for x in range(p) if x not in set(absent))
    return BarrierResult(arrived, absent, algorithm, rounds, stats.wall_s)


def _central_barrier(
    comm: Communicator,
    epoch: int,
    *,
    timeout: float | None,
    policy: BarrierPolicy,
    label: str,
) -> tuple[int, ...]:
    """Arrival-counting barrier that can identify who did not arrive."""
    cid = comm._record_collective("barrier", epoch, "central", None, {"label": label})
    deadline = None if timeout is None else time.time() + timeout
    poll = 0.02
    while True:
        rows = comm.fabric.query("SELECT crank FROM coll_parts WHERE cid=?", (cid,))
        arrived = {int(x["crank"]) for x in rows}
        if len(arrived) >= comm.size:
            return ()
        if deadline is not None and time.time() > deadline:
            absent = tuple(sorted(set(range(comm.size)) - arrived))
            comm.fabric.emit(
                "barrier.timeout",
                rank=comm.rt.wrank,
                ctx=comm.ctx,
                absent=list(absent),
                policy=policy.value,
                label=label,
            )
            if policy is BarrierPolicy.RAISE or policy is BarrierPolicy.WAIT:
                raise AmpiTimeout("barrier incomplete", absent=absent, ctx=comm.ctx)
            from .ft import declare_failed, revoke, shrink_in_place  # noqa: PLC0415 - ft imports comm

            for a in absent:
                declare_failed(comm, a, kind="fail_stop", detail=f"barrier {label or epoch}")
            if policy is BarrierPolicy.REVOKE:
                revoke(comm)
                raise AmpiProcFailed("barrier revoked communicator", failed=absent, ctx=comm.ctx)
            if policy is BarrierPolicy.SHRINK:
                shrink_in_place(comm, absent)
            return absent
        time.sleep(poll)
        poll = min(0.25, poll * 1.2)


# ============================================================================
# Broadcast
# ============================================================================


def bcast(
    comm: Communicator,
    payload: Any = None,
    root: int = 0,
    *,
    algorithm: str | None = None,
    timeout: float | None = 900.0,
    mode: Mode | str = Mode.AUTO,
    admit: bool = False,
    label: str = "",
) -> Any:
    """Broadcast ``payload`` from ``root`` to all members.

    **Broadcast is drift-free in AgentMPI, and this is not an accident.**  A
    tree broadcast has intermediate ranks forward the message, and in an agent
    system the obvious implementation — "tell your children what I told you" —
    is a game of telephone whose corruption grows with depth.  Because every
    payload is content-addressed (:mod:`agentmpi.store`) and forwarding moves
    the *handle*, an interior rank cannot paraphrase what it relays, so a
    ⌈log₂ p⌉-depth tree delivers byte-identical content to every rank.  This is
    the single design decision that lets AgentMPI use logarithmic broadcast at
    all, and it is why the protocol insists on immutable artifacts rather than
    conversational message passing.

    Algorithms
    ----------
    ``flat``
        Root sends *p−1* messages.  Latency at the root ``(p−1)(α + nβ)``; two
        rounds of wall clock at the leaves.  This is what almost every agent
        framework does when it "broadcasts to the group", and it makes the root
        a serialisation point.
    ``binomial``
        Standard binomial tree, ⌈log₂ p⌉ rounds, ``p−1`` messages, latency
        ``⌈log₂ p⌉(α + nβ)``.  The default.
    ``chain``
        Pipeline: rank *i* forwards to *i+1*.  ``p−1`` rounds — bad for latency,
        but the per-rank fan-out is 1, which matters if forwarding costs the
        forwarding *agent* something (it does, if the harness admits the payload
        into context on the way through).
    ``scatter_allgather``
        Van de Geijn's algorithm: scatter disjoint pieces, then ring-allgather
        them.  In MPI this halves the bandwidth term for large messages.  In
        AgentMPI it is mostly a curiosity — but a useful one, because it is the
        only broadcast that never places the whole artifact in any interior
        rank's mailbox, so it is the bandwidth-optimal *and* context-optimal
        choice for very large artifacts.
    """
    p, r = comm.size, comm.rank
    algorithm = algorithm or ("flat" if p <= 3 else "binomial")
    epoch = comm._next_epoch("bcast")
    tr = _Tracker(comm, "bcast", algorithm, root=root)
    if p == 1:
        stats = tr.finish(label=label)
        _record(comm, stats)
        return payload

    itag = comm._itag("bcast", epoch)
    result: Any = payload

    if algorithm == "flat":
        if r == root:
            for d in range(p):
                if d != root:
                    comm._csend(payload, d, itag, mode=mode, timeout=timeout)
                    tr.sent(_tok(comm, payload))
        else:
            result = comm._crecv(root, itag, timeout=timeout, admit=admit)
        tr.stats.rounds = 1 if r != root else p - 1

    elif algorithm == "binomial":
        # The textbook binomial tree, rotated by `root` so any rank may be the
        # source.  Rank vr receives from the peer that clears vr's lowest set
        # bit, then forwards to vr|mask for every mask strictly below it.
        vr = (r - root) % p
        depth = 0
        mask = 1
        while mask < p:
            if vr & mask:
                parent = ((vr - mask) + root) % p
                result = comm._crecv(parent, itag, timeout=timeout, admit=admit)
                depth = bin(vr).count("1")
                break
            mask <<= 1
        mask >>= 1
        while mask > 0:
            child_v = vr + mask
            if child_v < p:
                child = (child_v + root) % p
                comm._csend(result, child, itag, mode=mode, timeout=timeout)
                tr.sent(_tok(comm, result))
            mask >>= 1
        tr.stats.rounds = _ilog2_ceil(p)
        tr.stats.fold_depth = depth
        tr.stats.extra["tree_depth"] = depth

    elif algorithm == "chain":
        if r == root:
            nxt = (r + 1) % p
            if nxt != root:
                comm._csend(payload, nxt, itag, mode=mode, timeout=timeout)
                tr.sent(_tok(comm, payload))
        else:
            prev = (r - 1) % p
            result = comm._crecv(prev, itag, timeout=timeout, admit=admit)
            nxt = (r + 1) % p
            if nxt != root:
                comm._csend(result, nxt, itag, mode=mode, timeout=timeout)
                tr.sent(_tok(comm, result))
        tr.stats.rounds = p - 1

    elif algorithm == "scatter_allgather":
        pieces = None
        if r == root:
            pieces = _split_payload(payload, p)
        mine = scatter(comm, pieces, root, algorithm="linear", timeout=timeout, label=f"{label}:sa")
        allpieces = allgather(comm, mine, algorithm="ring", timeout=timeout, label=f"{label}:sa")
        result = _join_payload(allpieces, payload if r == root else None)
        tr.stats.rounds = 1 + (p - 1)

    else:
        raise AmpiUsageError("unknown bcast algorithm", algorithm=algorithm)

    stats = tr.finish(label=label, tokens=_tok(comm, result))
    _record(comm, stats)
    return result


def _split_payload(payload: Any, p: int) -> list[Any]:
    if isinstance(payload, list):
        return [payload[i::p] for i in range(p)]
    text = payload if isinstance(payload, str) else None
    if text is None:
        return [payload if i == 0 else None for i in range(p)]
    step = max(1, math.ceil(len(text) / p))
    return [text[i * step : (i + 1) * step] for i in range(p)]


def _join_payload(pieces: Sequence[Any], hint: Any) -> Any:
    if all(isinstance(x, list) for x in pieces):
        out: list[Any] = []
        for chunk in pieces:
            out.extend(chunk)
        return out
    if all(isinstance(x, str) or x is None for x in pieces):
        return "".join(x or "" for x in pieces)
    return hint


# ============================================================================
# Scatter / Gather / Allgather
# ============================================================================


def scatter(
    comm: Communicator,
    payloads: Sequence[Any] | None = None,
    root: int = 0,
    *,
    algorithm: str | None = None,
    timeout: float | None = 900.0,
    mode: Mode | str = Mode.AUTO,
    admit: bool = False,
    label: str = "",
) -> Any:
    """Distribute ``payloads[i]`` to rank *i*.

    This is work decomposition, the most common collective in a data-parallel
    agent harness.  ``binomial`` forwards sub-blocks through a tree so the root
    issues only ⌈log₂ p⌉ messages instead of *p−1*; the sub-blocks passing
    through interior ranks are handles, so no interior agent reads work assigned
    to someone else.  That last property is worth stating because it is the
    difference between a scatter and "post the whole task list to the group
    chat": the latter costs every rank the full *p*-way work list in context.
    """
    p, r = comm.size, comm.rank
    algorithm = algorithm or ("linear" if p <= 4 else "binomial")
    epoch = comm._next_epoch("scatter")
    tr = _Tracker(comm, "scatter", algorithm, root=root)
    if p == 1:
        stats = tr.finish(label=label)
        _record(comm, stats)
        return payloads[0] if payloads else None

    itag = comm._itag("scatter", epoch)
    if r == root and payloads is None:
        raise AmpiUsageError("root must supply payloads to scatter", root=root)
    if r == root and len(payloads) != p:  # type: ignore[arg-type]
        raise AmpiUsageError("scatter payload count must equal comm size", got=len(payloads), size=p)  # type: ignore[arg-type]

    if algorithm == "linear":
        if r == root:
            for d in range(p):
                if d != root:
                    comm._csend(payloads[d], d, itag, mode=mode, timeout=timeout)  # type: ignore[index]
                    tr.sent(_tok(comm, payloads[d]))  # type: ignore[index]
            mine = payloads[root]  # type: ignore[index]
        else:
            mine = comm._crecv(root, itag, timeout=timeout, admit=admit)
        tr.stats.rounds = 1

    elif algorithm == "binomial":
        vr = (r - root) % p
        if vr == 0:
            block = list(payloads)  # type: ignore[arg-type]
            base = 0
        else:
            highest = 1 << (vr.bit_length() - 1)
            parent = (vr - highest + root) % p
            recvd = comm._crecv(parent, itag, timeout=timeout, admit=False)
            base, block = recvd["base"], recvd["block"]
        # Send away the upper half of the block for each child bit.
        mask = (1 << (vr.bit_length() - 1)) if vr else (1 << _ilog2_ceil(p))
        while mask > 0:
            if vr == 0 or mask < (1 << (vr.bit_length() - 1)):
                child_v = vr | mask
                if child_v != vr and child_v < p:
                    child = (child_v + root) % p
                    # The child owns virtual ranks [child_v, min(child_v+mask, p)).
                    lo = child_v - vr
                    hi = min(lo + mask, len(block))
                    sub = block[lo:hi]
                    comm._csend({"base": base + lo, "block": sub}, child, itag, mode=mode, timeout=timeout)
                    tr.sent(sum(_tok(comm, x) for x in sub))
                    block = block[:lo]
            mask >>= 1
        mine = block[0] if block else None
        tr.stats.rounds = _ilog2_ceil(p)
    else:
        raise AmpiUsageError("unknown scatter algorithm", algorithm=algorithm)

    stats = tr.finish(label=label)
    _record(comm, stats)
    return mine


def scatterv(
    comm: Communicator,
    payloads: Sequence[Sequence[Any]] | None = None,
    root: int = 0,
    **kw: Any,
) -> Any:
    """``MPI_Scatterv``: variable-sized blocks.

    Load imbalance across agent ranks is severe and *predictable* (a 400-line
    module is not a 40-line module), so the variable-count form is the one an
    agent harness should reach for by default.  Any decomposition that gives
    every rank the same *number* of items will be limited by its largest item.
    """
    return scatter(comm, payloads, root, **kw)


def gather(
    comm: Communicator,
    payload: Any,
    root: int = 0,
    *,
    algorithm: str | None = None,
    timeout: float | None = 900.0,
    mode: Mode | str = Mode.AUTO,
    admit: bool = False,
    label: str = "",
) -> list[Any] | None:
    """Collect one contribution per rank at ``root``.

    Note the interaction with context budgets: a gather of *p* artifacts of *n*
    tokens presents the root with *pn* tokens, and for realistic *p* and *n*
    that exceeds any context window.  The default ``mode=AUTO`` therefore
    delivers large contributions by handle, and ``admit=False`` keeps them out
    of the root agent's context; the root receives a *list of handles* and
    decides what to materialise.  A harness that ignores this is the origin of
    the most common failure in fan-in agent designs.
    """
    p, r = comm.size, comm.rank
    algorithm = algorithm or ("linear" if p <= 4 else "binomial")
    epoch = comm._next_epoch("gather")
    tr = _Tracker(comm, "gather", algorithm, root=root)
    if p == 1:
        stats = tr.finish(label=label)
        _record(comm, stats)
        return [payload]

    itag = comm._itag("gather", epoch)
    out: list[Any] | None = None

    if algorithm == "linear":
        if r == root:
            out = [None] * p
            out[root] = payload
            for _ in range(p - 1):
                msg = comm.recv(source=-1, tag=itag, timeout=timeout, admit=admit, _internal=True)
                body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
                out[msg.source] = body
        else:
            comm._csend(payload, root, itag, mode=mode, timeout=timeout)
            tr.sent(_tok(comm, payload))
        tr.stats.rounds = 1

    elif algorithm == "binomial":
        vr = (r - root) % p
        acc: dict[int, Any] = {vr: payload}
        mask = 1
        while mask < p:
            if vr & mask:
                highest = mask
                parent = ((vr - highest) + root) % p
                comm._csend({str(k): v for k, v in acc.items()}, parent, itag, mode=mode, timeout=timeout)
                tr.sent(sum(_tok(comm, v) for v in acc.values()))
                break
            child_v = vr | mask
            if child_v < p:
                child = (child_v + root) % p
                got = comm._crecv(child, itag, timeout=timeout, admit=False)
                acc.update({int(k): v for k, v in got.items()})
            mask <<= 1
        if vr == 0:
            # `acc` is keyed by virtual rank; undo the rotation by root.
            out = [acc.get((i - root) % p) for i in range(p)]
        tr.stats.rounds = _ilog2_ceil(p)
    else:
        raise AmpiUsageError("unknown gather algorithm", algorithm=algorithm)

    stats = tr.finish(label=label)
    _record(comm, stats)
    return out


def allgather(
    comm: Communicator,
    payload: Any,
    *,
    algorithm: str | None = None,
    timeout: float | None = 900.0,
    mode: Mode | str = Mode.AUTO,
    admit: bool = False,
    tag_hint: str = "allgather",
    label: str = "",
) -> list[Any]:
    """Every rank receives every rank's contribution, in rank order.

    Algorithms
    ----------
    ``ring``
        *p−1* rounds, each rank sending one block per round.  Bandwidth-optimal,
        latency-pessimal.
    ``recursive_doubling``
        ⌈log₂ p⌉ rounds, doubling the block each round.  Latency-optimal for
        powers of two; needs the standard "extra ranks" correction otherwise,
        which is implemented here by falling back to Bruck.
    ``bruck``
        ⌈log₂ p⌉ rounds for any *p*, at the price of a final local rotation.
        The default, because agent rank counts are rarely powers of two and α
        dominates.
    ``gather_bcast``
        Gather to rank 0 then broadcast.  Included because it is what a
        harness with a coordinator naturally writes, and it is a factor of two
        worse in rounds while making rank 0 a bottleneck.
    """
    p, r = comm.size, comm.rank
    algorithm = algorithm or ("ring" if p <= 3 else "bruck")
    if algorithm == "recursive_doubling" and (p & (p - 1)) != 0:
        algorithm = "bruck"
    epoch = comm._next_epoch("allgather")
    tr = _Tracker(comm, "allgather", algorithm)
    if p == 1:
        stats = tr.finish(label=label)
        _record(comm, stats)
        return [payload]

    itag = comm._itag(tag_hint, epoch)
    out: list[Any]

    if algorithm == "ring":
        buf: list[Any] = [None] * p
        buf[r] = payload
        cur_val = payload
        cur_idx = r
        for k in range(p - 1):
            right, left = (r + 1) % p, (r - 1) % p
            msg = comm.sendrecv(
                {"idx": cur_idx, "val": cur_val},
                dest=right,
                source=left,
                sendtag=f"{itag}:{k}",
                recvtag=f"{itag}:{k}",
                mode=mode,
                timeout=timeout,
                admit=False,
                _internal=True,
            )
            tr.sent(_tok(comm, cur_val))
            body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
            cur_idx, cur_val = int(body["idx"]), body["val"]
            buf[cur_idx] = cur_val
        out = buf
        tr.stats.rounds = p - 1

    elif algorithm == "recursive_doubling":
        have: dict[int, Any] = {r: payload}
        n_rounds = _ilog2_ceil(p)
        for k in range(n_rounds):
            partner = r ^ (1 << k)
            if partner >= p:
                continue
            msg = comm.sendrecv(
                {str(i): v for i, v in have.items()},
                dest=partner,
                source=partner,
                sendtag=f"{itag}:{k}",
                recvtag=f"{itag}:{k}",
                mode=mode,
                timeout=timeout,
                admit=False,
                _internal=True,
            )
            tr.sent(sum(_tok(comm, v) for v in have.values()))
            body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
            have.update({int(i): v for i, v in body.items()})
        out = [have.get(i) for i in range(p)]
        tr.stats.rounds = n_rounds

    elif algorithm == "bruck":
        # Blocks are held in rotated order; rank r starts holding its own block
        # at local slot 0 and accumulates 2^k blocks per round.
        local: list[Any] = [payload]
        n_rounds = _ilog2_ceil(p)
        for k in range(n_rounds):
            dist = 1 << k
            dst = (r - dist) % p
            src = (r + dist) % p
            n_send = min(dist, p - dist)
            msg = comm.sendrecv(
                local[:n_send],
                dest=dst,
                source=src,
                sendtag=f"{itag}:{k}",
                recvtag=f"{itag}:{k}",
                mode=mode,
                timeout=timeout,
                admit=False,
                _internal=True,
            )
            tr.sent(sum(_tok(comm, v) for v in local[:n_send]))
            body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
            local.extend(body)
            local = local[:p]
        out = [None] * p
        for i, val in enumerate(local[:p]):
            out[(r + i) % p] = val
        tr.stats.rounds = n_rounds

    elif algorithm == "gather_bcast":
        got = gather(comm, payload, 0, timeout=timeout, mode=mode, label=label)
        out = bcast(comm, got, 0, timeout=timeout, mode=mode, label=label)
        tr.stats.rounds = 2 * _ilog2_ceil(p)
    else:
        raise AmpiUsageError("unknown allgather algorithm", algorithm=algorithm)

    stats = tr.finish(label=label)
    _record(comm, stats)
    return out


# ============================================================================
# Reduce family
# ============================================================================


def _reduce_ctx(comm: Communicator, depth: int, weight: int) -> ReduceContext:
    return ReduceContext(rank=comm.rank, depth=depth, weight=weight, agent=comm.agent_fn())


def reduce(
    comm: Communicator,
    payload: Any,
    op: Op,
    root: int = 0,
    *,
    algorithm: str | None = None,
    timeout: float | None = 1800.0,
    mode: Mode | str = Mode.AUTO,
    label: str = "",
) -> Any:
    """Combine all contributions at ``root`` with ``op``.

    Algorithm selection is *constrained by the operator's declared algebra*.
    An operator declared :attr:`~agentmpi.constants.Associativity.NONE` may only
    be evaluated by the serial chain, exactly as MPI may not reorder a
    non-commutative operator; requesting a tree for such an operator is an
    error rather than a silent quality regression.

    Algorithms
    ----------
    ``chain``
        Serial left fold along rank order.  ``p−1`` operator applications,
        ``p−1`` rounds, fold depth ``p−1``.  The only algorithm valid for a
        non-associative operator, and the reference semantics
        (:meth:`agentmpi.ops.Op.fold`) that the others are tested against.
    ``binomial``
        Binomial tree.  ``p−1`` applications, ⌈log₂ p⌉ rounds, fold depth
        ⌈log₂ p⌉.  For an exact operator this strictly dominates ``chain``.  For
        a lossy operator it trades a factor of ``(p−1)/log₂ p`` in latency
        against ``log₂ p`` successive lossy compositions instead of ``p−1``:
        note that the tree has *lower* depth, so for a merely lossy operator
        the tree is better on both axes.  The interesting adversarial case is
        an operator that is lossy *and* order-sensitive, where the chain's
        left-to-right discipline preserves information the tree discards.
    ``flat``
        Everyone sends to the root, which folds locally.  ``p−1`` messages, one
        round, fold depth ``p−1`` — but all *p−1* applications execute on the
        root, so the root's cost is ``(p−1)`` agent calls.  This is what a
        "manager summarises all worker reports" harness does, and it is the
        right choice when only the root has the judgement to combine and *n* is
        small enough to fit its context.
    """
    p, r = comm.size, comm.rank
    if algorithm is None:
        algorithm = "chain" if op.associativity is Associativity.NONE else ("flat" if p <= 3 else "binomial")
    if algorithm != "chain" and op.associativity is Associativity.NONE:
        raise AmpiUsageError(
            "operator declared non-associative; only the chain algorithm is legal",
            op=op.name,
            algorithm=algorithm,
        )
    # A binomial tree keeps the lower virtual rank as the accumulator at every
    # combine, so rank order is preserved along each path and non-commutative
    # operators remain correct -- the same reasoning MPI uses to permit trees
    # for user operators declared non-commutative.
    epoch = comm._next_epoch("reduce")
    tr = _Tracker(comm, "reduce", algorithm, root=root)
    if p == 1:
        stats = tr.finish(label=label, op=op.name)
        _record(comm, stats)
        return payload

    itag = comm._itag("reduce", epoch)
    result: Any = None

    if algorithm == "chain":
        # Order-preserving: rank (root+1) starts, each rank folds and passes on.
        order = [(root + 1 + i) % p for i in range(p - 1)] + [root]
        pos = order.index(r)
        if pos == 0:
            acc, weight = payload, 1
        else:
            prev = order[pos - 1]
            got = comm._crecv(prev, itag, timeout=timeout, admit=False)
            acc = op.fn(got["acc"], payload, _reduce_ctx(comm, got["depth"], got["weight"]))
            weight = int(got["weight"]) + 1
            tr.stats.fold_depth = int(got["depth"]) + 1
        if pos < len(order) - 1:
            nxt = order[pos + 1]
            comm._csend({"acc": acc, "depth": tr.stats.fold_depth, "weight": weight}, nxt, itag, mode=mode, timeout=timeout)
            tr.sent(_tok(comm, acc))
            result = None
        else:
            result = acc
        tr.stats.rounds = p - 1

    elif algorithm == "flat":
        if r == root:
            contribs: list[tuple[int, Any]] = [(root, payload)]
            for _ in range(p - 1):
                msg = comm.recv(source=-1, tag=itag, timeout=timeout, admit=False, _internal=True)
                body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
                contribs.append((msg.source, body))
            if not op.commutative:
                contribs.sort(key=lambda kv: kv[0])
            acc = contribs[0][1]
            for i, (_, v) in enumerate(contribs[1:], start=1):
                acc = op.fn(acc, v, _reduce_ctx(comm, i, i + 1))
            result = acc
            tr.stats.fold_depth = p - 1
        else:
            comm._csend(payload, root, itag, mode=mode, timeout=timeout)
            tr.sent(_tok(comm, payload))
        tr.stats.rounds = 1

    elif algorithm == "binomial":
        vr = (r - root) % p
        acc, weight, depth = payload, 1, 0
        mask = 1
        while mask < p:
            if vr & mask:
                parent = ((vr - mask) + root) % p
                comm._csend({"acc": acc, "depth": depth, "weight": weight, "vr": vr}, parent, itag, mode=mode, timeout=timeout)
                tr.sent(_tok(comm, acc))
                acc = None
                break
            child_v = vr | mask
            if child_v < p:
                child = (child_v + root) % p
                got = comm._crecv(child, itag, timeout=timeout, admit=False)
                # Lower virtual rank is the accumulator, so rank order is
                # preserved and non-commutative operators remain correct.
                depth = max(depth, int(got["depth"])) + 1
                acc = op.fn(acc, got["acc"], _reduce_ctx(comm, depth, weight + int(got["weight"])))
                weight += int(got["weight"])
            mask <<= 1
        tr.stats.fold_depth = depth
        tr.stats.rounds = _ilog2_ceil(p)
        result = acc if vr == 0 else None
    else:
        raise AmpiUsageError("unknown reduce algorithm", algorithm=algorithm)

    stats = tr.finish(label=label, op=op.name, lossy=op.lossy)
    _record(comm, stats)
    return result


def allreduce(
    comm: Communicator,
    payload: Any,
    op: Op,
    *,
    algorithm: str | None = None,
    timeout: float | None = 1800.0,
    mode: Mode | str = Mode.AUTO,
    label: str = "",
) -> Any:
    """Combine all contributions and deliver the result to every rank.

    Allreduce is the workhorse of agent harnesses for the same reason it is the
    workhorse of distributed training: it is how a population reaches a shared
    view.  Its agent uses are a shared glossary, an agreed interface contract, a
    consensus decision, a global health check.

    Algorithms
    ----------
    ``reduce_bcast``
        ``reduce`` to rank 0 then ``bcast``.  ``2⌈log₂ p⌉`` rounds, fold depth
        ⌈log₂ p⌉, and — the property that matters — **one** distinguished result
        that every rank then receives *by handle*, hence byte-identical.  The
        default.
    ``recursive_doubling``
        ⌈log₂ p⌉ rounds of pairwise exchange-and-combine; every rank computes
        the result itself.  Latency-optimal in MPI, and a *trap* for lossy
        operators: each rank performs its own fold sequence, so the *p* results
        differ.  For an exact operator that is invisible; for a semantic
        operator it means the population silently disagrees about the shared
        glossary it just agreed on.  AgentMPI permits it, records a
        ``divergence_risk`` flag in the trace, and the paper measures the
        resulting disagreement.
    ``ring``
        Reduce-scatter followed by allgather (Rabenseifner's structure).
        Bandwidth-optimal for large payloads and exact operators; requires the
        payload to be *decomposable*, which prose is not, so it is restricted to
        list-valued contributions.
    """
    p = comm.size
    if algorithm is None:
        algorithm = "reduce_bcast"
    if algorithm == "recursive_doubling" and op.associativity is Associativity.NONE:
        raise AmpiUsageError("non-associative operator requires reduce_bcast/chain", op=op.name)
    epoch = comm._next_epoch("allreduce")
    tr = _Tracker(comm, "allreduce", algorithm)
    if p == 1:
        stats = tr.finish(label=label, op=op.name)
        _record(comm, stats)
        return payload

    if algorithm == "reduce_bcast":
        red_alg = "chain" if op.associativity is Associativity.NONE else None
        val = reduce(comm, payload, op, 0, algorithm=red_alg, timeout=timeout, mode=mode, label=label)
        out = bcast(comm, val, 0, timeout=timeout, mode=mode, label=label)
        tr.stats.rounds = 2 * _ilog2_ceil(p)
        tr.stats.fold_depth = LAST_STATS.get(comm.rt.wrank, tr.stats).fold_depth
        stats = tr.finish(label=label, op=op.name, divergence_risk=False)
        _record(comm, stats)
        return out

    if algorithm == "recursive_doubling":
        itag = comm._itag("allreduce", epoch)
        acc, weight, depth = payload, 1, 0
        n_rounds = _ilog2_ceil(p)
        r = comm.rank
        # Standard non-power-of-two handling: the highest 2^n ranks below p pair
        # up with the remainder, which contributes and then sits out.
        pof2 = 1 << (p.bit_length() - 1) if (p & (p - 1)) else p
        rem = p - pof2
        newrank = r
        if r < 2 * rem:
            if r % 2 == 0:
                got = comm.sendrecv(
                    {"acc": acc, "depth": depth, "weight": weight},
                    dest=r + 1,
                    source=r + 1,
                    sendtag=f"{itag}:pre",
                    recvtag=f"{itag}:pre",
                    mode=mode,
                    timeout=timeout,
                    admit=False,
                    _internal=True,
                )
                body = got.payload if got.payload is not None else comm.fabric.blobs.get(got.digest, got.kind)
                depth = max(depth, int(body["depth"])) + 1
                acc = op.fn(acc, body["acc"], _reduce_ctx(comm, depth, weight + int(body["weight"])))
                weight += int(body["weight"])
                newrank = r // 2
            else:
                got = comm.sendrecv(
                    {"acc": acc, "depth": depth, "weight": weight},
                    dest=r - 1,
                    source=r - 1,
                    sendtag=f"{itag}:pre",
                    recvtag=f"{itag}:pre",
                    mode=mode,
                    timeout=timeout,
                    admit=False,
                    _internal=True,
                )
                tr.sent(_tok(comm, acc))
                newrank = -1
        else:
            newrank = r - rem
        if newrank >= 0:
            for k in range(pof2.bit_length() - 1):
                partner_new = newrank ^ (1 << k)
                partner = (partner_new * 2) if partner_new < rem else (partner_new + rem)
                got = comm.sendrecv(
                    {"acc": acc, "depth": depth, "weight": weight},
                    dest=partner,
                    source=partner,
                    sendtag=f"{itag}:{k}",
                    recvtag=f"{itag}:{k}",
                    mode=mode,
                    timeout=timeout,
                    admit=False,
                    _internal=True,
                )
                tr.sent(_tok(comm, acc))
                body = got.payload if got.payload is not None else comm.fabric.blobs.get(got.digest, got.kind)
                depth = max(depth, int(body["depth"])) + 1
                if comm.rank < partner or op.commutative:
                    acc = op.fn(acc, body["acc"], _reduce_ctx(comm, depth, weight + int(body["weight"])))
                else:
                    acc = op.fn(body["acc"], acc, _reduce_ctx(comm, depth, weight + int(body["weight"])))
                weight += int(body["weight"])
        # Give the sitting-out ranks the answer.
        if r < 2 * rem:
            if r % 2 == 0:
                comm._csend(acc, r + 1, f"{itag}:post", mode=mode, timeout=timeout)
                tr.sent(_tok(comm, acc))
            else:
                acc = comm._crecv(r - 1, f"{itag}:post", timeout=timeout, admit=False)
        tr.stats.rounds = n_rounds
        tr.stats.fold_depth = depth
        stats = tr.finish(label=label, op=op.name, divergence_risk=op.lossy)
        _record(comm, stats)
        return acc

    if algorithm == "ring":
        if not isinstance(payload, list):
            raise AmpiUsageError("ring allreduce requires list-valued contributions", got=type(payload).__name__)
        parts = reduce_scatter(comm, _chunk(payload, comm.size), op, timeout=timeout, mode=mode, label=label)
        gathered = allgather(comm, parts, algorithm="ring", timeout=timeout, mode=mode, label=label)
        out = [x for chunk in gathered for x in (chunk if isinstance(chunk, list) else [chunk])]
        tr.stats.rounds = 2 * (comm.size - 1)
        stats = tr.finish(label=label, op=op.name, divergence_risk=False)
        _record(comm, stats)
        return out

    raise AmpiUsageError("unknown allreduce algorithm", algorithm=algorithm)


def scan(
    comm: Communicator,
    payload: Any,
    op: Op,
    *,
    exclusive: bool = False,
    algorithm: str | None = None,
    timeout: float | None = 1800.0,
    mode: Mode | str = Mode.AUTO,
    label: str = "",
) -> Any:
    """Prefix reduction: rank *i* receives ``op`` over ranks ``0..i``.

    Scan is the most under-appreciated collective for agent work.  Any task
    with *sequential* dependence but *parallel* bulk — translating a novel where
    chapter *i* must be consistent with the names and register established in
    chapters ``0..i−1``, writing a document whose later sections must not
    contradict earlier ones — is a prefix computation.  A harness that instead
    runs the task strictly sequentially pays ``p`` agent latencies; a harness
    that ignores the dependence produces inconsistent output.  ``scan`` gives
    the middle option, and the ``recursive_doubling`` algorithm gets each rank
    its prefix in ⌈log₂ p⌉ rounds rather than ``p``.

    Algorithms
    ----------
    ``chain``
        Rank *i* receives the prefix from *i−1*, folds, forwards.  ``p−1``
        rounds; the reference semantics.
    ``recursive_doubling``
        Hillis–Steele: in round *k*, rank *r* sends its accumulator to
        ``r + 2ᵏ`` and folds anything from ``r − 2ᵏ``.  ⌈log₂ p⌉ rounds and
        ``p log p`` operator applications — it trades *more* operator work for
        *fewer* rounds, the opposite of the usual trade, and for agents that is
        the right direction whenever α dominates.
    """
    p, r = comm.size, comm.rank
    if algorithm is None:
        algorithm = "chain" if op.associativity is Associativity.NONE else ("recursive_doubling" if p > 4 else "chain")
    if algorithm != "chain" and op.associativity is Associativity.NONE:
        raise AmpiUsageError("non-associative operator requires the chain scan", op=op.name)
    epoch = comm._next_epoch("scan")
    tr = _Tracker(comm, "exscan" if exclusive else "scan", algorithm)
    itag = comm._itag("scan", epoch)

    if p == 1:
        stats = tr.finish(label=label, op=op.name)
        _record(comm, stats)
        return op.identity if exclusive else payload

    if algorithm == "chain":
        incoming = None
        if r > 0:
            got = comm._crecv(r - 1, itag, timeout=timeout, admit=False)
            incoming = got["acc"]
            depth = int(got["depth"])
        else:
            depth = 0
        inclusive = payload if incoming is None else op.fn(incoming, payload, _reduce_ctx(comm, depth, r + 1))
        if incoming is not None:
            depth += 1
        if r < p - 1:
            comm._csend({"acc": inclusive, "depth": depth}, r + 1, itag, mode=mode, timeout=timeout)
            tr.sent(_tok(comm, inclusive))
        tr.stats.rounds = p - 1
        tr.stats.fold_depth = depth
        out = (incoming if incoming is not None else op.identity) if exclusive else inclusive

    elif algorithm == "recursive_doubling":
        inclusive = payload
        excl: Any = op.identity
        depth = 0
        got_any = False
        n_rounds = _ilog2_ceil(p)
        for k in range(n_rounds):
            dist = 1 << k
            dst, src = r + dist, r - dist
            send_val = {"acc": inclusive, "depth": depth}
            recvd = None
            if 0 <= src and dst < p:
                msg = comm.sendrecv(
                    send_val, dest=dst, source=src, sendtag=f"{itag}:{k}", recvtag=f"{itag}:{k}",
                    mode=mode, timeout=timeout, admit=False, _internal=True,
                )
                tr.sent(_tok(comm, inclusive))
                recvd = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
            elif dst < p:
                comm._csend(send_val, dst, f"{itag}:{k}", mode=mode, timeout=timeout)
                tr.sent(_tok(comm, inclusive))
            elif 0 <= src:
                recvd = comm._crecv(src, f"{itag}:{k}", timeout=timeout, admit=False)
            if recvd is not None:
                depth = max(depth, int(recvd["depth"])) + 1
                # Left operand is the lower-ranked prefix, preserving order.
                inclusive = op.fn(recvd["acc"], inclusive, _reduce_ctx(comm, depth, r + 1))
                excl = recvd["acc"] if not got_any else op.fn(recvd["acc"], excl, _reduce_ctx(comm, depth, r))
                got_any = True
        tr.stats.rounds = n_rounds
        tr.stats.fold_depth = depth
        out = excl if exclusive else inclusive
    else:
        raise AmpiUsageError("unknown scan algorithm", algorithm=algorithm)

    stats = tr.finish(label=label, op=op.name)
    _record(comm, stats)
    return out


def reduce_scatter(
    comm: Communicator,
    payloads: Sequence[Any],
    op: Op,
    *,
    timeout: float | None = 1800.0,
    mode: Mode | str = Mode.AUTO,
    label: str = "",
) -> Any:
    """Reduce element-wise, then scatter: rank *i* receives the *i*-th result.

    Each rank contributes *p* items and receives the reduction of the *i*-th
    items.  The natural agent reading is a **partitioned reduction**: every rank
    has an opinion about every module, and each module's owner receives the
    combined opinion about *their* module.  That is a cross-review fan-in, and
    doing it this way costs each reviewer one message instead of *p*.
    """
    p, r = comm.size, comm.rank
    if len(payloads) != p:
        raise AmpiUsageError("reduce_scatter requires one item per rank", got=len(payloads), size=p)
    epoch = comm._next_epoch("reduce_scatter")
    tr = _Tracker(comm, "reduce_scatter", "linear")
    itag = comm._itag("reduce_scatter", epoch)
    # Each rank sends item j to rank j and folds what it receives for itself.
    for d in range(p):
        if d != r:
            comm._csend(payloads[d], d, itag, mode=mode, timeout=timeout)
            tr.sent(_tok(comm, payloads[d]))
    contribs: list[tuple[int, Any]] = [(r, payloads[r])]
    for _ in range(p - 1):
        msg = comm.recv(source=-1, tag=itag, timeout=timeout, admit=False, _internal=True)
        body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
        contribs.append((msg.source, body))
    if not op.commutative:
        contribs.sort(key=lambda kv: kv[0])
    acc = contribs[0][1]
    for i, (_, v) in enumerate(contribs[1:], start=1):
        acc = op.fn(acc, v, _reduce_ctx(comm, i, i + 1))
    tr.stats.rounds = 1
    tr.stats.fold_depth = p - 1
    stats = tr.finish(label=label, op=op.name)
    _record(comm, stats)
    return acc


# ============================================================================
# Alltoall
# ============================================================================


def alltoall(
    comm: Communicator,
    payloads: Sequence[Any],
    *,
    algorithm: str | None = None,
    timeout: float | None = 1800.0,
    mode: Mode | str = Mode.AUTO,
    label: str = "",
) -> list[Any]:
    """Rank *i* sends ``payloads[j]`` to rank *j* and receives one from each.

    The agent reading is **peer review**: every rank produces a critique of
    every other rank's artifact and receives every other rank's critique of its
    own.  It is the most expensive collective — ``p(p−1)`` messages and ``p²``
    agent calls if each critique is generated separately — and a harness should
    reach for it only when the *p*-way cross-product genuinely carries
    information.  ``alltoall`` on a ring topology (see
    :func:`agentmpi.topology.neighbor_alltoall`) is the cheap alternative and
    usually the right one.

    Algorithms
    ----------
    ``pairwise``
        In step *k*, rank *r* exchanges with ``r XOR k`` (for powers of two) or
        ``(r+k) mod p`` / ``(r−k) mod p``.  ``p−1`` steps, no extra volume,
        congestion-free.  The default.
    ``bruck``
        ⌈log₂ p⌉ steps at the price of ``O(n p log p)`` volume.  In MPI this
        wins for small messages because α dominates; here α dominates *much*
        more, so it wins over a wider range — but its intermediate messages
        carry other ranks' data, so the volume increase is paid in tokens.
    ``linear``
        Post everything, then receive everything.  Requires ``p−1`` messages of
        eager credit at every rank simultaneously, and is the canonical way to
        deadlock an AgentMPI program: with ``mode=EAGER`` and a realistic
        unexpected-message budget it stalls at moderate *p*.  Retained
        deliberately as the negative example in the transport-safety experiment.
    """
    p, r = comm.size, comm.rank
    if len(payloads) != p:
        raise AmpiUsageError("alltoall requires one payload per rank", got=len(payloads), size=p)
    algorithm = algorithm or "pairwise"
    epoch = comm._next_epoch("alltoall")
    tr = _Tracker(comm, "alltoall", algorithm)
    itag = comm._itag("alltoall", epoch)
    out: list[Any] = [None] * p
    out[r] = payloads[r]

    if algorithm == "pairwise":
        for k in range(1, p):
            dst, src = (r + k) % p, (r - k) % p
            msg = comm.sendrecv(
                payloads[dst], dest=dst, source=src, sendtag=f"{itag}:{k}", recvtag=f"{itag}:{k}",
                mode=mode, timeout=timeout, admit=False, _internal=True,
            )
            tr.sent(_tok(comm, payloads[dst]))
            out[src] = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
        tr.stats.rounds = p - 1

    elif algorithm == "linear":
        for d in range(p):
            if d != r:
                comm._csend(payloads[d], d, itag, mode=mode, timeout=timeout)
                tr.sent(_tok(comm, payloads[d]))
        for _ in range(p - 1):
            msg = comm.recv(source=-1, tag=itag, timeout=timeout, admit=False, _internal=True)
            out[msg.source] = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
        tr.stats.rounds = 1

    elif algorithm == "bruck":
        # Rotate locally, exchange doubling-distance blocks, rotate back.
        rot = [payloads[(r + i) % p] for i in range(p)]
        n_rounds = _ilog2_ceil(p)
        for k in range(n_rounds):
            dist = 1 << k
            dst, src = (r + dist) % p, (r - dist) % p
            idx = [i for i in range(p) if i & dist]
            msg = comm.sendrecv(
                {"idx": idx, "vals": [rot[i] for i in idx]},
                dest=dst, source=src, sendtag=f"{itag}:{k}", recvtag=f"{itag}:{k}",
                mode=mode, timeout=timeout, admit=False, _internal=True,
            )
            tr.sent(sum(_tok(comm, rot[i]) for i in idx))
            body = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
            for i, v in zip(body["idx"], body["vals"], strict=True):
                rot[i] = v
        for i in range(p):
            out[(r - i) % p] = rot[i]
        tr.stats.rounds = n_rounds
    else:
        raise AmpiUsageError("unknown alltoall algorithm", algorithm=algorithm)

    stats = tr.finish(label=label)
    _record(comm, stats)
    return out


# ============================================================================
# helpers
# ============================================================================


def _tok(_comm: Communicator, payload: Any) -> int:
    return _tokens.count(payload) if payload is not None else 0


def _chunk(items: Sequence[Any], p: int) -> list[list[Any]]:
    step = max(1, math.ceil(len(items) / p))
    return [list(items[i * step : (i + 1) * step]) for i in range(p)]
