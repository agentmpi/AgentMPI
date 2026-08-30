"""Collective operations and their algorithms.

Collectives are where MPI's real engineering lives.  ``MPI_Bcast`` is one
line of specification and, in MPICH, several thousand lines of algorithm
selection: flat, binomial, k-nomial, pipelined chain, and van de Geijn's
scatter-plus-allgather, each optimal in a different region of the (message
size, process count, network) space.  The standard exposes none of that;
the application says *what* it wants and the implementation decides *how*.

AgentMPI inherits both the interface and the machinery, but the cost model
underneath is different enough that the *answers* change.  In HPC:

    T = alpha * (messages on the critical path) + beta * (bytes moved)

with ``alpha`` around a microsecond and ``beta`` around a nanosecond per
byte.  For agents, moving a payload is a file write, but *reading* it costs
an agent turn -- tens of seconds and a permanent bite out of a context
window.  The cost model becomes

    T = gamma * (turns on the critical path) + alpha * (messages) + beta * (tokens)

with ``gamma`` five to seven orders of magnitude larger than ``alpha``, and
with a hard *feasibility* constraint that no rank may ingest more than its
remaining context.  Two consequences run through this module:

1. **Latency-optimal algorithms win almost everywhere.**  The HPC crossover
   where ring algorithms beat logarithmic ones at large message sizes
   essentially disappears, because bandwidth is nearly free and turns are
   nearly everything.  A ring allreduce over 64 agents costs 126 sequential
   turns; recursive doubling costs 6.
2. **Feasibility, not just cost, decides.**  Recursive doubling doubles the
   data each rank holds every round, so at round *i* each rank ingests
   ``2^i`` contributions.  With agents that is not a bandwidth cost, it is a
   wall: past ``log2(C/s)`` rounds the rank simply cannot read its input.
   So the selection logic must first ask "does this fit?" and only then ask
   "is this fast?".  That question has no counterpart in MPI, and it is why
   :func:`select_algorithm` takes a context budget.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Sequence

from .constants import TAG_UB, CollAlgorithm, InternalTag
from .context import plan_reduction, safe_fanout
from .datatypes import JSON_, TEXT, TypeDescriptor, lookup
from .errors import ArgError, ContractError, OpError, RootError
from .ops import Op, check_op_for_tree, lookup_op
from .tokens import count_tokens
from .trace import Event

# --------------------------------------------------------------------------
# Internal tagging
# --------------------------------------------------------------------------
_PHASE_BITS = 5
_COLL_BASE = int(InternalTag.COLL)


def _next_coll_id(comm, name: str = "?") -> int:
    """Advance this communicator's collective counter and record what it was.

    Kept in durable runtime state for the same reason as the message
    sequence counter: when each collective is a separate ``ampi`` invocation,
    an in-memory counter would restart at zero every time and successive
    collectives would collide on the same tag.

    Recording the *name* alongside the number is what makes a skipped
    collective diagnosable.  Every internal envelope carries the pair, and a
    rank that receives ``(#4, "exscan")`` when it executed ``#4`` as
    ``"scatterv"`` knows immediately that a peer has fallen out of step --
    rather than waiting forever for a tag that peer will never use.
    """
    rt = comm.runtime
    key = comm.context

    # Resynchronise after a crash *inside* a collective, using evidence
    # rather than a guess.
    #
    # A rank restarted mid-collective faces a question it cannot answer
    # locally: re-enter that collective, or move past it? Both answers are
    # wrong half the time. Re-entering when the group already finished puts
    # this rank permanently one behind; skipping when the group is still
    # waiting strands every peer. The awkward case is a rootless barrier,
    # where a rank's dissemination messages can satisfy every peer before the
    # rank itself returns, so the barrier completes for the group and leaves
    # no local record.
    #
    # The peers' durable logs settle it. If any peer recorded the collective
    # as complete, it completed; move on. If none did, nobody got past it;
    # re-enter with the same identifier so the tags still line up.
    pending = rt.pending_collective(key)
    if pending is not None and pending[1] == name:
        cid, _ = pending
        finished_by = rt.peers_completed(comm, cid)
        if finished_by:
            rt.complete_collective(key, cid)
            rt.profiler.note(
                "skipping an interrupted collective the group already finished",
                collective=name, cid=cid, evidence=finished_by)
        else:
            comm._coll_counter = cid
            comm._active_coll = (name, cid)
            rt.profiler.note(
                "re-entering an interrupted collective no peer completed",
                collective=name, cid=cid)
            return cid

    nxt = rt.coll_counter.get(key, 0) + 1
    rt.coll_counter[key] = nxt
    comm._coll_counter = nxt
    rt.record_collective(comm.context, nxt, name)
    comm._active_coll = (name, nxt)
    # Persist the counter *before* the collective it labels, not after.
    #
    # This is the write-ahead logging argument, and skipping it cost us a
    # live run. A rank whose process is killed part way through a collective
    # -- an agent's shell timing out is the usual cause -- has already sent
    # messages tagged with this counter. If the increment was only persisted
    # at exit, the rank's next process reuses the same number, replays the
    # collective its peers have already completed, and is thereafter one
    # behind them forever. Nothing detects it, either: the replayed operation
    # has the same *name* as the one the peers ran at that number, so it looks
    # like an ordinary slow peer rather than a divergence.
    comm.runtime.save_state()
    return nxt


def _coll_meta(comm, cid: int) -> dict[str, Any]:
    name, _ = getattr(comm, "_active_coll", ("?", cid))
    return {"c": name, "i": cid}


def _tag(coll_id: int, phase: int = 0) -> int:
    """A tag unique to one collective invocation and phase.

    Successive collectives on the same communicator must not match each
    other's traffic.  MPI implementations solve this with a hidden context
    id per collective; we derive the separation from the per-communicator
    collective counter, which every rank advances identically because MPI's
    (and AgentMPI's) rule that collectives be issued in the same order on
    every rank makes the counter a replicated value.
    """
    return _COLL_BASE + ((coll_id & 0xFFFF) << _PHASE_BITS) + (phase & ((1 << _PHASE_BITS) - 1))


def _emit(comm, op: str, algorithm: str, steps: int, t0: float, **detail: Any) -> None:
    active = getattr(comm, "_active_coll", None)
    comm._active_coll = None
    if active is not None:
        comm.runtime.complete_collective(comm.context, active[1])
    comm.runtime.profiler.emit(
        Event(kind="coll", ts=time.time(), rank=comm.runtime.world_rank, op=op,
              context=comm.context, algorithm=algorithm, dur=time.time() - t0,
              turn=comm.runtime.turn, detail={"steps": steps, "size": comm.size, **detail})
    )


def _tokens_of(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return count_tokens(value)
    return count_tokens(json.dumps(value, default=str, ensure_ascii=False))


# --------------------------------------------------------------------------
# Algorithm selection (the MCA "decision function")
# --------------------------------------------------------------------------

@dataclass
class Selection:
    algorithm: CollAlgorithm
    rationale: str
    predicted_steps: int
    predicted_peak_ingest: int
    feasible: bool = True

    def __str__(self) -> str:
        return (f"{self.algorithm.value} ({self.predicted_steps} steps, "
                f"peak ingest {self.predicted_peak_ingest} tok): {self.rationale}")


def select_algorithm(
    collective: str,
    size: int,
    item_tokens: int,
    budget: int,
    *,
    op: Op | None = None,
    requested: CollAlgorithm | str = CollAlgorithm.AUTO,
) -> Selection:
    """Choose a collective algorithm, and say why.

    Returning the rationale is not decoration.  MPI's algorithm selection is
    invisible, which is tolerable because every choice computes the same
    answer.  Here the choice can change the *result* -- a tree reduction with
    a summarising operator loses different information than a flat one -- so
    the selection must be inspectable and recorded in the trace.
    """
    req = CollAlgorithm(requested) if isinstance(requested, str) else requested
    log2p = max(int(math.ceil(math.log2(max(size, 2)))), 1)
    k = safe_fanout(budget, max(item_tokens, 1))

    if req is not CollAlgorithm.AUTO:
        return Selection(req, "explicitly requested by the harness", log2p,
                         item_tokens * max(k, 1))

    if collective == "barrier":
        return Selection(
            CollAlgorithm.DISSEMINATION,
            "barrier carries no payload, so the only cost is rounds; the "
            "dissemination barrier needs ceil(log2 p) rounds for any p, "
            "including non-powers of two",
            log2p, 0,
        )

    if collective == "bcast":
        if size <= 4:
            return Selection(CollAlgorithm.FLAT,
                             "at this size the root's p-1 sends cost less than "
                             "the extra tree rounds", 1, item_tokens)
        return Selection(
            CollAlgorithm.BINOMIAL,
            f"binomial tree gives {log2p} rounds instead of {size - 1}; each "
            f"participant ingests the message exactly once, so the tree is "
            f"context-neutral",
            log2p, item_tokens,
        )

    if collective in ("gather", "scatter"):
        if item_tokens * (size - 1) <= budget:
            return Selection(CollAlgorithm.BINOMIAL,
                             "intermediate nodes can hold their subtree's data, "
                             "so a tree is both feasible and log-depth",
                             log2p, item_tokens * max(k, 1))
        return Selection(
            CollAlgorithm.FLAT,
            f"a tree would require intermediate nodes to hold up to "
            f"{item_tokens * (size - 1)} tokens, exceeding the {budget}-token "
            f"budget; the flat algorithm keeps all payload at the root, which "
            f"can spill to the payload plane",
            size - 1, item_tokens * (size - 1),
        )

    if collective == "allgather":
        total = item_tokens * (size - 1)
        if total > budget:
            return Selection(
                CollAlgorithm.RING, "allgather is infeasible: its definition "
                f"requires every rank to hold all {total} tokens, which exceeds "
                f"the {budget}-token budget; use allreduce with a contracting "
                f"operator, or a bounded datatype",
                size - 1, total, feasible=False,
            )
        return Selection(
            CollAlgorithm.RECURSIVE_DOUBLING,
            f"recursive doubling completes in {log2p} rounds versus {size - 1} "
            f"for the ring, and the total ingest is identical, so the ring's "
            f"only advantage (lower peak bandwidth) is worthless here",
            log2p, total,
        )

    if collective in ("reduce", "allreduce"):
        contracting = op is not None and op.contracting
        out_tokens = op.output_tokens if (op and op.output_tokens) else item_tokens
        if op is not None and not op.associative:
            return Selection(
                CollAlgorithm.FLAT,
                f"operator {op.name} is not associative, so any tree would "
                f"compute a different result; the reduction must be flat",
                1, item_tokens * (size - 1),
            )
        if not contracting:
            flat_ingest = item_tokens * (size - 1)
            if flat_ingest <= budget:
                return Selection(
                    CollAlgorithm.FLAT,
                    f"operator is not contracting, so a tree's peak ingest grows "
                    f"with depth; the flat reduction fits ({flat_ingest} tokens "
                    f"at the root)",
                    1, flat_ingest,
                )
            return Selection(
                CollAlgorithm.FLAT,
                f"operator is not contracting and the flat reduction needs "
                f"{flat_ingest} tokens at the root, over the {budget}-token "
                f"budget; declare an output bound on the operator to enable a "
                f"capacity-aware tree",
                1, flat_ingest, feasible=False,
            )
        plan = plan_reduction(size, item_tokens, budget, output_tokens=out_tokens)
        if collective == "reduce":
            return Selection(
                CollAlgorithm.KNOMIAL,
                f"contracting operator (bound {out_tokens} tok) admits a "
                f"{plan.fanout}-ary tree: {plan.rounds} rounds, peak ingest "
                f"{plan.peak_ingest} tokens, independent of p",
                plan.rounds, plan.peak_ingest, feasible=plan.feasible,
            )
        return Selection(
            CollAlgorithm.RECURSIVE_DOUBLING if out_tokens * log2p <= budget
            else CollAlgorithm.RABENSEIFNER,
            f"contracting operator; recursive doubling costs {log2p} rounds with "
            f"peak ingest {out_tokens * log2p} tokens",
            log2p, out_tokens * log2p,
        )

    if collective in ("scan", "exscan"):
        return Selection(
            CollAlgorithm.RECURSIVE_DOUBLING,
            f"parallel prefix in {log2p} rounds rather than the {size - 1} "
            f"rounds of the sequential chain; this is the single largest "
            f"structural win available to a harness with a carried dependency",
            log2p, item_tokens * log2p,
        )

    if collective == "alltoall":
        total = item_tokens * (size - 1)
        if total > budget:
            return Selection(CollAlgorithm.PAIRWISE,
                             f"alltoall requires every rank to ingest {total} "
                             f"tokens, over budget", size - 1, total, feasible=False)
        return Selection(CollAlgorithm.BRUCK if size > 8 else CollAlgorithm.PAIRWISE,
                         f"Bruck's algorithm completes in {log2p} rounds at the "
                         f"cost of extra token volume, which is the right trade "
                         f"when turns dominate", log2p, total)

    return Selection(CollAlgorithm.FLAT, "no specialised algorithm", size - 1,
                     item_tokens * size)


# --------------------------------------------------------------------------
# Barrier
# --------------------------------------------------------------------------

def barrier(comm, *, algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
            timeout: float | None = None) -> None:
    """``AMPI_Barrier``.

    The agent reading of a barrier is a *phase boundary*: no rank may begin
    the next phase until every rank has finished the current one.  Harnesses
    need it exactly where MPI programs do -- before reading a window that
    others were writing, between a draft phase and a review phase -- and
    getting it wrong produces the same class of bug, a reader that sees half
    an update.

    The default is the dissemination barrier: ``ceil(log2 p)`` rounds, no
    root, and correct for any *p*.  Its rootlessness matters more here than
    in MPI: a rooted barrier makes the root a single point of failure at the
    exact moment when every other rank is blocked.
    """
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if p == 1:
        return
    sel = select_algorithm("barrier", p, 0, comm.runtime.budget.headroom, requested=algorithm)
    cid = _next_coll_id(comm, "barrier")
    steps = 0
    if sel.algorithm is CollAlgorithm.DISSEMINATION:
        distance = 1
        phase = 0
        while distance < p:
            dst = (rank + distance) % p
            src = (rank - distance + p) % p
            comm.send(1, dst, _tag(cid, phase % 32), JSON_)
            comm.recv(src, _tag(cid, phase % 32), JSON_, timeout=timeout)
            distance <<= 1
            phase += 1
            steps += 1
    else:  # flat: everyone reports to rank 0, rank 0 releases
        if rank == 0:
            for r in range(1, p):
                comm.recv(r, _tag(cid, 0), JSON_, timeout=timeout)
            for r in range(1, p):
                comm.send(1, r, _tag(cid, 1), JSON_)
        else:
            comm.send(1, 0, _tag(cid, 0), JSON_)
            comm.recv(0, _tag(cid, 1), JSON_, timeout=timeout)
        steps = 2
    _emit(comm, "barrier", sel.algorithm.value, steps, t0)


# --------------------------------------------------------------------------
# Broadcast
# --------------------------------------------------------------------------

def bcast(
    comm,
    value: Any = None,
    root: int = 0,
    *,
    datatype: TypeDescriptor | str = JSON_,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
    relay: Any = None,
) -> Any:
    """``AMPI_Bcast``.

    ``relay`` is the one genuinely new parameter.  In MPI an interior node of
    a broadcast tree forwards bytes verbatim.  An agent interior node *can*
    forward verbatim, but it can also adapt the message for its subtree --
    translate it, specialise it, compress it.  Passing a ``relay`` function
    turns the broadcast into what we call a *refracting broadcast*: the same
    log-depth schedule, but each level may transform the payload.

    That is strictly more expressive and strictly more dangerous, and the
    protocol makes the danger visible rather than preventing it.  Every
    envelope carries a ``provenance`` chain, so a receiver can see how many
    times its copy was rewritten, and the trace records the depth.  Our
    experiments measure the resulting fidelity loss directly (Section 7.4):
    with verbatim relay a broadcast is exact at any depth; with an
    LLM relay, agreement with the root's text decays with tree depth, which
    is the quantitative form of the children's game of telephone.
    """
    if not 0 <= root < comm.size:
        raise RootError("root out of range", root=root, size=comm.size)
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if p == 1:
        return value
    item_tokens = _tokens_of(value) if rank == root else 0
    sel = select_algorithm("bcast", p, item_tokens, comm.runtime.budget.headroom,
                           requested=algorithm)
    cid = _next_coll_id(comm, "bcast")
    steps = 0
    depth = 0

    if sel.algorithm is CollAlgorithm.FLAT:
        if rank == root:
            for r in range(p):
                if r != root:
                    comm.send(value, r, _tag(cid, 0), datatype)
            steps = 1
        else:
            value, _st = comm.recv(root, _tag(cid, 0), datatype, timeout=timeout)
            steps = 1
    elif sel.algorithm is CollAlgorithm.CHAIN:
        vrank = (rank - root + p) % p
        if vrank != 0:
            value, _st = comm.recv((rank - 1 + p) % p, _tag(cid, 0), datatype, timeout=timeout)
            depth = vrank
        if vrank != p - 1:
            payload = relay(value, depth) if relay is not None else value
            comm.send(payload, (rank + 1) % p, _tag(cid, 0), datatype)
        steps = p - 1
    else:  # binomial
        vrank = (rank - root + p) % p
        mask = 1
        while mask < p:
            if vrank & mask:
                src = (vrank - mask + root) % p
                value, _st = comm.recv(src, _tag(cid, 0), datatype, timeout=timeout)
                depth = bin(vrank).count("1")
                break
            mask <<= 1
        mask >>= 1
        payload = relay(value, depth) if (relay is not None and depth > 0) else value
        while mask > 0:
            if vrank + mask < p:
                dst = (vrank + mask + root) % p
                comm.send(payload, dst, _tag(cid, 0), datatype)
            mask >>= 1
        value = payload
        steps = max(int(math.ceil(math.log2(p))), 1)

    _emit(comm, "bcast", sel.algorithm.value, steps, t0, root=root, depth=depth,
          refracting=relay is not None, rationale=sel.rationale)
    return value


# --------------------------------------------------------------------------
# Scatter / Gather
# --------------------------------------------------------------------------

def scatter(
    comm, values: Sequence[Any] | None = None, root: int = 0, *,
    datatype: TypeDescriptor | str = JSON_,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
) -> Any:
    """``AMPI_Scatter`` -- hand rank *i* the *i*-th piece of the work."""
    return _scatter_impl(comm, values, root, datatype, algorithm, timeout, variable=False)


def scatterv(
    comm, values: Sequence[Any] | None = None, root: int = 0, *,
    datatype: TypeDescriptor | str = JSON_,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
) -> Any:
    """``AMPI_Scatterv`` -- pieces of unequal size.

    This is the common case, not the exception, when the payload is a
    document: chapters differ in length, modules differ in difficulty, and
    the load imbalance that follows is the dominant scalability limit of an
    embarrassingly parallel agent harness.  The ``v`` form exists so a
    harness can partition by measured cost rather than by count.
    """
    return _scatter_impl(comm, values, root, datatype, algorithm, timeout, variable=True)


def _scatter_impl(comm, values, root, datatype, algorithm, timeout, variable):
    if not 0 <= root < comm.size:
        raise RootError("root out of range", root=root, size=comm.size)
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if rank == root:
        if values is None:
            raise ArgError("root must supply values to scatter")
        values = list(values)
        if len(values) != p and not variable:
            raise ArgError("scatter requires exactly one value per rank",
                           got=len(values), size=p)
        if variable and len(values) != p:
            # Pad or fold: scatterv allows a rank to receive nothing.
            values = list(values) + [None] * (p - len(values)) if len(values) < p else values
    if p == 1:
        return values[0] if values else None

    item_tokens = max((_tokens_of(v) for v in (values or [])), default=0)
    sel = select_algorithm("scatter", p, item_tokens, comm.runtime.budget.headroom,
                           requested=algorithm)
    cid = _next_coll_id(comm, "scatterv" if variable else "scatter")
    mine: Any = None
    steps = 0

    if sel.algorithm is CollAlgorithm.BINOMIAL:
        # Recursive halving: the root keeps the lower half of the virtual
        # rank space and ships the upper half onward, so interior nodes carry
        # their subtree's data.  log-depth, at the price of transient ingest.
        # Recursive halving.  The root starts holding the whole vector in
        # virtual-rank order; at each step a node that holds a block passes
        # the upper half of it to the node half a block away and keeps the
        # lower half.  After log2(p) steps every node holds exactly its own
        # element.  Interior nodes transiently hold a subtree's worth of data,
        # which is why the decision function only picks this when the
        # transient fits.
        vrank = (rank - root + p) % p
        held: list[Any] | None = None
        if rank == root:
            reordered = [values[(v + root) % p] for v in range(p)]
            held = reordered
        mask = 1
        while mask < p:
            mask <<= 1
        mask >>= 1
        block_start = 0
        while mask > 0:
            if held is not None and vrank == block_start:
                partner_v = block_start + mask
                if partner_v < p:
                    keep = partner_v - block_start
                    chunk = held[keep:]
                    if chunk:
                        comm.send(chunk, (partner_v + root) % p, _tag(cid, 0), JSON_)
                        held = held[:keep]
                        steps += 1
            elif held is None and vrank >= block_start + mask:
                if vrank == block_start + mask:
                    held, _st = comm.recv((block_start + root) % p, _tag(cid, 0),
                                          JSON_, timeout=timeout)
                    held = list(held or [])
                    steps += 1
            if vrank >= block_start + mask:
                block_start += mask
            mask >>= 1
        mine = held[0] if held else None
    else:  # flat
        if rank == root:
            for r in range(p):
                if r == root:
                    mine = values[r]
                else:
                    comm.send(values[r], r, _tag(cid, 0), datatype)
            steps = 1
        else:
            mine, _st = comm.recv(root, _tag(cid, 0), datatype, timeout=timeout)
            steps = 1

    # Check the contract on the *item*, not on the block.
    #
    # A tree scatter moves blocks: an interior node receives a slice of the
    # vector and forwards the rest, so the datatype the caller declared
    # describes an element and never gets applied to one. That is how a
    # misdelivered work assignment reached four of our ranks unchallenged
    # even though each payload carried the rank it was addressed to.
    dt = lookup(datatype) if isinstance(datatype, str) else datatype
    if mine is not None:
        violations = dt.check(mine)
        if violations:
            comm.runtime.pvars.inc("contract_violations", len(violations))
            if comm.runtime.cvars["ampi_strict_contracts"]:
                raise ContractError(
                    "scattered item does not satisfy its declared contract",
                    violations=violations, rank=rank, root=root)
            comm.runtime.profiler.note("scattered item violates its contract",
                                       violations=list(violations))

    _emit(comm, "scatterv" if variable else "scatter", sel.algorithm.value, steps, t0,
          root=root, rationale=sel.rationale)
    return mine


def gather(
    comm, value: Any, root: int = 0, *,
    datatype: TypeDescriptor | str = JSON_,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
) -> list[Any] | None:
    """``AMPI_Gather`` -- collect one contribution per rank at the root.

    A gather is the most context-hostile collective in the catalogue: by
    definition the root ends up holding everything.  The runtime therefore
    lets the payloads travel on the payload plane (blob references) and only
    materialises them when the harness actually reads them, which is the
    difference between a root that can gather 200 chapter translations and
    one that dies at 12.
    """
    if not 0 <= root < comm.size:
        raise RootError("root out of range", root=root, size=comm.size)
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if p == 1:
        return [value]
    item_tokens = _tokens_of(value)
    sel = select_algorithm("gather", p, item_tokens, comm.runtime.budget.headroom,
                           requested=algorithm)
    cid = _next_coll_id(comm, "gather")
    steps = 0
    result: list[Any] | None = None

    if sel.algorithm is CollAlgorithm.BINOMIAL:
        vrank = (rank - root + p) % p
        held: dict[int, Any] = {vrank: value}
        mask = 1
        while mask < p:
            if vrank & mask:
                dst = ((vrank - mask) + root) % p
                comm.send({str(k): v for k, v in held.items()}, dst, _tag(cid, 0), JSON_)
                steps += 1
                break
            partner_v = vrank | mask
            if partner_v < p:
                src = (partner_v + root) % p
                incoming, _st = comm.recv(src, _tag(cid, 0), JSON_, timeout=timeout)
                for k, v in (incoming or {}).items():
                    held[int(k)] = v
                steps += 1
            mask <<= 1
        if vrank == 0:
            result = [held.get((r - root + p) % p) for r in range(p)]
    else:
        if rank == root:
            slots: dict[int, Any] = {root: value}
            for _ in range(p - 1):
                v, st = comm.recv(-1, _tag(cid, 0), datatype, timeout=timeout)
                slots[st.source] = v
            result = [slots.get(r) for r in range(p)]
            steps = 1
        else:
            comm.send(value, root, _tag(cid, 0), datatype)
            steps = 1

    _emit(comm, "gather", sel.algorithm.value, steps, t0, root=root,
          rationale=sel.rationale)
    return result


def allgather(
    comm, value: Any, *,
    datatype: TypeDescriptor | str = JSON_,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
) -> list[Any]:
    """``AMPI_Allgather`` -- everybody ends up with everybody's contribution.

    Worth a warning label.  Allgather is the collective a naive harness
    reaches for ("let all the agents see all the drafts") and it is the one
    that most reliably exhausts context: its peak ingest is ``(p-1) * s`` at
    *every* rank, so it is the only collective whose feasibility degrades
    linearly in the number of agents.  When the runtime predicts that it will
    not fit, it says so with an actionable message rather than letting the
    agents quietly truncate.
    """
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if p == 1:
        return [value]
    item_tokens = _tokens_of(value)
    sel = select_algorithm("allgather", p, item_tokens, comm.runtime.budget.headroom,
                           requested=algorithm)
    if not sel.feasible:
        comm.runtime.profiler.note("allgather predicted infeasible", rationale=sel.rationale)
    if sel.algorithm is CollAlgorithm.RECURSIVE_DOUBLING and (p & (p - 1)) != 0:
        # Recursive doubling is only defined on powers of two.  MPI
        # implementations handle the remainder by folding the extra ranks
        # into a power-of-two core and redistributing afterwards; Bruck's
        # algorithm reaches the same ceil(log2 p) round count for arbitrary p
        # without the special case, which is the better trade here because
        # its extra token volume costs nothing in this cost model.
        sel = Selection(CollAlgorithm.BRUCK,
                        f"p={p} is not a power of two; Bruck's algorithm gives the "
                        f"same logarithmic round count without a remainder phase",
                        sel.predicted_steps, sel.predicted_peak_ingest, sel.feasible)
    cid = _next_coll_id(comm, "allgather")
    steps = 0

    if sel.algorithm is CollAlgorithm.RECURSIVE_DOUBLING and (p & (p - 1)) == 0:
        held = {rank: value}
        distance = 1
        phase = 0
        while distance < p:
            partner = rank ^ distance
            comm.send({str(k): v for k, v in held.items()}, partner, _tag(cid, phase % 32), JSON_)
            incoming, _st = comm.recv(partner, _tag(cid, phase % 32), JSON_, timeout=timeout)
            for k, v in (incoming or {}).items():
                held[int(k)] = v
            distance <<= 1
            phase += 1
            steps += 1
        result = [held.get(r) for r in range(p)]
    elif sel.algorithm is CollAlgorithm.BRUCK:
        held = [value] + [None] * (p - 1)   # held[i] holds rank (rank+i) % p
        distance = 1
        phase = 0
        while distance < p:
            count = min(distance, p - distance)
            dst = (rank - distance + p) % p
            src = (rank + distance) % p
            comm.send(held[:count], dst, _tag(cid, phase % 32), JSON_)
            incoming, _st = comm.recv(src, _tag(cid, phase % 32), JSON_, timeout=timeout)
            for i, v in enumerate(incoming or []):
                if distance + i < p:
                    held[distance + i] = v
            distance <<= 1
            phase += 1
            steps += 1
        result = [held[(r - rank + p) % p] for r in range(p)]
    elif sel.algorithm is CollAlgorithm.RING:
        held = [None] * p
        held[rank] = value
        left = (rank - 1 + p) % p
        right = (rank + 1) % p
        send_idx = rank
        for step in range(p - 1):
            comm.send({"idx": send_idx, "v": held[send_idx]}, right, _tag(cid, step % 32), JSON_)
            msg, _st = comm.recv(left, _tag(cid, step % 32), JSON_, timeout=timeout)
            held[int(msg["idx"])] = msg["v"]
            send_idx = int(msg["idx"])
            steps += 1
        result = held
    else:  # flat: gather to 0 then broadcast
        gathered = gather(comm, value, 0, datatype=datatype,
                          algorithm=CollAlgorithm.FLAT, timeout=timeout)
        result = bcast(comm, gathered, 0, datatype=JSON_,
                       algorithm=CollAlgorithm.BINOMIAL, timeout=timeout)
        steps = 2

    _emit(comm, "allgather", sel.algorithm.value, steps, t0, rationale=sel.rationale,
          feasible=sel.feasible)
    return list(result)


def allgather_raw(comm, value: Any) -> list[Any]:
    """Allgather used by the runtime itself (communicator construction).

    Uses the flat algorithm unconditionally so that it cannot recurse into
    algorithm selection, which would need a communicator that does not exist
    yet.  MPI implementations have the same bootstrap constraint.
    """
    p, rank = comm.size, comm.rank
    if p == 1:
        return [value]
    cid = _next_coll_id(comm, "allgather_raw")
    slots: dict[int, Any] = {rank: value}
    for r in range(p):
        if r != rank:
            comm.send(value, r, _tag(cid, 0), JSON_)
    for _ in range(p - 1):
        v, st = comm.recv(-1, _tag(cid, 0), JSON_)
        slots[st.source] = v
    return [slots.get(r) for r in range(p)]


# --------------------------------------------------------------------------
# Reductions
# --------------------------------------------------------------------------

def reduce(
    comm, value: Any, op: Op | str, root: int = 0, *,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
) -> Any:
    """``AMPI_Reduce``.

    The interesting parameter is ``op``, because the operator's declared
    algebra decides which schedules are legal (see :mod:`agentmpi.ops`).  A
    non-associative operator forces a flat reduction; a non-contracting one
    forbids deep trees; a commutative, idempotent, contracting one admits any
    tree and tolerates duplicate delivery, which makes it the operator class
    to prefer when the executors are unreliable.
    """
    operation = lookup_op(op)
    if not 0 <= root < comm.size:
        raise RootError("root out of range", root=root, size=comm.size)
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if p == 1:
        return value
    item_tokens = _tokens_of(value)
    sel = select_algorithm("reduce", p, item_tokens, comm.runtime.budget.headroom,
                           op=operation, requested=algorithm)
    cid = _next_coll_id(comm, "reduce")
    steps = 0
    result: Any = None

    if sel.algorithm is CollAlgorithm.FLAT:
        check_op_for_tree(operation, 1)
        if rank == root:
            contributions: dict[int, Any] = {rank: value}
            for _ in range(p - 1):
                v, st = comm.recv(-1, _tag(cid, 0), datatype, timeout=timeout)
                contributions[st.source] = v
            ordered = [contributions[r] for r in sorted(contributions)]
            result = operation.apply(ordered)
            steps = 1
        else:
            comm.send(value, root, _tag(cid, 0), datatype)
            steps = 1
    else:
        plan = plan_reduction(p, item_tokens, comm.runtime.budget.headroom,
                              output_tokens=operation.output_tokens)
        check_op_for_tree(operation, max(plan.rounds, 1))
        # k-ary reduction tree, with k chosen so that no interior node ever
        # ingests more than its context budget allows.  At stride s, ranks
        # whose virtual index is a multiple of k*s collect from the k-1
        # siblings at s, 2s, ..., (k-1)s and then move up a level; everyone
        # else has already forwarded and left the tree.
        vrank = (rank - root + p) % p
        k = max(plan.fanout, 2)
        acc = value
        stride = 1
        phase = 0
        while stride < p:
            group_size = stride * k
            if vrank % group_size == 0:
                gathered = [acc]
                for j in range(1, k):
                    child_v = vrank + j * stride
                    if child_v < p:
                        src = (child_v + root) % p
                        v, _st = comm.recv(src, _tag(cid, phase % 32), datatype,
                                           timeout=timeout)
                        gathered.append(v)
                        steps += 1
                if len(gathered) > 1:
                    acc = operation.apply(gathered)
            else:
                parent_v = vrank - (vrank % group_size)
                comm.send(acc, (parent_v + root) % p, _tag(cid, phase % 32), datatype)
                steps += 1
                acc = None
                break
            stride = group_size
            phase += 1
        result = acc if vrank == 0 else None

    _emit(comm, "reduce", sel.algorithm.value, steps, t0, root=root, reduce_op=operation.name,
          rationale=sel.rationale)
    return result


def allreduce(
    comm, value: Any, op: Op | str, *,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
) -> Any:
    """``AMPI_Allreduce`` -- reduce, and give every rank the result.

    This is the collective behind every "the agents deliberate and converge"
    pattern: N-version redundancy with a vote, multi-agent debate, consensus
    on a design decision.  Writing it as an allreduce rather than as a chat
    loop makes the cost explicit and the algorithm swappable, which is
    exactly the argument MPI made for ``MPI_Allreduce`` over hand-rolled
    exchanges in 1993.
    """
    operation = lookup_op(op)
    t0 = time.time()
    p, rank = comm.size, comm.rank
    if p == 1:
        return value
    item_tokens = _tokens_of(value)
    sel = select_algorithm("allreduce", p, item_tokens, comm.runtime.budget.headroom,
                           op=operation, requested=algorithm)
    cid = _next_coll_id(comm, "allreduce")
    steps = 0

    if sel.algorithm is CollAlgorithm.RECURSIVE_DOUBLING and (p & (p - 1)) == 0 \
            and operation.associative and operation.commute:
        acc = value
        distance = 1
        phase = 0
        while distance < p:
            partner = rank ^ distance
            comm.send(acc, partner, _tag(cid, phase % 32), datatype)
            incoming, _st = comm.recv(partner, _tag(cid, phase % 32), datatype, timeout=timeout)
            # Order the pair deterministically so that every rank computes an
            # identical result even when the operator is order sensitive at
            # the margins; without this, an allreduce over agents can end with
            # the participants disagreeing about what they agreed on.
            pair = [acc, incoming] if rank < partner else [incoming, acc]
            acc = operation.apply(pair)
            distance <<= 1
            phase += 1
            steps += 1
        result = acc
    elif sel.algorithm is CollAlgorithm.RING and operation.associative:
        acc = value
        for step in range(p - 1):
            comm.send(acc, (rank + 1) % p, _tag(cid, step % 32), datatype)
            incoming, _st = comm.recv((rank - 1 + p) % p, _tag(cid, step % 32),
                                      datatype, timeout=timeout)
            acc = operation.apply([acc, incoming])
            steps += 1
        result = acc
    else:  # reduce + broadcast; also the required path for non-associative ops
        reduced = reduce(comm, value, operation, 0, algorithm=sel.algorithm
                         if sel.algorithm is not CollAlgorithm.RABENSEIFNER
                         else CollAlgorithm.KNOMIAL,
                         timeout=timeout, datatype=datatype)
        result = bcast(comm, reduced, 0, datatype=JSON_, timeout=timeout)
        steps = 2

    _emit(comm, "allreduce", sel.algorithm.value, steps, t0, reduce_op=operation.name,
          rationale=sel.rationale)
    return result


def reduce_scatter(
    comm, values: Sequence[Any], op: Op | str, *,
    timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
) -> Any:
    """``AMPI_Reduce_scatter`` -- reduce element-wise, scatter the result.

    Each rank contributes a vector of ``p`` items and receives the reduction
    of the ``i``-th component.  For agents the natural reading is
    *distributed responsibility*: every reviewer comments on every module,
    and each module owner receives the merged commentary for their module
    only -- which keeps each owner's ingest at ``O(p)`` comments rather than
    ``O(p^2)``.
    """
    operation = lookup_op(op)
    p, rank = comm.size, comm.rank
    if len(values) != p:
        raise ArgError("reduce_scatter needs one contribution per rank",
                       got=len(values), size=p)
    t0 = time.time()
    if p == 1:
        return values[0]
    cid = _next_coll_id(comm, "reduce_scatter")
    inbox: list[Any] = [values[rank]]
    for r in range(p):
        if r != rank:
            comm.send(values[r], r, _tag(cid, 0), datatype)
    contributions: dict[int, Any] = {rank: values[rank]}
    for _ in range(p - 1):
        v, st = comm.recv(-1, _tag(cid, 0), datatype, timeout=timeout)
        contributions[st.source] = v
    ordered = [contributions[r] for r in sorted(contributions)]
    result = operation.apply(ordered)
    _emit(comm, "reduce_scatter", "pairwise", p - 1, t0, reduce_op=operation.name)
    return result


def alltoall(
    comm, values: Sequence[Any], *,
    timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
) -> list[Any]:
    """``AMPI_Alltoall`` -- rank *i* sends ``values[j]`` to rank *j*.

    The canonical agent use is cross review: every author sends every
    reviewer the artifact that reviewer should look at, and receives the
    ``p`` artifacts assigned to it.  The pairwise-exchange schedule below
    matches MPI's: at step *s*, rank *i* exchanges with rank ``i XOR s``,
    which guarantees that no rank is the target of more than one message per
    step -- an anti-hotspot property that matters even more with agents,
    because a rank receiving eight messages at once must ingest all eight.
    """
    p, rank = comm.size, comm.rank
    if len(values) != p:
        raise ArgError("alltoall needs one value per rank", got=len(values), size=p)
    t0 = time.time()
    if p == 1:
        return [values[0]]
    item_tokens = max((_tokens_of(v) for v in values), default=0)
    sel = select_algorithm("alltoall", p, item_tokens, comm.runtime.budget.headroom,
                           requested=algorithm)
    cid = _next_coll_id(comm, "alltoall")
    received: list[Any] = [None] * p
    received[rank] = values[rank]
    steps = 0
    for step in range(1, p):
        partner = rank ^ step if (p & (p - 1)) == 0 else (rank + step) % p
        recv_from = partner if (p & (p - 1)) == 0 else (rank - step + p) % p
        if partner >= p:
            continue
        comm.send(values[partner], partner, _tag(cid, step % 32), datatype)
        v, _st = comm.recv(recv_from, _tag(cid, step % 32), datatype, timeout=timeout)
        received[recv_from] = v
        steps += 1
    _emit(comm, "alltoall", sel.algorithm.value, steps, t0, rationale=sel.rationale)
    return received


# --------------------------------------------------------------------------
# Prefix operations
# --------------------------------------------------------------------------

def scan(
    comm, value: Any, op: Op | str, *,
    algorithm: CollAlgorithm | str = CollAlgorithm.AUTO,
    timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
    inclusive: bool = True,
) -> Any:
    """``AMPI_Scan`` / ``AMPI_Exscan`` -- parallel prefix.

    Scan is the most under-appreciated collective in MPI and, we argue, the
    most valuable one for agents, because it is the primitive that breaks
    *carried dependencies*.

    The motivating case is concrete.  Translating a book in parallel is
    embarrassingly parallel only if the chunks are independent, and they are
    not: chapter *i* must use the terminology, register, and named-entity
    choices established by chapters 1..*i*-1, or the result reads like it was
    written by forty different people, which it was.  The obvious fix -- run
    the chapters in order, threading a glossary through -- reintroduces a
    fully sequential dependency and throws away all the parallelism.

    A scan does both.  With a glossary-merge operator, ``exscan`` gives
    chapter *i* the merged glossary of all chapters before it in
    ``ceil(log2 p)`` rounds instead of ``p-1``.  For 40 chapters that is 6
    rounds instead of 39: the carried dependency survives, and the harness is
    still parallel.  We measure exactly this in Section 7.2.

    The implementation is Hillis-Steele inclusive scan, which requires the
    operator to be associative but not commutative -- the standard structure
    of a prefix network.
    """
    operation = lookup_op(op)
    p, rank = comm.size, comm.rank
    t0 = time.time()
    if p == 1:
        return value if inclusive else operation.identity
    if not operation.associative:
        raise OpError(f"scan requires an associative operator; {operation.name} is not",
                      operation=operation.name)
    sel = select_algorithm("scan", p, _tokens_of(value), comm.runtime.budget.headroom,
                           op=operation, requested=algorithm)
    cid = _next_coll_id(comm, "scan" if inclusive else "exscan")
    steps = 0

    if sel.algorithm is CollAlgorithm.CHAIN:
        # Sequential prefix: p-1 rounds, and the schedule a harness writes by
        # hand when it threads state through its workers one at a time.  Kept
        # as a selectable algorithm precisely so that the experiments can
        # measure what the parallel version saves.
        acc = value
        if rank > 0:
            prefix, _st = comm.recv(rank - 1, _tag(cid, 0), datatype, timeout=timeout)
            acc = operation.apply([prefix, value])
        if rank < p - 1:
            comm.send(acc, rank + 1, _tag(cid, 0), datatype)
        steps = p - 1
        inclusive_value = acc
    else:
        # Hillis-Steele: at distance d, rank r folds in the partial prefix of
        # rank r-d.  Every rank sends the value it held at the *start* of the
        # round, which is what makes the network correct; ceil(log2 p) rounds,
        # and every rank is busy in every round.
        acc = value
        distance = 1
        phase = 0
        while distance < p:
            if rank + distance < p:
                comm.send(acc, rank + distance, _tag(cid, phase % 30), datatype)
            if rank - distance >= 0:
                incoming, _st = comm.recv(rank - distance, _tag(cid, phase % 30),
                                          datatype, timeout=timeout)
                acc = operation.apply([incoming, acc])
            distance <<= 1
            phase += 1
            steps += 1
        inclusive_value = acc

    if inclusive:
        result = inclusive_value
    else:
        # Exclusive prefix by a single shift.  Deriving it from the inclusive
        # scan rather than tracking it inside the network costs one extra
        # round and removes a whole class of off-by-one errors; with agents
        # the extra round is a rounding error against the cost of a turn.
        if rank < p - 1:
            comm.send(inclusive_value, rank + 1, _tag(cid, 31), datatype)
        if rank == 0:
            result = operation.identity
        else:
            result, _st = comm.recv(rank - 1, _tag(cid, 31), datatype, timeout=timeout)
        steps += 1

    _emit(comm, "scan" if inclusive else "exscan", sel.algorithm.value, steps, t0,
          reduce_op=operation.name, rationale=sel.rationale)
    return result


def exscan(comm, value: Any, op: Op | str, **kw: Any) -> Any:
    """``AMPI_Exscan`` -- prefix excluding the caller's own contribution."""
    return scan(comm, value, op, inclusive=False, **kw)


# --------------------------------------------------------------------------
# Neighborhood collectives (MPI-3)
# --------------------------------------------------------------------------

def neighbor_allgather(
    comm, value: Any, *, timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
) -> list[Any]:
    """``AMPI_Neighbor_allgather`` -- exchange only with topological neighbours.

    MPI-3 added neighbourhood collectives because on a stencil code, an
    all-to-all is wasteful when each rank only needs its four neighbours.
    The agent case is identical and even more acute: an "everyone reads
    everyone" review round costs ``O(p^2)`` tokens and exhausts every
    context, while a review *graph* -- each module reviewed by its two
    downstream consumers -- costs ``O(p)`` and is what a human team actually
    does.  The topology is declared once with
    :func:`agentmpi.topology.dist_graph_create` and every neighbourhood
    collective then respects it.
    """
    from .topology import neighbors_of

    srcs, dsts = neighbors_of(comm)
    t0 = time.time()
    cid = _next_coll_id(comm, "neighbor_allgather")
    for d in dsts:
        comm.send(value, d, _tag(cid, 0), datatype)
    got: dict[int, Any] = {}
    for _ in srcs:
        v, st = comm.recv(-1, _tag(cid, 0), datatype, timeout=timeout)
        got[st.source] = v
    _emit(comm, "neighbor_allgather", "graph", 1, t0, degree=len(srcs))
    return [got.get(s) for s in srcs]


def neighbor_alltoall(
    comm, values: Sequence[Any], *, timeout: float | None = None,
    datatype: TypeDescriptor | str = JSON_,
) -> list[Any]:
    """``AMPI_Neighbor_alltoall`` -- a distinct payload per outgoing edge."""
    from .topology import neighbors_of

    srcs, dsts = neighbors_of(comm)
    if len(values) != len(dsts):
        raise ArgError("one value per outgoing neighbour is required",
                       got=len(values), degree=len(dsts))
    t0 = time.time()
    cid = _next_coll_id(comm, "neighbor_alltoall")
    for v, d in zip(values, dsts):
        comm.send(v, d, _tag(cid, 0), datatype)
    got: dict[int, Any] = {}
    for _ in srcs:
        v, st = comm.recv(-1, _tag(cid, 0), datatype, timeout=timeout)
        got[st.source] = v
    _emit(comm, "neighbor_alltoall", "graph", 1, t0, degree=len(srcs))
    return [got.get(s) for s in srcs]
