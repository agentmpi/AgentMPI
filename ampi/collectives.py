"""Collective operations.

MPI's collectives are the reason MPI is more than a socket library: they name a
*global* pattern of data movement, and let the implementation choose the
schedule that fits the machine. AgentMPI keeps the names, the semantics and the
algorithm catalogue, but the cost structure underneath is different in a way
that changes which algorithm wins.

The key structural difference is that AgentMPI has a **shared control plane and
a private data plane**. The journal is a shared, sequentially consistent,
content-addressed store that every rank can read, so moving a *handle* to all
ranks costs one round regardless of P. But a payload only becomes useful to an
agent when it enters that agent's context window, and that is private, costly
and bounded. So:

* For collectives whose operator the *runtime* can evaluate (``sum``, ``union``,
  ``vote``, ...), the shared medium makes flat, journal-mediated schedules
  optimal -- the analogue of in-network aggregation (SHARP) rather than of a
  software tree.
* For collectives whose operator an *agent* must evaluate (``agent:merge``),
  every operator application is a serialised, minute-scale, context-consuming
  step. Here MPI's tree algorithms matter enormously: a flat reduction puts
  ``P-1`` agent merges on the critical path, while a binomial tree puts
  ``ceil(log2 P)`` there, at identical total work.

Both regimes are implemented, selectable, and measured, because the point of
the paper is that the classical algorithm catalogue transfers -- but the
*selection rule* must be rederived for the agent cost model.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ops as ops_mod
from . import p2p
from . import views as views_mod
from .core import (
    ANY_SOURCE,
    Ctx,
    check_comm_usable,
    comm_members,
    comm_to_world,
    ctx_charge,
    failed_ranks,
    heartbeat,
    internal_tag,
    package,
)
from .errors import (
    AmpiError,
    ArgError,
    ErrClass,
    LateError,
    OpError,
    ProcFailedError,
    RevokedError,
    TimeoutError_,
)
from .journal import Journal, now_ns

# --------------------------------------------------------------------------
# Algorithm catalogue
# --------------------------------------------------------------------------

ALGORITHMS: Dict[str, List[str]] = {
    "barrier": ["central", "dissemination", "linear"],
    "bcast": ["flat", "binomial", "chain", "relay"],
    "reduce": ["binomial", "flat", "chain"],
    "allreduce": ["reduce_bcast", "recursive_doubling", "flat"],
    "gather": ["flat", "binomial"],
    "allgather": ["flat", "ring", "recursive_doubling"],
    "scatter": ["flat", "binomial"],
    "alltoall": ["flat", "pairwise"],
    "scan": ["chain", "recursive_doubling"],
    "exscan": ["chain", "recursive_doubling"],
    "reduce_scatter": ["flat"],
}

DEFAULT_ALGO = {
    "barrier": "central",
    "bcast": "flat",
    "reduce": "binomial",
    "allreduce": "reduce_bcast",
    "gather": "flat",
    "allgather": "flat",
    "scatter": "flat",
    "alltoall": "flat",
    "scan": "chain",
    "exscan": "chain",
    "reduce_scatter": "flat",
}


def resolve_algo(op: str, algo: Optional[str]) -> str:
    if not algo or algo == "auto":
        return DEFAULT_ALGO[op]
    if algo not in ALGORITHMS.get(op, []):
        raise ArgError(
            f"algorithm {algo!r} is not available for {op}",
            hint=f"available: {', '.join(ALGORITHMS.get(op, []))}",
        )
    return algo


#: Rank count above which a journal-mediated barrier loses to a dissemination
#: tree. Measured, not guessed: with stub executors on the reference runtime,
#: `central` wins at P=16 (0.31s vs 0.45s) and P=32 (0.67s vs 1.08s) and loses at
#: P=64 (3.59s vs 2.59s) and P=128 (9.16s vs 6.28s), so the crossover lies
#: between 32 and 64. The shared medium behaves like a switch with in-network
#: aggregation up to a point and like a contended resource past it -- the same
#: reason MPI implementations abandon linear collectives at scale.
BARRIER_CENTRAL_MAX_P = 32


def select_barrier_algo(algo: Optional[str], size: int) -> str:
    """Pick a barrier algorithm from the communicator size.

    This is the AgentMPI analogue of MPICH selecting a collective algorithm from
    the message size: a threshold fitted to measurement, overridable by the
    caller, and documented so that a harness author can reason about it.
    """
    if algo and algo != "auto":
        return resolve_algo("barrier", algo)
    return "central" if size <= BARRIER_CENTRAL_MAX_P else "dissemination"


def select_reduce_algo(kind: str, algo: Optional[str], op: "ops_mod.Op") -> str:
    """Automatic algorithm selection for reductions.

    MPICH selects a collective algorithm from the message size, because message
    size determines whether the latency term or the bandwidth term dominates.
    The corresponding question for AgentMPI is *who evaluates the operator*, and
    the answer flips the ranking:

    * A **runtime** operator costs microseconds and can be applied by whoever
      holds the data. Since the journal is a shared medium, the cheapest schedule
      is to let one reader fold all contributions in place -- ``flat``, one round,
      zero messages. A tree here would add rounds and buy nothing. This is the
      in-network-aggregation regime.
    * An **agent** operator costs seconds to minutes and consumes context. Now
      the number of operator applications *on the critical path* is the entire
      cost, so the classical tree matters: ``binomial`` puts ``ceil(log2 P)``
      merges on the critical path where ``flat`` and ``chain`` put ``P-1``.

    An explicitly requested algorithm always wins, so a harness (or a benchmark)
    can override the rule.
    """
    if algo and algo != "auto":
        return resolve_algo(kind, algo)
    if op.fn is not None:
        return "flat"
    return "binomial" if kind == "reduce" else "reduce_bcast"


# --------------------------------------------------------------------------
# Collective identity: joining, sequencing, idempotent retry
# --------------------------------------------------------------------------


def _next_seqno(c: sqlite3.Connection, comm: str, op: str) -> int:
    v = c.execute("SELECT COALESCE(MAX(seqno),-1)+1 FROM coll WHERE comm=? AND op=?", (comm, op)).fetchone()[0]
    return int(v)


def join(
    ctx: Ctx,
    op: str,
    *,
    label: Optional[str] = None,
    reduce_op: Optional[str] = None,
    root: Optional[int] = None,
    algo: Optional[str] = None,
    quorum: Optional[float] = None,
    deadline_ns: Optional[int] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[sqlite3.Row, sqlite3.Row]:
    """Join (creating if necessary) a collective on ``ctx.comm``.

    Collective *identity* is the one place where AgentMPI cannot simply copy
    MPI. MPI identifies a collective implicitly by program order: the k-th
    collective call on a communicator matches across ranks because the program
    text says so. An LLM agent's "program order" is not reliable -- it may retry
    a shell command, skip a step, or reorder two independent calls -- so relying
    on it silently mismatches ranks.

    AgentMPI therefore makes the identity explicit and recommends a ``--label``:
    ranks join the collective named ``label`` on that communicator. Program
    order remains available as a fallback (the k-th call of this op), and a
    retried call rejoins the participant's still-open collective rather than
    starting a new one. Named collectives turned out to be the single largest
    robustness win in the whole interface.
    """
    j = ctx.j
    check_comm_usable(j, ctx.comm)
    ctx.check_live()
    crank = ctx.crank
    algo = resolve_algo(op, algo)
    ts = now_ns()
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        row = None
        if label:
            row = c.execute(
                "SELECT * FROM coll WHERE comm=? AND op=? AND json_extract(params,'$.label')=?",
                (ctx.comm, op, label),
            ).fetchone()
        else:
            # Rejoin our own open participation if there is one (retry-safe).
            row = c.execute(
                "SELECT k.* FROM coll k JOIN coll_part p ON p.coll=k.id"
                " WHERE k.comm=? AND k.op=? AND p.crank=? AND p.state NOT IN ('done','absent','failed')"
                " ORDER BY k.seqno LIMIT 1",
                (ctx.comm, op, crank),
            ).fetchone()
            if row is None:
                done = c.execute(
                    "SELECT COALESCE(MAX(k.seqno),-1) FROM coll k JOIN coll_part p ON p.coll=k.id"
                    " WHERE k.comm=? AND k.op=? AND p.crank=?",
                    (ctx.comm, op, crank),
                ).fetchone()[0]
                nxt = int(done) + 1
                row = c.execute(
                    "SELECT * FROM coll WHERE comm=? AND op=? AND seqno=?", (ctx.comm, op, nxt)
                ).fetchone()
        if row is None:
            cid = "k:" + uuid.uuid4().hex[:12]
            seqno = _next_seqno(c, ctx.comm, op)
            p = dict(params or {})
            if label:
                p["label"] = label
            c.execute(
                "INSERT INTO coll(id,job,comm,seqno,op,reduce_op,root,algo,quorum,deadline_ns,"
                "state,created_ns,nparts,params) VALUES(?,?,?,?,?,?,?,?,?,?,'open',?,?,?)",
                (
                    cid,
                    j.job_id,
                    ctx.comm,
                    seqno,
                    op,
                    reduce_op,
                    root,
                    algo,
                    float(quorum if quorum is not None else ctx.cfg.quorum),
                    deadline_ns,
                    ts,
                    0,
                    json.dumps(p),
                ),
            )
            row = c.execute("SELECT * FROM coll WHERE id=?", (cid,)).fetchone()
            j.trace(
                "coll_create",
                rank=ctx.rank,
                epoch=ctx.epoch,
                comm=ctx.comm,
                coll=cid,
                status=op,
                detail={"algo": algo, "label": label, "op": reduce_op, "root": root},
                conn=c,
            )
        cid = str(row["id"])
        if row["state"] == "revoked":
            raise RevokedError(f"collective {op}({label or row['seqno']}) was revoked")
        part = c.execute("SELECT * FROM coll_part WHERE coll=? AND crank=?", (cid, crank)).fetchone()
        if part is None:
            late = row["state"] == "closed"
            if late and op == "barrier":
                # A barrier that has already released has nothing to give a
                # latecomer, so this is the one case where lateness is an error.
                raise LateError(
                    f"barrier {label or row['seqno']!r} already released without you",
                    hint=(
                        "a quorum released this barrier; you are behind. Skip to the next phase "
                        "and check `ampi status` to see how far ahead the others are."
                    ),
                    detail={"coll": cid},
                )
            # For data-bearing collectives, a latecomer still gets the published
            # result -- it simply did not contribute to it. Returning the result
            # with a `late` flag is strictly more useful than an error, and it is
            # what bounded-staleness execution requires.
            c.execute(
                "INSERT INTO coll_part(coll,crank,state,joined_ns) VALUES(?,?,?,?)",
                (cid, crank, "late" if late else "joined", ts),
            )
            c.execute("UPDATE coll SET nparts=nparts+1 WHERE id=?", (cid,))
            part = c.execute("SELECT * FROM coll_part WHERE coll=? AND crank=?", (cid, crank)).fetchone()
            j.trace(
                "coll_join",
                rank=ctx.rank,
                epoch=ctx.epoch,
                comm=ctx.comm,
                coll=cid,
                phase="enter",
                status=op,
                conn=c,
            )
    return row, part


def _part_meta(part: sqlite3.Row) -> Dict[str, Any]:
    return json.loads(part["meta"] or "{}")


def _set_part(
    j: Journal,
    coll: str,
    crank: int,
    *,
    state: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    in_obj: Optional[str] = None,
    out_obj: Optional[str] = None,
    rounds: Optional[int] = None,
    done: bool = False,
) -> None:
    sets: List[str] = []
    args: List[Any] = []
    if state:
        sets.append("state=?")
        args.append(state)
    if meta is not None:
        sets.append("meta=?")
        args.append(json.dumps(meta, ensure_ascii=False))
    if in_obj is not None:
        sets.append("in_obj=?")
        args.append(in_obj)
    if out_obj is not None:
        sets.append("out_obj=?")
        args.append(out_obj)
    if rounds is not None:
        sets.append("rounds=?")
        args.append(rounds)
    if done:
        sets.append("done_ns=?")
        args.append(now_ns())
    if not sets:
        return
    args += [coll, crank]
    with j.tx() as c:
        c.execute(f"UPDATE coll_part SET {', '.join(sets)} WHERE coll=? AND crank=?", args)


def _close(j: Journal, coll: str, *, result_obj: Optional[str] = None, state: str = "closed") -> None:
    with j.tx() as c:
        c.execute(
            "UPDATE coll SET state=?, closed_ns=COALESCE(closed_ns,?), result_obj=COALESCE(?,result_obj)"
            " WHERE id=?",
            (state, now_ns(), result_obj, coll),
        )


def _live_members(ctx: Ctx) -> List[int]:
    """Communicator-local ranks currently believed alive."""
    dead = set(failed_ranks(ctx.j, ctx.comm))
    return [i for i, w in enumerate(comm_members(ctx.j, ctx.comm)) if w not in dead]


class LiveCache:
    """Cache the liveness view inside a polling loop.

    Recomputing "who is alive" costs a scan of the rank table plus a scan of the
    communicator membership. Doing that on every poll iteration, from every rank,
    turns an O(1)-round barrier into O(P^2) journal work per round -- which is
    exactly why the first measurements showed the journal-mediated barrier losing
    to a dissemination tree above P=32. Liveness changes on the timescale of a
    lease (minutes), so a one-second cache is both safe and sufficient.
    """

    TTL_S = 1.0

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self._at = -1e9
        self._live: List[int] = []
        self._members: Optional[List[int]] = None

    def __call__(self) -> List[int]:
        now = time.monotonic()
        if now - self._at > self.TTL_S:
            if self._members is None:
                self._members = comm_members(self.ctx.j, self.ctx.comm)
            dead = set(failed_ranks(self.ctx.j, self.ctx.comm))
            self._live = [i for i, w in enumerate(self._members) if w not in dead]
            self._at = now
        return self._live


def _child_alive(ctx: Ctx, crank: int) -> bool:
    w = comm_to_world(ctx.j, ctx.comm, crank)
    return w not in set(failed_ranks(ctx.j, ctx.comm))


def _deadline(ctx: Ctx, coll: sqlite3.Row, timeout_ns: Optional[int]) -> int:
    if coll["deadline_ns"]:
        return int(coll["deadline_ns"])
    return now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)


# --------------------------------------------------------------------------
# Barrier
# --------------------------------------------------------------------------


def barrier(
    ctx: Ctx,
    *,
    label: Optional[str] = None,
    algo: Optional[str] = None,
    quorum: Optional[float] = None,
    timeout_ns: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Barrier``.

    The quorum parameter has no MPI counterpart and is the most consequential
    relaxation in AgentMPI. Agent completion times are heavy-tailed, so a strict
    barrier over P agents waits for the slowest of P samples from a long-tailed
    distribution; at P=48 that is routinely several times the median. A quorum
    barrier releases at ``ceil(quorum * live)`` arrivals and reports who was
    late, letting the harness choose between bulk-synchronous determinism and
    bounded-staleness throughput -- the same trade stale-synchronous parameter
    servers make, expressed as a collective.
    """
    coll, part = join(ctx, "barrier", label=label,
                      algo=select_barrier_algo(algo, ctx.size), quorum=quorum)
    algo = str(coll["algo"])
    cid = str(coll["id"])
    t0 = now_ns()
    deadline = _deadline(ctx, coll, timeout_ns)
    if algo == "central":
        res = _barrier_central(ctx, coll, deadline)
    elif algo == "dissemination":
        res = _barrier_dissemination(ctx, coll, deadline)
    elif algo == "linear":
        res = _barrier_linear(ctx, coll, deadline)
    else:  # pragma: no cover - resolve_algo guards this
        raise ArgError(f"unknown barrier algorithm {algo}")
    _set_part(ctx.j, cid, ctx.crank, state="done", done=True)
    with ctx.j.tx() as c:
        ctx.j.trace(
            "coll_exit",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            coll=cid,
            phase="exit",
            status="barrier",
            dur_ns=now_ns() - t0,
            detail={"algo": algo, **{k: v for k, v in res.items() if k != "arrived"}},
            conn=c,
        )
    res.update({"coll": cid, "algo": algo, "wait_ns": now_ns() - t0})
    return res


def _barrier_central(ctx: Ctx, coll: sqlite3.Row, deadline: int) -> Dict[str, Any]:
    """Journal-mediated barrier: one round, O(1) messages, quorum-aware.

    This is the analogue of a hardware/in-network barrier: the shared medium can
    count arrivals itself, so no agent needs to relay anything.
    """
    j = ctx.j
    cid = str(coll["id"])
    q = float(coll["quorum"])
    start = time.time()
    prog = p2p.Progress(ctx)
    livec = LiveCache(ctx)
    while True:
        prog()
        live = livec()
        need = max(1, math.ceil(q * len(live)))
        arrived = [
            int(r["crank"])
            for r in j.q("SELECT crank FROM coll_part WHERE coll=?", (cid,))
        ]
        arrived_live = [a for a in arrived if a in live]
        if len(arrived_live) >= need or coll["state"] == "closed":
            # Reaching quorum *releases* the barrier but does not close it: a
            # straggler that arrives afterwards must still be able to pass
            # through, or a quorum barrier would guarantee that exactly the
            # slowest ranks fail. The collective is marked closed only once
            # every live rank has arrived, so `closed_ns` still records when the
            # barrier released for the metrics.
            all_in = len(arrived_live) >= len(live)
            with j.tx() as c:
                c.execute(
                    "UPDATE coll SET closed_ns=COALESCE(closed_ns,?)" + (", state='closed'" if all_in else "")
                    + " WHERE id=?",
                    (now_ns(), cid),
                )
            late = sorted(set(live) - set(arrived_live))
            return {
                "released": True,
                "arrived": len(arrived_live),
                "need": need,
                "live": len(live),
                "late": late,
            }
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_Barrier: {len(arrived_live)}/{need} arrived before the deadline",
                hint=(
                    "your arrival is recorded, so re-running resumes the wait. "
                    "Ranks still missing: "
                    + ", ".join(str(x) for x in sorted(set(live) - set(arrived_live))[:12])
                ),
                detail={"arrived": len(arrived_live), "need": need},
            )
        p2p._poll_sleep(time.time() - start)
        coll = j.q1("SELECT * FROM coll WHERE id=?", (cid,))  # refresh closed/revoked state


def _barrier_dissemination(ctx: Ctx, coll: sqlite3.Row, deadline: int) -> Dict[str, Any]:
    """Dissemination barrier: ceil(log2 P) rounds of pairwise exchange.

    Implemented for comparison: it is the textbook latency-optimal barrier on a
    point-to-point network, and measuring it against ``central`` quantifies how
    much AgentMPI gains from having a shared medium.
    """
    P = ctx.size
    rr = ctx.crank
    rounds = max(0, math.ceil(math.log2(P))) if P > 1 else 0
    cid = str(coll["id"])
    for k in range(rounds):
        dist = 1 << k
        to = (rr + dist) % P
        frm = (rr - dist) % P
        tg = internal_tag("barrier", k)
        p2p.send(ctx, to, tg, "1", kind="coll", coll=cid, coll_round=k, idem=f"{cid}:b:{rr}:{k}")
        p2p.recv(
            ctx,
            frm,
            tg,
            timeout_ns=max(1, deadline - now_ns()),
            materialize=False,
        )
    if rr == 0:
        _close(ctx.j, cid)
    return {"released": True, "rounds": rounds, "arrived": P, "need": P, "live": P, "late": []}


def _barrier_linear(ctx: Ctx, coll: sqlite3.Row, deadline: int) -> Dict[str, Any]:
    """Linear barrier: all ranks report to rank 0, which releases them."""
    P = ctx.size
    rr = ctx.crank
    cid = str(coll["id"])
    tg = internal_tag("barrier", 0)
    tg2 = internal_tag("barrier", 1)
    if rr == 0:
        for src in range(1, P):
            if not _child_alive(ctx, src):
                continue
            p2p.recv(ctx, src, tg, timeout_ns=max(1, deadline - now_ns()), materialize=False)
        for dst in range(1, P):
            if _child_alive(ctx, dst):
                p2p.send(ctx, dst, tg2, "go", kind="coll", coll=cid, idem=f"{cid}:rel:{dst}")
        _close(ctx.j, cid)
    else:
        p2p.send(ctx, 0, tg, "here", kind="coll", coll=cid, idem=f"{cid}:arr:{rr}")
        p2p.recv(ctx, 0, tg2, timeout_ns=max(1, deadline - now_ns()), materialize=False)
    return {"released": True, "rounds": 2, "arrived": P, "need": P, "live": P, "late": []}


# --------------------------------------------------------------------------
# Broadcast
# --------------------------------------------------------------------------


def bcast(
    ctx: Ctx,
    *,
    root: int = 0,
    text: Optional[str] = None,
    label: Optional[str] = None,
    algo: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
    schema: Optional[str] = None,
) -> Dict[str, Any]:
    """``AMPI_Bcast``.

    A finding worth stating plainly: with an immutable payload and a shared,
    content-addressed medium, broadcast is a *one-round* operation in AgentMPI
    (``flat``). MPI's binomial tree exists to overcome a network that can only
    move bytes point-to-point; AgentMPI's journal has no such limitation. What
    broadcast *does* cost is context: every rank that materialises the body pays
    ``n`` tokens, so the total context cost is ``Theta(n*P)`` regardless of the
    schedule, and the only way to reduce it is to deliver handles or views.

    Tree schedules therefore survive in AgentMPI for a different reason: when the
    forwarding agents are permitted to *transform* the payload (``relay``), a
    tree becomes a hierarchical briefing cascade in which each level adapts the
    message for its subtree -- something MPI has no analogue for, because bytes
    do not reinterpret themselves in flight.
    """
    coll, part = join(ctx, "bcast", label=label, root=root, algo=algo, params={"root": root})
    algo = str(coll["algo"])
    cid = str(coll["id"])
    rr = ctx.crank
    t0 = now_ns()
    deadline = _deadline(ctx, coll, timeout_ns)

    if rr == root:
        if text is None:
            raise ArgError("the root of AMPI_Bcast must supply a payload (--in)")
        with ctx.j.tx() as c:
            pay = package(ctx.j, text, creator=ctx.rank, cfg=ctx.cfg, schema=schema, conn=c)
        _set_part(ctx.j, cid, rr, in_obj=pay.obj, out_obj=pay.obj)
        _close(ctx.j, cid, result_obj=pay.obj)

    if algo == "flat":
        obj = _await_result_obj(ctx, cid, deadline, who=f"root rank {root}")
    elif algo in ("binomial", "chain"):
        obj = _bcast_tree(ctx, coll, root=root, algo=algo, deadline=deadline)
    elif algo == "relay":
        return _bcast_relay(ctx, coll, root=root, deadline=deadline, budget=budget)
    else:  # pragma: no cover
        raise ArgError(f"unknown bcast algorithm {algo}")

    env = _present(ctx, obj, materialize=materialize, budget=budget, what=f"bcast from {root}")
    _set_part(ctx.j, cid, rr, state="done", out_obj=obj, done=True)
    with ctx.j.tx() as c:
        ctx.j.trace(
            "coll_exit",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            coll=cid,
            phase="exit",
            status="bcast",
            tokens=env.get("context_charged", 0),
            dur_ns=now_ns() - t0,
            detail={"algo": algo, "root": root},
            conn=c,
        )
    env.update({"coll": cid, "algo": algo, "root": root, "wait_ns": now_ns() - t0})
    return env


def _await_result_obj(ctx: Ctx, cid: str, deadline: int, *, who: str) -> str:
    start = time.time()
    prog = p2p.Progress(ctx)
    while True:
        row = ctx.j.q1("SELECT result_obj,state FROM coll WHERE id=?", (cid,))
        if row is not None and row["result_obj"]:
            return str(row["result_obj"])
        prog()
        if now_ns() > deadline:
            raise TimeoutError_(
                f"collective result from {who} did not arrive before the deadline",
                hint="re-run the identical command to keep waiting",
            )
        p2p._poll_sleep(time.time() - start)


def _binomial_children(rr: int, P: int, root: int) -> List[int]:
    """Children of ``rr`` in a binomial broadcast tree rooted at ``root``."""
    vr = (rr - root) % P
    kids: List[int] = []
    mask = 1
    while mask < P:
        if vr & mask:
            break
        mask <<= 1
    mask >>= 1
    while mask >= 1:
        child = vr + mask
        if child < P:
            kids.append((child + root) % P)
        mask >>= 1
    return kids


def _binomial_parent(rr: int, P: int, root: int) -> Optional[int]:
    vr = (rr - root) % P
    if vr == 0:
        return None
    mask = 1
    while not (vr & mask):
        mask <<= 1
    return ((vr & ~mask) + root) % P


def _bcast_tree(ctx: Ctx, coll: sqlite3.Row, *, root: int, algo: str, deadline: int) -> str:
    """Binomial (or chain) broadcast in which the *handle* is forwarded.

    The agent at each internal node blocks inside the collective while it
    forwards, exactly as an MPI process does. Only the envelope enters the
    forwarder's context, so this measures the pure latency (alpha) term of a
    tree schedule without confounding it with token cost.
    """
    cid = str(coll["id"])
    P = ctx.size
    rr = ctx.crank
    tg = internal_tag("bcast", 0)
    if algo == "chain":
        parent = None if rr == root else (rr - 1) % P
        kids = [] if (rr + 1) % P == root else [(rr + 1) % P]
    else:
        parent = _binomial_parent(rr, P, root)
        kids = _binomial_children(rr, P, root)
    if parent is None:
        obj = str(ctx.j.scalar("SELECT result_obj FROM coll WHERE id=?", (cid,)))
    else:
        env = p2p.recv(ctx, parent, tg, timeout_ns=max(1, deadline - now_ns()), materialize=False)
        obj = str(env["handle"])
    for k in kids:
        if not _child_alive(ctx, k):
            continue
        p2p.send(
            ctx,
            k,
            tg,
            ctx.j.object_text(obj),
            kind="coll",
            coll=cid,
            coll_round=0,
            idem=f"{cid}:bc:{rr}->{k}",
            force_payload_mode="rendezvous",
        )
    return obj


def _bcast_relay(
    ctx: Ctx, coll: sqlite3.Row, *, root: int, deadline: int, budget: Optional[int]
) -> Dict[str, Any]:
    """``relay``: a broadcast tree whose internal nodes rewrite the payload.

    Each internal agent receives its parent's message, adapts it for its own
    subtree, and forwards the adapted version. There is no MPI analogue; the
    closest classical relative is a hierarchical reduction run in reverse. It is
    included because it is the schedule a human organisation actually uses to
    broadcast intent, and because it exposes the failure mode MPI never has:
    semantic drift accumulating over ``log P`` rewrites, which the evaluation
    measures.
    """
    cid = str(coll["id"])
    P = ctx.size
    rr = ctx.crank
    tg = internal_tag("bcast", 1)
    parent = _binomial_parent(rr, P, root)
    kids = [k for k in _binomial_children(rr, P, root) if _child_alive(ctx, k)]
    if parent is None:
        obj = str(ctx.j.scalar("SELECT result_obj FROM coll WHERE id=?", (cid,)))
        body = ctx.j.object_text(obj)
        for k in kids:
            p2p.send(ctx, k, tg, body, kind="coll", coll=cid, idem=f"{cid}:rl:{rr}->{k}")
        _set_part(ctx.j, cid, rr, state="done", out_obj=obj, done=True)
        return {"role": "root", "handle": obj, "children": kids, "depth": 0}
    env = p2p.recv(ctx, parent, tg, timeout_ns=max(1, deadline - now_ns()), materialize=True, budget=budget)
    depth = _tree_depth(rr, P, root)
    if not kids:
        _set_part(ctx.j, cid, rr, state="done", out_obj=env["handle"], done=True)
        return {"role": "leaf", "depth": depth, **env}
    work = _work_dir(ctx, cid)
    inbox = work / f"relay_in_r{rr}.md"
    inbox.write_text(env.get("body") or ctx.j.object_text(str(env["handle"])), encoding="utf-8")
    _set_part(
        ctx.j,
        cid,
        rr,
        state="reducing",
        meta={"relay_children": kids, "relay_in": str(inbox), "depth": depth},
    )
    return {
        "role": "relay",
        "depth": depth,
        "action_required": "adapt_and_forward",
        "children": kids,
        "input_file": str(inbox),
        "directive": (
            f"You are an internal node of a relay broadcast at depth {depth}. Read {inbox}, "
            f"adapt it for your {len(kids)} subordinate rank(s) {kids} (keep every constraint that "
            "binds them, drop what does not), write the adapted brief to a file, then run: "
            f"ampi bcast relay-forward --coll {cid} --in <file>"
        ),
        **{k: v for k, v in env.items() if k in ("tokens", "summary", "handle")},
    }


def _tree_depth(rr: int, P: int, root: int) -> int:
    d = 0
    cur: Optional[int] = rr
    while cur is not None and cur != root:
        cur = _binomial_parent(cur, P, root)
        d += 1
    return d


def relay_forward(ctx: Ctx, cid: str, text: str) -> Dict[str, Any]:
    part = ctx.j.q1("SELECT * FROM coll_part WHERE coll=? AND crank=?", (cid, ctx.crank))
    if part is None:
        raise ArgError(f"you are not a participant in collective {cid}")
    meta = _part_meta(part)
    kids = meta.get("relay_children") or []
    tg = internal_tag("bcast", 1)
    with ctx.j.tx() as c:
        pay = package(ctx.j, text, creator=ctx.rank, cfg=ctx.cfg, conn=c)
    for k in kids:
        p2p.send(ctx, k, tg, text, kind="coll", coll=cid, idem=f"{cid}:rl:{ctx.crank}->{k}")
    _set_part(ctx.j, cid, ctx.crank, state="done", out_obj=pay.obj, done=True)
    return {"forwarded_to": kids, "handle": pay.obj, "tokens": pay.tokens}


# --------------------------------------------------------------------------
# Presentation: how a result enters (or does not enter) the caller's context
# --------------------------------------------------------------------------


def _present(
    ctx: Ctx,
    obj: str,
    *,
    materialize: Optional[bool],
    budget: Optional[int],
    what: str,
) -> Dict[str, Any]:
    """Decide whether to hand back the body, and charge context accordingly."""
    meta = ctx.j.object_meta(obj)
    ntok = int(meta["tokens"])
    want = (ntok <= ctx.cfg.eager_tokens) if materialize is None else materialize
    out: Dict[str, Any] = {
        "handle": obj,
        "tokens": ntok,
        "summary": meta["summary"],
        "schema": meta["schema"],
        "mode": "eager" if want else "rendezvous",
    }
    if budget and ntok > budget:
        view = views_mod.render_view(ctx.j, obj, {"op": "headtail", "budget": int(budget)})
        with ctx.j.tx() as c:
            ctx_charge(ctx.j, ctx.rank, ctx.epoch, view["tokens"], conn=c, force=True, what=what)
        out["body"] = view["body"]
        out["context_charged"] = view["tokens"]
        out["note"] = f"clipped to your --budget of {budget} tokens; full payload at {obj}"
        return out
    if want:
        try:
            with ctx.j.tx() as c:
                ctx_charge(ctx.j, ctx.rank, ctx.epoch, ntok, conn=c, what=what)
            out["body"] = ctx.j.object_text(obj)
            out["context_charged"] = ntok
        except AmpiError as exc:
            if exc.err_class != ErrClass.CTX_EXCEEDED:
                raise
            view = views_mod.render_view(ctx.j, obj, {"op": "headtail", "budget": 400})
            with ctx.j.tx() as c:
                ctx_charge(ctx.j, ctx.rank, ctx.epoch, view["tokens"], conn=c, force=True, what=what)
            out["body"] = view["body"]
            out["context_charged"] = view["tokens"]
            out["note"] = "context budget exhausted; delivered a clipped view"
    else:
        with ctx.j.tx() as c:
            ctx_charge(ctx.j, ctx.rank, ctx.epoch, 40, conn=c, force=True, what="envelope")
        out["context_charged"] = 40
        out["note"] = f"payload is {ntok} tokens; read it with `ampi view {obj}` when you need it"
    return out


def _work_dir(ctx: Ctx, cid: str) -> Path:
    d = ctx.j.dir / "work" / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# Reduce / Allreduce
# --------------------------------------------------------------------------


def reduce_(
    ctx: Ctx,
    *,
    op: str,
    text: Optional[str] = None,
    root: int = 0,
    label: Optional[str] = None,
    algo: Optional[str] = None,
    all_: bool = False,
    commute: Optional[bool] = None,
    timeout_ns: Optional[int] = None,
    quorum: Optional[float] = None,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
    operand_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Reduce`` / ``AMPI_Allreduce``.

    Runtime operators complete inside this one call. Agent operators cannot: the
    operator *is* an agent, so the call returns a **merge directive** and the
    agent continues the reduction by committing its merged result with
    ``ampi reduce commit``. That continuation structure is the honest analogue of
    ``MPI_Op_create``: the library owns the schedule, the user owns the operator,
    and here the user's operator happens to need a language model and several
    seconds of thought.
    """
    kind = "allreduce" if all_ else "reduce"
    o = ops_mod.get_op(op, commute=commute)
    algo = select_reduce_algo(kind, algo, o)
    coll, part = join(
        ctx,
        kind,
        label=label,
        reduce_op=o.name,
        root=root,
        algo=algo,
        quorum=quorum,
        params={"root": root, "commute": o.commute, "operand_budget": operand_budget},
    )
    cid = str(coll["id"])
    rr = ctx.crank
    deadline = _deadline(ctx, coll, timeout_ns)

    meta = _part_meta(part)
    if text is not None and not meta.get("acc"):
        with ctx.j.tx() as c:
            pay = package(ctx.j, text, creator=ctx.rank, cfg=ctx.cfg, conn=c)
        meta["acc"] = pay.obj
        meta.setdefault("mask", 1)
        _set_part(ctx.j, cid, rr, in_obj=pay.obj, meta=meta)
    elif not meta.get("acc") and text is None:
        raise ArgError("supply your contribution with --in (text or @file)")

    if str(coll["algo"]) == "flat":
        # The journal folds the contributions itself once they have landed: one
        # round, no messages. Only available for runtime operators.
        return _reduce_flat(
            ctx,
            coll,
            o,
            deadline=deadline,
            all_=all_,
            root=root,
            materialize=materialize,
            budget=budget,
        )
    return _reduce_tree(
        ctx,
        coll,
        o,
        deadline=deadline,
        all_=all_,
        root=root,
        materialize=materialize,
        budget=budget,
        operand_budget=operand_budget,
    )


def _reduce_flat(
    ctx: Ctx,
    coll: sqlite3.Row,
    o: ops_mod.Op,
    *,
    deadline: int,
    all_: bool,
    root: int,
    materialize: Optional[bool],
    budget: Optional[int],
) -> Dict[str, Any]:
    j = ctx.j
    cid = str(coll["id"])
    rr = ctx.crank
    t0 = now_ns()
    q = float(coll["quorum"])
    start = time.time()
    if o.fn is None:
        raise OpError(
            "the 'flat' reduce algorithm cannot evaluate an agent operator",
            hint="use --algo binomial (or chain) for agent:<label> operators",
        )
    prog = p2p.Progress(ctx)
    livec = LiveCache(ctx)
    while True:
        prog()
        live = livec()
        need = max(1, math.ceil(q * len(live)))
        rows = j.q(
            "SELECT crank,in_obj FROM coll_part WHERE coll=? AND in_obj IS NOT NULL ORDER BY crank",
            (cid,),
        )
        have = [(int(r["crank"]), str(r["in_obj"])) for r in rows if int(r["crank"]) in live]
        res_obj = j.scalar("SELECT result_obj FROM coll WHERE id=?", (cid,))
        if res_obj:
            break
        if len(have) >= need:
            # Canonical rank order, so the result is reproducible whether or not
            # the operator commutes.
            values = [j.object_text(oid) for _, oid in have]
            folded = ops_mod.reduce_sequence(o, values)
            with j.tx() as c:
                pay = package(j, folded, creator=ctx.rank, cfg=ctx.cfg, conn=c)
            _close(j, cid, result_obj=pay.obj)
            res_obj = pay.obj
            with j.tx() as c:
                j.trace(
                    "coll_fold",
                    rank=ctx.rank,
                    epoch=ctx.epoch,
                    comm=ctx.comm,
                    coll=cid,
                    status=o.name,
                    tokens=pay.tokens,
                    detail={"contributions": len(values), "absent": sorted(set(live) - {k for k, _ in have})},
                    conn=c,
                )
            break
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_{'All' if all_ else ''}reduce({o.name}): {len(have)}/{need} contributions arrived",
                hint="your contribution is recorded; re-run to keep waiting",
                detail={"have": len(have), "need": need},
            )
        p2p._poll_sleep(time.time() - start)

    if not all_ and rr != root:
        _set_part(ctx.j, cid, rr, state="done", done=True)
        return {
            "coll": cid,
            "role": "contributor",
            "complete": True,
            "algo": "flat",
            "note": f"contribution recorded; the result goes to root rank {root}",
        }
    env = _present(ctx, str(res_obj), materialize=materialize, budget=budget, what="reduce result")
    _set_part(ctx.j, cid, rr, state="done", out_obj=str(res_obj), done=True)
    with ctx.j.tx() as c:
        ctx.j.trace(
            "coll_exit",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            coll=cid,
            phase="exit",
            status=("allreduce" if all_ else "reduce"),
            tokens=env.get("context_charged", 0),
            dur_ns=now_ns() - t0,
            detail={"algo": "flat", "op": o.name},
            conn=c,
        )
    env.update({"coll": cid, "complete": True, "algo": "flat", "op": o.name, "merges": 0})
    return env


def _reduce_tree(
    ctx: Ctx,
    coll: sqlite3.Row,
    o: ops_mod.Op,
    *,
    deadline: int,
    all_: bool,
    root: int,
    materialize: Optional[bool],
    budget: Optional[int],
    operand_budget: Optional[int],
) -> Dict[str, Any]:
    """Drive this rank through a binomial (or chain) reduction tree.

    State lives in ``coll_part.meta`` -- accumulator handle plus the current
    round mask -- so the whole schedule is resumable. An agent that times out,
    crashes, or is replaced mid-reduction re-enters exactly where it left off,
    which is what makes an ``O(log P)``-deep agent reduction survivable at all.
    """
    j = ctx.j
    cid = str(coll["id"])
    P = ctx.size
    rr = ctx.crank
    algo = str(coll["algo"])
    part = j.q1("SELECT * FROM coll_part WHERE coll=? AND crank=?", (cid, rr))
    assert part is not None
    meta = _part_meta(part)
    mask = int(meta.get("mask", 1))
    merges = int(meta.get("merges", 0))
    vr = (rr - root) % P

    # An outstanding agent merge directive must be honoured before we advance.
    open_step = j.q1(
        "SELECT * FROM reduce_step WHERE coll=? AND crank=? AND state='pending' ORDER BY round LIMIT 1",
        (cid, rr),
    )
    if open_step is not None:
        return _describe_step(ctx, coll, open_step)

    tg_base = internal_tag("reduce", 0)

    if algo == "recursive_doubling":
        return _reduce_recursive_doubling(
            ctx, coll, o, meta=meta, deadline=deadline, root=root,
            materialize=materialize, budget=budget, operand_budget=operand_budget,
        )

    if algo == "chain":
        # Sequential pipeline: rank i receives the running accumulator from i+1,
        # applies the operator, and passes it to i-1. Every one of the P-1
        # operator applications is therefore on the critical path. This is the
        # pessimal schedule, and it is included precisely because it is what a
        # naive "the orchestrator merges everything" harness actually does -- so
        # the paper can quantify what the tree buys over it.
        return _reduce_chain(
            ctx, coll, o, meta=meta, deadline=deadline, all_=all_, root=root,
            materialize=materialize, budget=budget, operand_budget=operand_budget,
        )

    while mask < P:
        if vr & mask:
            dest = ((vr - mask) + root) % P
            _send_acc(ctx, coll, dest=dest, tag=tg_base + int(math.log2(mask)), meta=meta)
            meta["mask"] = P
            _set_part(j, cid, rr, state="sent", meta=meta)
            break
        child = vr + mask
        if child < P:
            cw = (child + root) % P
            if _child_alive(ctx, cw):
                got = _recv_operand(ctx, coll, cw, tg_base + int(math.log2(mask)), deadline)
                if got is None:
                    return _await_more(ctx, cid, [cw])
                return _do_merge(
                    ctx,
                    coll,
                    o,
                    meta,
                    right_obj=got,
                    right_from=cw,
                    round_=int(math.log2(mask)),
                    operand_budget=operand_budget,
                    next_mask=mask << 1,
                )
            # Fault-tolerant tree: a failed child's subtree is dropped, and the
            # omission is recorded so the harness can decide whether the result
            # is still acceptable.
            with j.tx() as c:
                j.trace(
                    "coll_drop_subtree",
                    rank=ctx.rank,
                    epoch=ctx.epoch,
                    comm=ctx.comm,
                    coll=cid,
                    peer=cw,
                    status="child_failed",
                    conn=c,
                )
            meta.setdefault("dropped", []).append(cw)
        mask <<= 1
        meta["mask"] = mask
        _set_part(j, cid, rr, meta=meta)

    if vr == 0:
        acc = str(meta.get("acc"))
        _close(j, cid, result_obj=acc)
    if all_:
        obj = _await_result_obj(ctx, cid, deadline, who=f"root rank {root}")
        env = _present(ctx, obj, materialize=materialize, budget=budget, what="allreduce result")
        _set_part(j, cid, rr, state="done", out_obj=obj, meta=meta, done=True)
        env.update({"coll": cid, "complete": True, "algo": algo, "op": o.name, "merges": merges,
                    "dropped": meta.get("dropped", [])})
        return env
    if vr == 0:
        obj = str(meta.get("acc"))
        env = _present(ctx, obj, materialize=materialize, budget=budget, what="reduce result")
        _set_part(j, cid, rr, state="done", out_obj=obj, meta=meta, done=True)
        env.update({"coll": cid, "complete": True, "algo": algo, "op": o.name, "merges": merges,
                    "dropped": meta.get("dropped", [])})
        return env
    _set_part(j, cid, rr, state="done", meta=meta, done=True)
    return {
        "coll": cid,
        "role": "contributor",
        "complete": True,
        "algo": algo,
        "merges": merges,
        "note": f"your accumulator was folded upward; the result lands at root rank {root}",
    }


def _reduce_chain(
    ctx: Ctx,
    coll: sqlite3.Row,
    o: ops_mod.Op,
    *,
    meta: Dict[str, Any],
    deadline: int,
    all_: bool,
    root: int,
    materialize: Optional[bool],
    budget: Optional[int],
    operand_budget: Optional[int],
) -> Dict[str, Any]:
    """Linear-chain reduction: P-1 operator applications, all serialised.

    State is a single flag in ``meta`` -- whether we have already folded our
    successor's accumulator into our own -- which keeps the schedule resumable
    across an agent-evaluated merge that spans several CLI invocations.
    """
    j = ctx.j
    cid = str(coll["id"])
    P = ctx.size
    rr = ctx.crank
    vr = (rr - root) % P
    tg = internal_tag("reduce", 200)

    if vr + 1 < P and not meta.get("chain_folded"):
        succ = (vr + 1 + root) % P
        if _child_alive(ctx, succ):
            got = _recv_operand(ctx, coll, succ, tg, deadline)
            if got is None:
                return _await_more(ctx, cid, [succ])
            step_meta = dict(meta)
            # Persist the "already folded my successor" flag with the merge, so a
            # resumed invocation does not try to receive from the successor twice.
            step_meta["chain_folded"] = True
            res = _do_merge(
                ctx, coll, o, step_meta, right_obj=got, right_from=succ, round_=vr,
                operand_budget=operand_budget, next_mask=1,
            )
            if res.get("action_required") == "merge":
                return res
            return res
        with j.tx() as c:
            j.trace("coll_drop_subtree", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm,
                    coll=cid, peer=succ, status="successor_failed", conn=c)
        meta.setdefault("dropped", []).append(succ)

    meta["chain_folded"] = True
    if vr > 0:
        _send_acc(ctx, coll, dest=(vr - 1 + root) % P, tag=tg, meta=meta)
        _set_part(j, cid, rr, state="done", meta=meta, done=True)
        if all_:
            obj = _await_result_obj(ctx, cid, deadline, who=f"root rank {root}")
            env = _present(ctx, obj, materialize=materialize, budget=budget, what="allreduce result")
            env.update({"coll": cid, "complete": True, "algo": "chain", "op": o.name,
                        "merges": int(meta.get("merges", 0))})
            return env
        return {"coll": cid, "role": "contributor", "complete": True, "algo": "chain",
                "merges": int(meta.get("merges", 0)),
                "note": f"your accumulator was folded and passed toward root rank {root}"}
    _close(j, cid, result_obj=str(meta["acc"]))
    env = _present(ctx, str(meta["acc"]), materialize=materialize, budget=budget,
                   what="reduce result")
    _set_part(j, cid, rr, state="done", out_obj=str(meta["acc"]), meta=meta, done=True)
    env.update({"coll": cid, "complete": True, "algo": "chain", "op": o.name,
                "merges": int(meta.get("merges", 0)), "dropped": meta.get("dropped", [])})
    return env


def _reduce_recursive_doubling(
    ctx: Ctx,
    coll: sqlite3.Row,
    o: ops_mod.Op,
    *,
    meta: Dict[str, Any],
    deadline: int,
    root: int,
    materialize: Optional[bool],
    budget: Optional[int],
    operand_budget: Optional[int],
) -> Dict[str, Any]:
    """Recursive-doubling allreduce.

    Every rank exchanges accumulators with the partner at distance ``2^k`` and
    both apply the operator, so after ``log2 P`` rounds all ranks hold the full
    reduction with no separate broadcast. In MPI this is the standard choice for
    short messages: ``log P`` rounds, and the redundant arithmetic is free.

    For an *agent* operator the arithmetic is emphatically not free: total
    operator applications rise from ``P-1`` (binomial reduce plus broadcast) to
    ``P log P``, at the same critical-path depth. The evaluation measures both,
    because this is the clearest case where transplanting MPI's selection rule
    unchanged would be a mistake -- the algorithm that wins for bytes loses for
    agents.

    Merge order is pinned by rank (lower rank is the left operand) so that all
    ranks obtain byte-identical results even for a non-commutative operator.
    """
    j = ctx.j
    cid = str(coll["id"])
    P = ctx.size
    rr = ctx.crank
    if P & (P - 1):
        raise ArgError(
            f"recursive_doubling currently requires a power-of-two communicator size (got {P})",
            hint="use --algo reduce_bcast, or size the communicator to a power of two",
        )
    mask = int(meta.get("mask", 1))
    while mask < P:
        partner = rr ^ mask
        tg = internal_tag("reduce", 64 + int(math.log2(mask)))
        if not _child_alive(ctx, partner):
            # No fault-tolerant variant of recursive doubling exists that keeps
            # the same result on all ranks, so we fail loudly rather than return
            # silently different answers to different ranks.
            raise ProcFailedError(
                f"recursive_doubling partner rank {partner} has failed at round {int(math.log2(mask))}",
                hint="revoke and shrink, then retry with --algo reduce_bcast, which tolerates gaps",
                detail={"failed": [partner]},
            )
        _send_acc(ctx, coll, dest=partner, tag=tg, meta=meta)
        got = _recv_operand(ctx, coll, partner, tg, deadline)
        if got is None:
            return _await_more(ctx, cid, [partner])
        left_obj, right_obj = (str(meta["acc"]), got) if rr < partner else (got, str(meta["acc"]))
        step_meta = dict(meta)
        step_meta["acc"] = left_obj
        res = _do_merge(
            ctx, coll, o, step_meta, right_obj=right_obj, right_from=partner,
            round_=int(math.log2(mask)), operand_budget=operand_budget, next_mask=mask << 1,
        )
        if res.get("action_required") == "merge":
            return res
        # Runtime operator: _do_merge recursed and completed the schedule.
        return res
    if rr == 0:
        _close(j, cid, result_obj=str(meta["acc"]))
    env = _present(ctx, str(meta["acc"]), materialize=materialize, budget=budget,
                   what="allreduce result")
    _set_part(j, cid, rr, state="done", out_obj=str(meta["acc"]), meta=meta, done=True)
    env.update({"coll": cid, "complete": True, "algo": "recursive_doubling", "op": o.name,
                "merges": int(meta.get("merges", 0)),
                "rounds": int(math.log2(P)) if P > 1 else 0})
    return env


def _send_acc(ctx: Ctx, coll: sqlite3.Row, *, dest: int, tag: int, meta: Dict[str, Any]) -> None:
    acc = str(meta["acc"])
    p2p.send(
        ctx,
        dest,
        tag,
        ctx.j.object_text(acc),
        kind="coll",
        coll=str(coll["id"]),
        idem=f"{coll['id']}:acc:{ctx.crank}->{dest}:{tag}",
        force_payload_mode="rendezvous",
    )


def _recv_operand(ctx: Ctx, coll: sqlite3.Row, src: int, tag: int, deadline: int) -> Optional[str]:
    try:
        env = p2p.recv(ctx, src, tag, timeout_ns=max(1, deadline - now_ns()), materialize=False)
    except AmpiError as exc:
        if exc.err_class in (ErrClass.TIMEOUT,):
            return None
        if exc.err_class == ErrClass.PROC_FAILED:
            return None
        raise
    return str(env["handle"])


def _await_more(ctx: Ctx, cid: str, waiting_on: List[int]) -> Dict[str, Any]:
    raise TimeoutError_(
        f"still waiting for contributions from rank(s) {waiting_on} in collective {cid}",
        hint="your accumulator is checkpointed; re-run the identical command to resume the reduction",
        detail={"coll": cid, "waiting_on": waiting_on},
    )


def _do_merge(
    ctx: Ctx,
    coll: sqlite3.Row,
    o: ops_mod.Op,
    meta: Dict[str, Any],
    *,
    right_obj: str,
    right_from: int,
    round_: int,
    operand_budget: Optional[int],
    next_mask: int,
) -> Dict[str, Any]:
    """Apply one operator instance, either in-runtime or by an agent."""
    j = ctx.j
    cid = str(coll["id"])
    rr = ctx.crank
    left_obj = str(meta["acc"])
    if o.fn is not None:
        merged = ops_mod.apply_op(o, j.object_text(left_obj), j.object_text(right_obj))
        with j.tx() as c:
            pay = package(j, merged, creator=ctx.rank, cfg=ctx.cfg, conn=c)
        meta["acc"] = pay.obj
        meta["mask"] = next_mask
        meta["merges"] = int(meta.get("merges", 0)) + 1
        _set_part(j, cid, rr, meta=meta, rounds=int(meta["merges"]))
        # Continue the schedule immediately.
        return _reduce_tree(
            ctx,
            coll,
            o,
            deadline=now_ns() + ctx.cfg.timeout_ns,
            all_=(str(coll["op"]) == "allreduce"),
            root=int(coll["root"] or 0),
            materialize=None,
            budget=None,
            operand_budget=operand_budget,
        )
    sid = "s:" + uuid.uuid4().hex[:10]
    with j.tx() as c:
        c.execute(
            "INSERT INTO reduce_step(id,coll,crank,round,left_obj,right_obj,left_from,right_from,"
            "state,issued_ns) VALUES(?,?,?,?,?,?,?,?,'pending',?)",
            (sid, cid, rr, round_, left_obj, right_obj, rr, right_from, now_ns()),
        )
        meta["mask"] = next_mask
        c.execute(
            "UPDATE coll_part SET state='reducing', meta=? WHERE coll=? AND crank=?",
            (json.dumps(meta, ensure_ascii=False), cid, rr),
        )
        j.trace(
            "reduce_step_issue",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            coll=cid,
            peer=right_from,
            status=o.name,
            detail={"round": round_, "step": sid},
            conn=c,
        )
    step = j.q1("SELECT * FROM reduce_step WHERE id=?", (sid,))
    assert step is not None
    return _describe_step(ctx, coll, step)


def _describe_step(ctx: Ctx, coll: sqlite3.Row, step: sqlite3.Row) -> Dict[str, Any]:
    """Write the two operands to files and tell the agent what to do.

    Operands are written as files rather than printed, and the *context ledger
    is charged as if the agent read them*, because it will. Pretending that
    file reads are free is the easiest way to build an agent system that
    silently exhausts its context window.
    """
    j = ctx.j
    cid = str(coll["id"])
    work = _work_dir(ctx, cid)
    params = json.loads(coll["params"] or "{}")
    ob = params.get("operand_budget")
    left_p = work / f"r{ctx.crank}_round{step['round']}_left.txt"
    right_p = work / f"r{ctx.crank}_round{step['round']}_right.txt"
    out_p = work / f"r{ctx.crank}_round{step['round']}_merged.txt"
    charged = 0
    for oid, path in ((str(step["left_obj"]), left_p), (str(step["right_obj"]), right_p)):
        meta = j.object_meta(oid)
        if ob and int(meta["tokens"]) > int(ob):
            view = views_mod.render_view(j, oid, {"op": "headtail", "budget": int(ob)})
            path.write_text(view["body"], encoding="utf-8")
            charged += view["tokens"]
        else:
            path.write_text(j.object_text(oid), encoding="utf-8")
            charged += int(meta["tokens"])
    with j.tx() as c:
        ctx_charge(j, ctx.rank, ctx.epoch, charged, conn=c, force=True, what="reduction operands")
    op_label = str(coll["reduce_op"] or "agent:merge").split(":", 1)[-1]
    return {
        "coll": cid,
        "complete": False,
        "action_required": "merge",
        "step": str(step["id"]),
        "round": int(step["round"]),
        "op": str(coll["reduce_op"]),
        "op_label": op_label,
        "left_file": str(left_p),
        "right_file": str(right_p),
        "left_from": int(step["left_from"]) if step["left_from"] is not None else None,
        "right_from": int(step["right_from"]) if step["right_from"] is not None else None,
        "suggested_out": str(out_p),
        "context_charged": charged,
        "directive": (
            f"REDUCTION STEP (round {step['round']}, operator '{op_label}'). You are an internal "
            f"node of the reduction tree. Combine the two operands into ONE result of the SAME "
            f"shape:\n  left  (yours, rank {step['left_from']}): {left_p}\n"
            f"  right (from rank {step['right_from']}): {right_p}\n"
            f"Write the combined result to {out_p}, then run:\n"
            f"  ampi reduce commit --step {step['id']} --in @{out_p}\n"
            "The combined result may be handed to you again at the next round, so keep it "
            "self-contained and do not lose information that later rounds will need."
        ),
    }


def reduce_commit(
    ctx: Ctx,
    step_id: str,
    text: str,
    *,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
    timeout_ns: Optional[int] = None,
) -> Dict[str, Any]:
    """Commit an agent-evaluated merge and resume the reduction schedule."""
    j = ctx.j
    step = j.q1("SELECT * FROM reduce_step WHERE id=?", (step_id,))
    if step is None:
        raise ArgError(f"unknown reduction step {step_id!r}")
    if int(step["crank"]) != ctx.crank:
        raise ArgError(f"reduction step {step_id} belongs to rank {step['crank']}, not you")
    coll = j.q1("SELECT * FROM coll WHERE id=?", (step["coll"],))
    assert coll is not None
    if step["state"] == "committed":
        merged_obj = str(step["out_obj"])
    else:
        with j.tx() as c:
            pay = package(j, text, creator=ctx.rank, cfg=ctx.cfg, conn=c)
            c.execute(
                "UPDATE reduce_step SET state='committed', out_obj=?, committed_ns=? WHERE id=?",
                (pay.obj, now_ns(), step_id),
            )
            j.trace(
                "reduce_step_commit",
                rank=ctx.rank,
                epoch=ctx.epoch,
                comm=ctx.comm,
                coll=str(coll["id"]),
                tokens=pay.tokens,
                dur_ns=now_ns() - int(step["issued_ns"]),
                detail={"round": int(step["round"]), "step": step_id},
                conn=c,
            )
        merged_obj = pay.obj
    part = j.q1("SELECT * FROM coll_part WHERE coll=? AND crank=?", (step["coll"], ctx.crank))
    assert part is not None
    meta = _part_meta(part)
    meta["acc"] = merged_obj
    meta["merges"] = int(meta.get("merges", 0)) + 1
    _set_part(j, str(coll["id"]), ctx.crank, meta=meta, rounds=int(meta["merges"]), state="joined")
    o = ops_mod.get_op(str(coll["reduce_op"]))
    return _reduce_tree(
        ctx,
        coll,
        o,
        deadline=now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns),
        all_=(str(coll["op"]) == "allreduce"),
        root=int(coll["root"] or 0),
        materialize=materialize,
        budget=budget,
        operand_budget=json.loads(coll["params"] or "{}").get("operand_budget"),
    )


# --------------------------------------------------------------------------
# Gather / Allgather / Scatter / Alltoall
# --------------------------------------------------------------------------


def gather(
    ctx: Ctx,
    *,
    text: str,
    root: int = 0,
    all_: bool = False,
    label: Optional[str] = None,
    algo: Optional[str] = None,
    quorum: Optional[float] = None,
    timeout_ns: Optional[int] = None,
    budget: Optional[int] = None,
    materialize: Optional[bool] = None,
) -> Dict[str, Any]:
    """``AMPI_Gather`` / ``AMPI_Allgather``.

    Gather is where naive agent harnesses die: the result is ``P`` times the size
    of one contribution, so at ``P=48`` a 2000-token contribution produces a
    96k-token result that no rank can read. AgentMPI's answer is that gather
    returns a *manifest of handles* by default -- one line per contributor with
    its rank, token count and structural summary -- and the caller then chooses
    which contributions to materialise, or asks for a per-contribution view
    budget. The classical protocol had no need for this because memory is not
    attention.
    """
    kind = "allgather" if all_ else "gather"
    coll, part = join(ctx, kind, label=label, root=root, algo=algo, quorum=quorum, params={"root": root})
    cid = str(coll["id"])
    rr = ctx.crank
    algo = str(coll["algo"])
    deadline = _deadline(ctx, coll, timeout_ns)
    t0 = now_ns()
    if not _part_meta(part).get("published"):
        with ctx.j.tx() as c:
            pay = package(ctx.j, text, creator=ctx.rank, cfg=ctx.cfg, conn=c)
        m = _part_meta(part)
        m["published"] = True
        _set_part(ctx.j, cid, rr, in_obj=pay.obj, meta=m)

    if algo in ("ring", "recursive_doubling"):
        _allgather_pairwise(ctx, coll, algo=algo, deadline=deadline)

    if not all_ and rr != root:
        _set_part(ctx.j, cid, rr, state="done", done=True)
        return {"coll": cid, "role": "contributor", "complete": True, "algo": algo}

    q = float(coll["quorum"])
    start = time.time()
    prog = p2p.Progress(ctx)
    livec = LiveCache(ctx)
    while True:
        prog()
        live = livec()
        need = max(1, math.ceil(q * len(live)))
        rows = ctx.j.q(
            "SELECT crank,in_obj FROM coll_part WHERE coll=? AND in_obj IS NOT NULL ORDER BY crank",
            (cid,),
        )
        have = [(int(r["crank"]), str(r["in_obj"])) for r in rows]
        if len(have) >= need:
            break
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_{'All' if all_ else ''}gather: {len(have)}/{need} contributions arrived",
                hint="your contribution is recorded; re-run to keep waiting",
                detail={"have": len(have), "need": need, "missing": sorted(set(live) - {k for k, _ in have})},
            )
        p2p._poll_sleep(time.time() - start)

    manifest = _gather_manifest(ctx, have, budget=budget, materialize=materialize)
    _set_part(ctx.j, cid, rr, state="done", done=True)
    if not ctx.j.scalar("SELECT result_obj FROM coll WHERE id=?", (cid,)):
        with ctx.j.tx() as c:
            pay = package(
                ctx.j,
                json.dumps({"contributions": [{"rank": k, "handle": v} for k, v in have]}, indent=2),
                creator=ctx.rank,
                cfg=ctx.cfg,
                conn=c,
            )
        _close(ctx.j, cid, result_obj=pay.obj)
    with ctx.j.tx() as c:
        ctx.j.trace(
            "coll_exit",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            coll=cid,
            phase="exit",
            status=kind,
            tokens=manifest["context_charged"],
            dur_ns=now_ns() - t0,
            detail={"algo": algo, "n": len(have)},
            conn=c,
        )
    manifest.update({"coll": cid, "complete": True, "algo": algo, "count": len(have)})
    return manifest


def _gather_manifest(
    ctx: Ctx,
    have: Sequence[Tuple[int, str]],
    *,
    budget: Optional[int],
    materialize: Optional[bool],
) -> Dict[str, Any]:
    j = ctx.j
    items: List[Dict[str, Any]] = []
    total = sum(int(j.object_meta(o)["tokens"]) for _, o in have)
    per = None
    if budget and have:
        per = max(60, int(budget) // len(have))
    charged = 0
    for crank, oid in have:
        meta = j.object_meta(oid)
        item: Dict[str, Any] = {
            "rank": crank,
            "world": comm_to_world(j, ctx.comm, crank),
            "handle": oid,
            "tokens": int(meta["tokens"]),
            "summary": meta["summary"],
        }
        if materialize and per is None:
            item["body"] = j.object_text(oid)
            charged += int(meta["tokens"])
        elif per is not None:
            view = views_mod.render_view(j, oid, {"op": "headtail", "budget": per})
            item["body"] = view["body"]
            item["clipped"] = int(meta["tokens"]) > per
            charged += view["tokens"]
        else:
            charged += 25
        items.append(item)
    with j.tx() as c:
        ctx_charge(j, ctx.rank, ctx.epoch, charged, conn=c, force=True, what="gather manifest")
    out: Dict[str, Any] = {
        "items": items,
        "total_payload_tokens": total,
        "context_charged": charged,
    }
    if per is None and not materialize:
        out["note"] = (
            f"{len(items)} contributions totalling {total} tokens were NOT read into your context. "
            "Read the ones you need with `ampi view <handle>`, or re-run with "
            "`--budget N` to get clipped bodies for all of them."
        )
    return out


def _allgather_pairwise(ctx: Ctx, coll: sqlite3.Row, *, algo: str, deadline: int) -> None:
    """Ring / recursive-doubling allgather over handle manifests.

    The payload exchanged is a manifest, not the bodies, so the doubling does not
    blow up context. This is the concrete form of the paper's argument that
    AgentMPI's data plane should move references and let each rank decide what to
    pay for.
    """
    j = ctx.j
    cid = str(coll["id"])
    P = ctx.size
    rr = ctx.crank
    known: Dict[int, str] = {}
    row = j.q1("SELECT in_obj FROM coll_part WHERE coll=? AND crank=?", (cid, rr))
    if row is not None and row["in_obj"]:
        known[rr] = str(row["in_obj"])
    if algo == "ring":
        for k in range(P - 1):
            tg = internal_tag("allgather", k)
            right = (rr + 1) % P
            left = (rr - 1) % P
            p2p.send(ctx, right, tg, json.dumps(known), kind="coll", coll=cid, coll_round=k,
                     idem=f"{cid}:ag:{rr}:{k}")
            env = p2p.recv(ctx, left, tg, timeout_ns=max(1, deadline - now_ns()), materialize=True)
            known.update({int(a): b for a, b in json.loads(env.get("body") or "{}").items()})
    else:
        rounds = math.ceil(math.log2(P)) if P > 1 else 0
        for k in range(rounds):
            partner = rr ^ (1 << k)
            if partner >= P:
                continue
            tg = internal_tag("allgather", 16 + k)
            p2p.send(ctx, partner, tg, json.dumps(known), kind="coll", coll=cid, coll_round=k,
                     idem=f"{cid}:rd:{rr}:{k}")
            env = p2p.recv(ctx, partner, tg, timeout_ns=max(1, deadline - now_ns()), materialize=True)
            known.update({int(a): b for a, b in json.loads(env.get("body") or "{}").items()})
    with j.tx() as c:
        for crank, oid in known.items():
            c.execute(
                "INSERT INTO coll_part(coll,crank,state,in_obj,joined_ns) VALUES(?,?,'joined',?,?)"
                " ON CONFLICT(coll,crank) DO UPDATE SET in_obj=COALESCE(coll_part.in_obj,excluded.in_obj)",
                (cid, crank, oid, now_ns()),
            )


def scatter(
    ctx: Ctx,
    *,
    root: int = 0,
    parts: Optional[Sequence[str]] = None,
    label: Optional[str] = None,
    algo: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Scatter``: the root hands rank i its i-th slice.

    This is the workhorse of agent harnesses -- it is how a manager assigns work
    -- and giving it a collective name rather than leaving it to ad-hoc prompt
    construction buys two things: the assignment is recorded in the journal (so a
    replacement agent can be told exactly what its predecessor owned), and slices
    are handles (so assigning a 40k-token chapter costs the manager nothing).
    """
    coll, part = join(ctx, "scatter", label=label, root=root, algo=algo, params={"root": root})
    cid = str(coll["id"])
    rr = ctx.crank
    P = ctx.size
    deadline = _deadline(ctx, coll, timeout_ns)
    if rr == root:
        if parts is None:
            raise ArgError("the root of AMPI_Scatter must supply --parts (one per rank)")
        if len(parts) != P:
            raise ArgError(f"scatter needs exactly {P} parts, got {len(parts)}")
        with ctx.j.tx() as c:
            handles = []
            for i, p in enumerate(parts):
                pay = package(ctx.j, p, creator=ctx.rank, cfg=ctx.cfg, label=f"scatter:{i}", conn=c)
                handles.append(pay.obj)
            for i, h in enumerate(handles):
                c.execute(
                    "INSERT INTO coll_part(coll,crank,state,out_obj,joined_ns) VALUES(?,?,'joined',?,?)"
                    " ON CONFLICT(coll,crank) DO UPDATE SET out_obj=excluded.out_obj",
                    (cid, i, h, now_ns()),
                )
            manifest = package(
                ctx.j, json.dumps({"parts": handles}, indent=2), creator=ctx.rank, cfg=ctx.cfg, conn=c
            )
        _close(ctx.j, cid, result_obj=manifest.obj)
    start = time.time()
    prog = p2p.Progress(ctx)
    while True:
        row = ctx.j.q1("SELECT out_obj FROM coll_part WHERE coll=? AND crank=?", (cid, rr))
        if row is not None and row["out_obj"]:
            obj = str(row["out_obj"])
            break
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_Scatter: root rank {root} has not published slices yet",
                hint="re-run to keep waiting",
            )
        prog()
        p2p._poll_sleep(time.time() - start)
    env = _present(ctx, obj, materialize=materialize, budget=budget, what="scatter slice")
    _set_part(ctx.j, cid, rr, state="done", done=True)
    env.update({"coll": cid, "complete": True, "index": rr, "root": root})
    return env


def alltoall(
    ctx: Ctx,
    *,
    parts: Sequence[str],
    label: Optional[str] = None,
    algo: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    budget: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Alltoall``: rank i sends its j-th part to rank j.

    The natural agent use is a review round: every rank produces one note per
    peer and receives one note from every peer. Its ``Theta(P^2)`` message count
    is exactly as unscalable here as in MPI, which is a useful thing for a
    harness author to be told by the interface rather than to discover.
    """
    coll, part = join(ctx, "alltoall", label=label, algo=algo)
    cid = str(coll["id"])
    rr = ctx.crank
    P = ctx.size
    if len(parts) != P:
        raise ArgError(f"alltoall needs exactly {P} parts, got {len(parts)}")
    deadline = _deadline(ctx, coll, timeout_ns)
    tg = internal_tag("alltoall", 0)
    for j_ in range(P):
        if j_ == rr:
            continue
        if not _child_alive(ctx, j_):
            continue
        p2p.send(ctx, j_, tg, parts[j_], kind="coll", coll=cid, idem=f"{cid}:a2a:{rr}->{j_}")
    got: Dict[int, Dict[str, Any]] = {rr: {"body": parts[rr], "source": rr}}
    live = [x for x in _live_members(ctx) if x != rr]
    for _ in live:
        try:
            env = p2p.recv(ctx, ANY_SOURCE, tg, timeout_ns=max(1, deadline - now_ns()),
                           materialize=None, budget=budget)
        except AmpiError as exc:
            if exc.err_class in (ErrClass.TIMEOUT, ErrClass.PROC_FAILED, ErrClass.PROC_FAILED_PENDING):
                break
            raise
        got[int(env["source"])] = env
    _set_part(ctx.j, cid, rr, state="done", done=True)
    return {
        "coll": cid,
        "complete": len(got) >= len(live) + 1,
        "received": [{"from": k, **{kk: vv for kk, vv in v.items() if kk in ("body", "handle", "tokens")}}
                     for k, v in sorted(got.items())],
        "expected": len(live) + 1,
    }


# --------------------------------------------------------------------------
# Scan / Exscan
# --------------------------------------------------------------------------


def scan(
    ctx: Ctx,
    *,
    op: str,
    text: str,
    exclusive: bool = False,
    label: Optional[str] = None,
    algo: Optional[str] = None,
    commute: Optional[bool] = None,
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
    operand_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Scan`` / ``AMPI_Exscan``: prefix reduction.

    Exclusive scan is the sleeper hit of the agent setting. "Give rank i a
    summary of everything ranks 0..i-1 produced" is precisely what a
    sequentially-consistent artefact needs -- the translator of chapter 12 needs
    the terminology decisions of chapters 1..11, and nothing else. In MPI,
    ``MPI_Exscan`` is a niche numerical routine; in AgentMPI it is the canonical
    way to propagate order-dependent context without serialising the whole job.
    """
    kind = "exscan" if exclusive else "scan"
    o = ops_mod.get_op(op, commute=commute)
    coll, part = join(
        ctx, kind, label=label, reduce_op=o.name, algo=algo,
        params={"exclusive": exclusive, "operand_budget": operand_budget},
    )
    cid = str(coll["id"])
    rr = ctx.crank
    deadline = _deadline(ctx, coll, timeout_ns)
    algo = str(coll["algo"])
    m = _part_meta(part)
    if not m.get("mine"):
        with ctx.j.tx() as c:
            pay = package(ctx.j, text, creator=ctx.rank, cfg=ctx.cfg, conn=c)
        m["mine"] = pay.obj
        _set_part(ctx.j, cid, rr, in_obj=pay.obj, meta=m)

    if o.fn is not None:
        # Runtime operator: fold prefixes directly out of the journal once the
        # predecessors have published. O(1) rounds.
        start = time.time()
        prog = p2p.Progress(ctx)
        while True:
            rows = ctx.j.q(
                "SELECT crank,in_obj FROM coll_part WHERE coll=? AND in_obj IS NOT NULL AND crank<=?"
                " ORDER BY crank",
                (cid, rr),
            )
            have = {int(r["crank"]): str(r["in_obj"]) for r in rows}
            need = list(range(0, rr if exclusive else rr + 1))
            missing = [k for k in need if k not in have]
            dead = set(_dead_locals(ctx))
            missing = [k for k in missing if k not in dead]
            if not missing:
                vals = [ctx.j.object_text(have[k]) for k in need if k in have]
                if not vals:
                    _set_part(ctx.j, cid, rr, state="done", done=True)
                    return {"coll": cid, "complete": True, "identity": True,
                            "note": "no predecessors: exclusive scan yields the operator identity"}
                folded = ops_mod.reduce_sequence(o, vals)
                with ctx.j.tx() as c:
                    pay = package(ctx.j, folded, creator=ctx.rank, cfg=ctx.cfg, conn=c)
                env = _present(ctx, pay.obj, materialize=materialize, budget=budget, what="scan prefix")
                _set_part(ctx.j, cid, rr, state="done", out_obj=pay.obj, done=True)
                env.update({"coll": cid, "complete": True, "algo": "journal", "prefix_of": need})
                return env
            if now_ns() > deadline:
                raise TimeoutError_(
                    f"AMPI_{'Ex' if exclusive else ''}scan: waiting on predecessor rank(s) {missing[:8]}",
                    hint="re-run to keep waiting",
                    detail={"missing": missing},
                )
            prog()
            p2p._poll_sleep(time.time() - start)

    # Agent operator: chain schedule. Rank i receives the running prefix from
    # i-1, merges its own contribution in, and passes the new prefix to i+1.
    return _scan_chain(ctx, coll, o, exclusive=exclusive, deadline=deadline,
                       materialize=materialize, budget=budget, operand_budget=operand_budget)


def _dead_locals(ctx: Ctx) -> List[int]:
    dead = set(failed_ranks(ctx.j, ctx.comm))
    return [i for i, w in enumerate(comm_members(ctx.j, ctx.comm)) if w in dead]


def _scan_chain(
    ctx: Ctx,
    coll: sqlite3.Row,
    o: ops_mod.Op,
    *,
    exclusive: bool,
    deadline: int,
    materialize: Optional[bool],
    budget: Optional[int],
    operand_budget: Optional[int],
) -> Dict[str, Any]:
    j = ctx.j
    cid = str(coll["id"])
    rr = ctx.crank
    P = ctx.size
    tg = internal_tag("scan", 0)
    part = j.q1("SELECT * FROM coll_part WHERE coll=? AND crank=?", (cid, rr))
    assert part is not None
    m = _part_meta(part)

    open_step = j.q1(
        "SELECT * FROM reduce_step WHERE coll=? AND crank=? AND state='pending' LIMIT 1", (cid, rr)
    )
    if open_step is not None:
        return _describe_step(ctx, coll, open_step)

    if "prefix_in" not in m:
        if rr == 0:
            m["prefix_in"] = None
        else:
            try:
                env = p2p.recv(ctx, rr - 1, tg, timeout_ns=max(1, deadline - now_ns()), materialize=False)
            except AmpiError as exc:
                if exc.err_class == ErrClass.TIMEOUT:
                    raise TimeoutError_(
                        f"AMPI_Exscan: rank {rr - 1} has not passed the running prefix yet",
                        hint="re-run to resume; your own contribution is already recorded",
                    ) from exc
                raise
            m["prefix_in"] = str(env["handle"])
        _set_part(j, cid, rr, meta=m)

    my_out_prefix: Optional[str]
    if m["prefix_in"] is None:
        my_out_prefix = str(m["mine"])
        result_obj = None if exclusive else str(m["mine"])
    else:
        if not m.get("combined"):
            step_meta = dict(m)
            step_meta["acc"] = str(m["prefix_in"])
            res = _do_merge(
                ctx, coll, o, step_meta,
                right_obj=str(m["mine"]), right_from=rr, round_=rr,
                operand_budget=operand_budget, next_mask=1 << 30,
            )
            if res.get("action_required") == "merge":
                m["awaiting_merge"] = True
                _set_part(j, cid, rr, meta=m)
                return res
            my_out_prefix = str(step_meta["acc"])
            m["combined"] = my_out_prefix
            _set_part(j, cid, rr, meta=m)
        my_out_prefix = str(m["combined"])
        result_obj = str(m["prefix_in"]) if exclusive else my_out_prefix

    if rr + 1 < P and _child_alive(ctx, rr + 1):
        p2p.send(ctx, rr + 1, tg, j.object_text(str(my_out_prefix)), kind="coll", coll=cid,
                 idem=f"{cid}:scan:{rr}->{rr+1}", force_payload_mode="rendezvous")
    _set_part(j, cid, rr, state="done", out_obj=result_obj, meta=m, done=True)
    if result_obj is None:
        return {"coll": cid, "complete": True, "identity": True, "algo": "chain",
                "note": "rank 0 has no predecessors; exclusive scan yields the identity"}
    env = _present(ctx, result_obj, materialize=materialize, budget=budget, what="scan prefix")
    env.update({"coll": cid, "complete": True, "algo": "chain"})
    return env


def _resume_scan_after_commit(ctx: Ctx, coll: sqlite3.Row) -> Dict[str, Any]:
    o = ops_mod.get_op(str(coll["reduce_op"]))
    params = json.loads(coll["params"] or "{}")
    part = ctx.j.q1("SELECT * FROM coll_part WHERE coll=? AND crank=?", (coll["id"], ctx.crank))
    assert part is not None
    m = _part_meta(part)
    m["combined"] = m.get("acc") or m.get("combined")
    m.pop("awaiting_merge", None)
    _set_part(ctx.j, str(coll["id"]), ctx.crank, meta=m)
    return _scan_chain(
        ctx, coll, o,
        exclusive=bool(params.get("exclusive")),
        deadline=now_ns() + ctx.cfg.timeout_ns,
        materialize=None, budget=None,
        operand_budget=params.get("operand_budget"),
    )


def reduce_scatter(
    ctx: Ctx,
    *,
    op: str,
    parts: Sequence[str],
    label: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
) -> Dict[str, Any]:
    """``AMPI_Reduce_scatter``: reduce elementwise, then scatter the i-th result.

    The agent reading of this is a divided-review pattern: every rank has an
    opinion about every section, all opinions on section i are merged, and rank i
    receives only the merged verdict on its own section. It keeps each rank's
    context proportional to its own responsibility rather than to the whole
    artefact, which is the only way a review round scales past a handful of
    ranks.
    """
    o = ops_mod.get_op(op)
    if o.fn is None:
        raise OpError("reduce_scatter currently supports runtime operators only",
                      hint="use a built-in op, or express the pattern as gather + per-rank reduce")
    coll, part = join(ctx, "reduce_scatter", label=label, reduce_op=o.name)
    cid = str(coll["id"])
    rr = ctx.crank
    P = ctx.size
    if len(parts) != P:
        raise ArgError(f"reduce_scatter needs exactly {P} parts, got {len(parts)}")
    deadline = _deadline(ctx, coll, timeout_ns)
    with ctx.j.tx() as c:
        pay = package(ctx.j, json.dumps(list(parts), ensure_ascii=False), creator=ctx.rank,
                      cfg=ctx.cfg, conn=c)
    _set_part(ctx.j, cid, rr, in_obj=pay.obj)
    start = time.time()
    prog = p2p.Progress(ctx)
    while True:
        rows = ctx.j.q("SELECT crank,in_obj FROM coll_part WHERE coll=? AND in_obj IS NOT NULL", (cid,))
        live = set(_live_members(ctx))
        have = {int(r["crank"]): str(r["in_obj"]) for r in rows}
        if live.issubset(set(have)):
            break
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_Reduce_scatter: {len(have)}/{len(live)} contributions arrived",
                hint="re-run to keep waiting",
            )
        prog()
        p2p._poll_sleep(time.time() - start)
    column = []
    for crank in sorted(have):
        vec = json.loads(ctx.j.object_text(have[crank]))
        if rr < len(vec):
            column.append(vec[rr])
    folded = ops_mod.reduce_sequence(o, column) if column else ""
    with ctx.j.tx() as c:
        pay2 = package(ctx.j, folded, creator=ctx.rank, cfg=ctx.cfg, conn=c)
    _set_part(ctx.j, cid, rr, state="done", out_obj=pay2.obj, done=True)
    env = _present(ctx, pay2.obj, materialize=materialize, budget=None, what="reduce_scatter result")
    env.update({"coll": cid, "complete": True, "contributors": sorted(have), "index": rr})
    return env


# --------------------------------------------------------------------------
# Nonblocking collectives
# --------------------------------------------------------------------------


def icollective(ctx: Ctx, op: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """``AMPI_Ibarrier`` / ``AMPI_Ibcast`` / ...: register intent, return a request.

    Nonblocking collectives matter more here than in MPI. A strict barrier over
    heavy-tailed agents wastes the fast ranks' time; an ``AMPI_Ibarrier`` lets a
    rank announce arrival, keep working on something independent, and complete
    the barrier later. This is Hoefler's argument for nonblocking collectives,
    with the overlap window measured in minutes rather than microseconds.
    """
    rid = p2p.new_request_id()
    label = params.get("label")
    coll, _ = join(
        ctx,
        op,
        label=label,
        reduce_op=params.get("op"),
        root=params.get("root"),
        algo=params.get("algo"),
        quorum=params.get("quorum"),
        params={k: v for k, v in params.items() if k not in ("text",)},
    )
    if params.get("text") is not None:
        with ctx.j.tx() as c:
            pay = package(ctx.j, str(params["text"]), creator=ctx.rank, cfg=ctx.cfg, conn=c)
        _set_part(ctx.j, str(coll["id"]), ctx.crank, in_obj=pay.obj)
    with ctx.j.tx() as c:
        c.execute(
            "INSERT INTO request(id,job,rank,epoch,op,comm,state,coll,created_ns,params)"
            " VALUES(?,?,?,?,?,?,'active',?,?,?)",
            (rid, ctx.j.job_id, ctx.rank, ctx.epoch, "i" + op, ctx.comm, str(coll["id"]),
             now_ns(), json.dumps(params, ensure_ascii=False)),
        )
    return {"request": rid, "coll": str(coll["id"]), "op": op, "state": "active"}


def test_collective(ctx: Ctx, rid: str) -> Dict[str, Any]:
    row = ctx.j.q1("SELECT * FROM request WHERE id=?", (rid,))
    if row is None:
        raise ArgError(f"unknown request {rid!r}")
    cid = str(row["coll"])
    coll = ctx.j.q1("SELECT * FROM coll WHERE id=?", (cid,))
    if coll is None:
        raise ArgError(f"request {rid} references an unknown collective")
    op = str(coll["op"])
    params = json.loads(row["params"] or "{}")
    if op == "barrier":
        q = float(coll["quorum"])
        live = _live_members(ctx)
        need = max(1, math.ceil(q * len(live)))
        n = int(ctx.j.scalar("SELECT COUNT(*) FROM coll_part WHERE coll=?", (cid,), 0))
        if n >= need or coll["state"] == "closed":
            _close(ctx.j, cid)
            p2p._complete_request(ctx, rid, {"released": True, "arrived": n})
            return {"request": rid, "complete": True, "arrived": n, "need": need}
        return {"request": rid, "complete": False, "arrived": n, "need": need}
    res = ctx.j.scalar("SELECT result_obj FROM coll WHERE id=?", (cid,))
    if res:
        env = _present(ctx, str(res), materialize=params.get("materialize"),
                       budget=params.get("budget"), what=f"i{op} result")
        p2p._complete_request(ctx, rid, env)
        return {"request": rid, "complete": True, **env}
    return {"request": rid, "complete": False}
