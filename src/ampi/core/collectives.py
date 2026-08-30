"""Collective operations: schedules, algorithms, and the decision function.

A collective is a named global operation whose *semantics* are fixed by the
standard and whose *algorithm* is chosen by the implementation.  That split is
the reason MPI collectives got thirty years of optimisation for free: the
application says ``MPI_Allreduce`` once, and recursive doubling, Rabenseifner's
reduce-scatter/allgather, the ring algorithm, and hardware offload all become
available without touching a line of application code.  Agent frameworks have
no such split --- "ask all the agents and merge the answers" is written out by
hand at every call site, in one fixed pattern, with no way to substitute a
better one and no way to measure what it cost.

Two things differ from MPI here, and both come from the same source: the
operands are natural-language artifacts and the operator may be a language
model.

**The algebra constrains the algorithm.**  MPI *requires* associativity, and so
may always use a tree.  AgentMPI accepts operators that are not associative and
refuses to use a tree for them; it accepts operators that are not commutative
and refuses to use the ring and Rabenseifner algorithms for them.  The depth of
a semantic allreduce --- and therefore its wall-clock latency and its dollar
cost --- is a direct, predictable consequence of properties the harness author
declared.

**Context is the binding constraint, not bandwidth.**  For a vector of *n*
tokens over *p* agents, recursive doubling makes every rank hold O(n) tokens at
every step; the reduce-scatter family holds O(n/p) through the reduction.  In
MPI that is a bandwidth argument worth a constant factor.  Here it decides
feasibility: when *n* exceeds one agent's context window, recursive doubling
cannot run at all and reduce-scatter can.  ``analyse_allreduce`` encodes this
as an admissibility test, not a cost comparison.

The execution engine is a persisted step machine, in the spirit of LibNBC's
collective schedules.  Each rank's participation compiles to a list of send /
recv / combine steps and the program counter lives in the device, which buys
three things at once: nonblocking collectives, collectives that survive an
agent restart, and the ability to suspend mid-collective for a model upcall and
resume at exactly the right step.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import util
from ..constants import (
    ALGO_AUTO,
    ALGO_BINOMIAL,
    ALGO_BRUCK,
    ALGO_CHAIN,
    ALGO_DISSEMINATION,
    ALGO_LINEAR,
    ALGO_OFFLOAD,
    ALGO_RABENSEIFNER,
    ALGO_RECURSIVE_DOUBLING,
    ALGO_RING,
    AMPI_TAG_INTERNAL_BASE,
    COLL_ALLGATHER,
    COLL_ALLREDUCE,
    COLL_ALLTOALL,
    COLL_BARRIER,
    COLL_BCAST,
    COLL_GATHER,
    COLL_REDUCE,
    COLL_REDUCE_SCATTER,
    COLL_SCAN,
    COLL_SCATTER,
    LIVE_RANK_STATES,
)
from ..errors import AmpiArgError, AmpiCollectiveMismatch, AmpiTimeout
from .ops import Op, get_op, reduce_sequence


class SemanticUpcall(Exception):
    """Suspends a collective pending a model evaluation of the operator.

    The AgentMPI analogue of MPI invoking a user function registered through
    MPI_Op_create --- except the callback cannot run inside the library,
    because evaluating it requires the calling agent's model.  The library
    unwinds to the binding, hands the operands to the agent, and the agent
    re-enters the identical collective call once it has produced a result.  The
    step machine's persisted program counter makes re-entry exact, so the
    suspension is invisible to the other ranks.
    """

    def __init__(self, op_token: str, op: Op, operands: list[Any], step: int,
                 label: str = "") -> None:
        super().__init__(f"semantic operator {op.name} requires evaluation at step {step}")
        self.op_token = op_token
        self.op = op
        self.operands = operands
        self.step = step
        self.label = label

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "op_required",
            "op_token": self.op_token,
            "op": self.op.name,
            "step": self.step,
            "key": self.label,
            "instructions": self.op.prompt,
            "operands": self.operands,
            "next": (
                "Evaluate the operator on the operands, write the result to a file, then run "
                f"`ampi op-submit --op-token {self.op_token} --result-file <path>` and re-issue "
                "the identical collective call to resume it."
            ),
        }


# ===========================================================================
# Cost model
# ===========================================================================


@dataclass(frozen=True)
class CostModel:
    """The agent analogue of the Hockney alpha-beta model.

    ``alpha``  seconds of dispatch latency per communication step
    ``beta``   seconds per token transferred
    ``gamma``  seconds per operator evaluation step on the critical path
    ``ctx``    tokens of context a rank may hold at once

    The fourth term has no Hockney counterpart and is not a cost but a
    constraint: an algorithm whose peak residency exceeds a rank's context
    limit is inadmissible at any price.  Defaults are the values fitted from
    the ping-pong and allreduce microbenchmarks in the evaluation.
    """

    alpha: float = 3.0
    beta: float = 0.0008
    gamma: float = 25.0

    def time(self, depth: int, tokens_moved: int, op_evals_on_path: int) -> float:
        return depth * self.alpha + tokens_moved * self.beta + op_evals_on_path * self.gamma


@dataclass
class AlgorithmCost:
    algo: str
    steps: int
    depth: int
    tokens_per_rank: int
    peak_resident: int
    operator_evals: int
    evals_on_critical_path: int
    admissible: bool = True
    reason: str = ""
    predicted_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def analyse_allreduce(algo: str, p: int, n: int, op: Op, ctx_limit: int) -> AlgorithmCost:
    """Closed-form cost and admissibility of one allreduce algorithm.

    ``n`` is the payload size in tokens and ``p`` the communicator size.  The
    step and volume terms are the standard ones (Thakur, Rabenseifner and
    Gropp, IJHPCA 2005) with bytes replaced by tokens.  The two added columns
    are peak residency, which decides admissibility under a context bound, and
    evaluations on the critical path, which is what an LLM operator actually
    charges for.
    """
    if p <= 1:
        return AlgorithmCost(algo, 0, 0, 0, n, 0, 0)
    lg = max(1, math.ceil(math.log2(p)))
    if algo == ALGO_LINEAR:
        cost = AlgorithmCost(algo, steps=2 * (p - 1), depth=p, tokens_per_rank=n,
                             peak_resident=2 * n, operator_evals=p - 1,
                             evals_on_critical_path=p - 1)
    elif algo == ALGO_BINOMIAL:
        cost = AlgorithmCost(algo, steps=2 * lg, depth=2 * lg, tokens_per_rank=n * lg,
                             peak_resident=2 * n, operator_evals=p - 1,
                             evals_on_critical_path=lg)
    elif algo == ALGO_RECURSIVE_DOUBLING:
        cost = AlgorithmCost(algo, steps=2 * lg, depth=lg, tokens_per_rank=2 * n * lg,
                             peak_resident=2 * n, operator_evals=p * lg,
                             evals_on_critical_path=lg)
    elif algo == ALGO_RING:
        block = max(1, n // p)
        cost = AlgorithmCost(algo, steps=2 * (p - 1), depth=2 * (p - 1),
                             tokens_per_rank=2 * block * (p - 1),
                             peak_resident=3 * block,
                             operator_evals=p - 1, evals_on_critical_path=p - 1)
    elif algo == ALGO_RABENSEIFNER:
        block = max(1, n // p)
        cost = AlgorithmCost(algo, steps=2 * lg, depth=2 * lg,
                             tokens_per_rank=2 * n * (p - 1) // p,
                             peak_resident=max(2 * block, n // 2),
                             operator_evals=p - 1, evals_on_critical_path=lg)
    elif algo == ALGO_OFFLOAD:
        cost = AlgorithmCost(algo, steps=2, depth=2, tokens_per_rank=2 * n,
                             peak_resident=p * n, operator_evals=p - 1,
                             evals_on_critical_path=p - 1)
    else:
        raise AmpiArgError(f"no cost model for allreduce algorithm {algo!r}")

    # Admissibility rule 1: re-association requires a declared associative op.
    if algo in (ALGO_BINOMIAL, ALGO_RECURSIVE_DOUBLING, ALGO_RABENSEIFNER, ALGO_RING,
                ALGO_OFFLOAD) and not op.associative:
        cost.admissible = False
        cost.reason = (
            f"{op.name} is not declared associative, so re-association is unsound; "
            "only canonical linear order is admissible"
        )
        return cost
    # Admissibility rule 2: block-decomposed algorithms reorder operands, so
    # they additionally require commutativity.  MPICH makes the same
    # restriction on Rabenseifner's algorithm for the same reason.
    if algo in (ALGO_RABENSEIFNER, ALGO_RING) and not op.commutative:
        cost.admissible = False
        cost.reason = (
            f"{op.name} is not commutative; reduce-scatter reorders operands within a block"
        )
        return cost
    # Admissibility rule 3: the context bound.
    if cost.peak_resident > ctx_limit:
        cost.admissible = False
        cost.reason = (
            f"peak residency of {cost.peak_resident} tokens exceeds the rank context limit "
            f"of {ctx_limit} tokens"
        )
    return cost


def select_algorithm(
    coll: str, p: int, n: int, op: Op | None, ctx_limit: int, vector: bool
) -> tuple[str, list[dict[str, Any]]]:
    """The AgentMPI decision function.

    Open MPI ships a ``coll tuned`` decision file and MPICH a set of control
    variables; both choose a collective algorithm from message size and
    communicator size.  Ours adds two inputs HPC does not have --- the
    operator's declared algebra and the receiver context bound --- and it
    returns its reasoning, because a harness author who cannot see why their
    semantic allreduce took p rounds instead of lg p cannot fix it.
    """
    considered: list[dict[str, Any]] = []
    if coll == COLL_BARRIER:
        return (ALGO_DISSEMINATION if p > 4 else ALGO_LINEAR), considered
    if coll == COLL_BCAST:
        return (ALGO_BINOMIAL if p > 3 else ALGO_LINEAR), considered
    if coll in (COLL_GATHER, COLL_SCATTER):
        return ALGO_BINOMIAL, considered
    if coll == COLL_ALLGATHER:
        return ALGO_RING, considered
    if coll == COLL_ALLTOALL:
        return ALGO_LINEAR, considered
    if coll == COLL_SCAN:
        return ALGO_CHAIN, considered
    if coll == COLL_REDUCE_SCATTER:
        return ALGO_RING, considered
    if coll in (COLL_REDUCE, COLL_ALLREDUCE):
        if op is None:
            raise AmpiArgError(f"{coll} requires an operator")
        if coll == COLL_REDUCE:
            candidates = [ALGO_BINOMIAL, ALGO_LINEAR]
        else:
            candidates = [ALGO_RABENSEIFNER, ALGO_RING, ALGO_RECURSIVE_DOUBLING,
                          ALGO_BINOMIAL, ALGO_LINEAR]
            if not vector:
                candidates = [c for c in candidates if c not in (ALGO_RABENSEIFNER, ALGO_RING)]
            if p & (p - 1) != 0:
                candidates = [c for c in candidates if c != ALGO_RABENSEIFNER]
        model = CostModel()
        best: tuple[float, str] | None = None
        for algo in candidates:
            cost = analyse_allreduce(algo, p, n, op, ctx_limit)
            if cost.admissible:
                cost.predicted_seconds = round(
                    model.time(cost.depth, cost.tokens_per_rank,
                               cost.evals_on_critical_path if op.is_semantic else 0),
                    3,
                )
                if best is None or cost.predicted_seconds < best[0]:
                    best = (cost.predicted_seconds, algo)
            considered.append(cost.to_dict())
        if best is None:
            raise AmpiArgError(
                f"no admissible {coll} algorithm for p={p}, n={n} tokens, op={op.name}, "
                f"ctx_limit={ctx_limit}",
                considered=considered,
            )
        return best[1], considered
    raise AmpiArgError(f"unknown collective {coll!r}")


# ===========================================================================
# Vector payloads and partitioning
# ===========================================================================


def is_vector(payload: Any) -> bool:
    """Vector payloads support element-wise reduction and hence partitioning.

    This is MPI's ``count``/datatype argument in disguise.  A scalar artifact
    must be reduced as a whole; a keyed collection of artifacts can be
    scattered across ranks and reduced independently per key.  Only vector
    payloads admit reduce-scatter, and therefore only vector payloads can be
    allreduced under a context bound that is smaller than the result.
    """
    return isinstance(payload, (dict, list)) and len(payload) > 0


def _stable_bucket(key: str, p: int) -> int:
    """Hash partitioning with a hash that is stable across processes.

    Python's built-in ``hash`` is salted per process, so using it here would
    make different ranks disagree about who owns which key --- a silent,
    intermittent correctness bug of exactly the kind a distributed protocol
    must design out.
    """
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % p


def partition(vec: Any, p: int) -> list[Any]:
    if isinstance(vec, dict):
        parts: list[Any] = [{} for _ in range(p)]
        for key, value in vec.items():
            parts[_stable_bucket(str(key), p)][key] = value
        return parts
    if isinstance(vec, list):
        parts = [[] for _ in range(p)]
        for i, value in enumerate(vec):
            parts[i % p].append(value)
        return parts
    raise AmpiArgError("payload is not a vector; reduce-scatter requires a dict or a list")


def assemble(parts: list[Any]) -> Any:
    present = [p for p in parts if p is not None]
    if present and all(isinstance(x, dict) for x in present):
        out: dict[str, Any] = {}
        for part in present:
            out.update(part)
        return out
    out_l: list[Any] = []
    for part in present:
        out_l.extend(part if isinstance(part, list) else [part])
    return out_l


def vec_combine(op: Op, a: Any, b: Any, evaluate: Callable[[list[Any], str], Any]) -> Any:
    """Element-wise combination of two vector payloads.

    Keys present in only one operand pass through untouched.  That sparse
    semantics is what makes a glossary or a symbol table a natural vector: an
    agent contributes only the terms it actually saw, and the reduction is not
    obliged to invent identity elements for everything it did not.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        out: dict[str, Any] = {}
        for key in sorted(set(a) | set(b)):
            if key in a and key in b and a[key] != b[key]:
                out[key] = evaluate([a[key], b[key]], key)
            else:
                out[key] = a.get(key, b.get(key))
        return out
    if isinstance(a, list) and isinstance(b, list):
        return evaluate([a, b], "")
    return evaluate([a, b], "")


# ===========================================================================
# Schedule representation
# ===========================================================================


@dataclass
class Step:
    kind: str
    peer: int | None = None
    dst: str = "acc"
    src: str = "acc"
    order: str = "left"
    meta: dict[str, Any] = field(default_factory=dict)


STEP_KINDS = {
    "send",         # transmit slots[src] to peer
    "recv",         # receive into slots[dst]
    "combine",      # slots['acc'] = op(acc, slots[src]) respecting `order`
    "move",         # slots[dst] = slots[src]
    "partition",    # slots['parts'] = partition(acc, p)
    "gatherparts",  # slots[dst] = {i: parts[i] for i in meta['indices']}
    "combineparts",  # parts[i] = op(parts[i], slots[src][i]) for i in indices
    "mergeparts",   # parts.update(slots[src])
    "assemble",     # slots['acc'] = assemble(parts)
    "setacc",       # slots['acc'] = parts[meta['index']]
}


def _internal_tag(seq: int) -> int:
    """One tag per collective invocation, above the user tag space.

    Reserving a band rather than sharing the user's is the point of MPI's
    MPI_TAG_UB and of communicator context ids: an agent that happens to send
    on tag 7 must not be able to corrupt a collective in flight.

    A single tag for the whole collective --- rather than one per step --- is
    what MPI implementations do, and it is correct for the same reason: within
    one collective, every pair of ranks issues its sends and its matching
    receives in the same order, so the non-overtaking guarantee pairs them up.
    Tagging by local step number would be wrong, because two ranks reach the
    same exchange at different points in their own schedules.
    """
    return AMPI_TAG_INTERNAL_BASE + (seq % 1_000_000)


@dataclass
class CollectiveState:
    coll_id: str
    pc: int
    slots: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"coll_id": self.coll_id, "pc": self.pc, "slots": self.slots}


# ===========================================================================
# Schedule generators
# ===========================================================================


def sched_barrier_dissemination(p: int, me: int) -> list[Step]:
    """Hensgen-Finkel-Manber dissemination barrier: ceil(lg p) rounds, no root."""
    steps: list[Step] = []
    for k in range(math.ceil(math.log2(p)) if p > 1 else 0):
        dist = 1 << k
        steps.append(Step("send", peer=(me + dist) % p, src="tok"))
        steps.append(Step("recv", peer=(me - dist) % p, dst="tok_in"))
    return steps


def sched_barrier_linear(p: int, me: int) -> list[Step]:
    steps: list[Step] = []
    if me == 0:
        steps += [Step("recv", peer=r, dst="tok_in") for r in range(1, p)]
        steps += [Step("send", peer=r, src="tok") for r in range(1, p)]
    else:
        steps.append(Step("send", peer=0, src="tok"))
        steps.append(Step("recv", peer=0, dst="tok_in"))
    return steps


def sched_bcast_binomial(p: int, me: int, root: int) -> list[Step]:
    """Binomial-tree broadcast: ceil(lg p) rounds and exactly p-1 messages."""
    steps: list[Step] = []
    rel = (me - root) % p
    mask = 1
    while mask < p:
        if rel & mask:
            steps.append(Step("recv", peer=((rel - mask) + root) % p, dst="acc"))
            break
        mask <<= 1
    mask >>= 1
    while mask > 0:
        child = rel + mask
        if child < p:
            steps.append(Step("send", peer=(child + root) % p, src="acc"))
        mask >>= 1
    return steps


def sched_bcast_linear(p: int, me: int, root: int) -> list[Step]:
    if me != root:
        return [Step("recv", peer=root, dst="acc")]
    return [Step("send", peer=r, src="acc") for r in range(p) if r != root]


def sched_bcast_chain(p: int, me: int, root: int) -> list[Step]:
    steps: list[Step] = []
    rel = (me - root) % p
    if rel > 0:
        steps.append(Step("recv", peer=((rel - 1) + root) % p, dst="acc"))
    if rel + 1 < p:
        steps.append(Step("send", peer=((rel + 1) + root) % p, src="acc"))
    return steps


def sched_reduce_binomial(p: int, me: int, root: int) -> list[Step]:
    """Binomial-tree reduction.

    The lower-ranked partial is always the left operand, so the fold respects
    rank order and an associative-but-not-commutative operator such as
    ``AMPI_CONCAT`` yields exactly what a linear reduction would --- at
    lg p depth instead of p.
    """
    steps: list[Step] = []
    rel = (me - root) % p
    mask = 1
    while mask < p:
        if rel & mask:
            steps.append(Step("send", peer=((rel - mask) + root) % p, src="acc"))
            break
        child = rel | mask
        if child < p:
            steps.append(Step("recv", peer=((child + root) % p), dst="inc"))
            steps.append(Step("combine", src="inc", order="left"))
        mask <<= 1
    return steps


def sched_reduce_linear(p: int, me: int, root: int) -> list[Step]:
    """Canonical-order reduction: the only sound schedule for a
    non-associative operator, at a cost of p-1 sequential evaluations."""
    steps: list[Step] = []
    if me == root:
        for r in range(p):
            if r == root:
                continue
            steps.append(Step("recv", peer=r, dst="inc"))
            steps.append(Step("combine", src="inc", order="left"))
    else:
        steps.append(Step("send", peer=root, src="acc"))
    return steps


def sched_allreduce_recursive_doubling(p: int, me: int) -> list[Step]:
    """Recursive doubling: lg p rounds, every rank ends holding the result.

    Non-power-of-two sizes use MPICH's treatment: the ``rem`` excess ranks fold
    into their even partners first, the power-of-two core runs the doubling,
    and the excess ranks are refreshed at the end.  Operand order is decided by
    rank comparison at every exchange, which keeps the schedule sound for
    associative non-commutative operators.
    """
    steps: list[Step] = []
    pof2 = 1 << int(math.floor(math.log2(p))) if p > 1 else 1
    rem = p - pof2
    if me < 2 * rem:
        if me % 2 == 0:
            steps.append(Step("recv", peer=me + 1, dst="inc"))
            steps.append(Step("combine", src="inc", order="left"))
            new_rank = me // 2
        else:
            steps.append(Step("send", peer=me - 1, src="acc"))
            new_rank = -1
    else:
        new_rank = me - rem

    if new_rank >= 0:
        mask = 1
        while mask < pof2:
            partner_new = new_rank ^ mask
            partner = partner_new * 2 if partner_new < rem else partner_new + rem
            if me < partner:
                steps.append(Step("send", peer=partner, src="acc"))
                steps.append(Step("recv", peer=partner, dst="inc"))
                steps.append(Step("combine", src="inc", order="left"))
            else:
                steps.append(Step("recv", peer=partner, dst="inc"))
                steps.append(Step("send", peer=partner, src="acc"))
                steps.append(Step("combine", src="inc", order="right"))
            mask <<= 1

    if me < 2 * rem:
        if me % 2 == 0:
            steps.append(Step("send", peer=me + 1, src="acc"))
        else:
            steps.append(Step("recv", peer=me - 1, dst="acc"))
    return steps


def sched_allreduce_ring(p: int, me: int) -> list[Step]:
    """Ring reduce-scatter followed by ring allgather.

    2(p-1) steps, but every step moves one block of about n/p tokens and peak
    residency stays at O(n/p) through the reduction.  This is what makes an
    allreduce possible when the whole vector does not fit in one agent's
    context window.  Requires a commutative operator.
    """
    steps: list[Step] = [Step("partition")]
    left, right = (me - 1) % p, (me + 1) % p
    for k in range(p - 1):
        send_idx = (me - k) % p
        recv_idx = (me - k - 1) % p
        steps.append(Step("gatherparts", dst="out", meta={"indices": [send_idx]}))
        steps.append(Step("send", peer=right, src="out"))
        steps.append(Step("recv", peer=left, dst="inc"))
        steps.append(Step("combineparts", src="inc", meta={"indices": [recv_idx]},
                          order="left"))
    for k in range(p - 1):
        send_idx = (me + 1 - k) % p
        steps.append(Step("gatherparts", dst="out", meta={"indices": [send_idx]}))
        steps.append(Step("send", peer=right, src="out"))
        steps.append(Step("recv", peer=left, dst="inc"))
        steps.append(Step("mergeparts", src="inc"))
    steps.append(Step("assemble"))
    return steps


def sched_allreduce_rabenseifner(p: int, me: int) -> list[Step]:
    """Recursive-halving reduce-scatter plus recursive-doubling allgather.

    2 lg p steps with the ring's O(n/p) reduction-phase residency: the
    algorithm Rabenseifner introduced to get the latency of recursive doubling
    and the bandwidth of the ring simultaneously.  Power-of-two sizes only;
    the decision function falls back to the ring otherwise, as MPICH does.
    """
    if p & (p - 1) != 0:
        return sched_allreduce_ring(p, me)
    steps: list[Step] = [Step("partition")]
    owned = list(range(p))
    mask = p >> 1
    while mask >= 1:
        partner = me ^ mask
        keep = [i for i in owned if (i & mask) == (me & mask)]
        give = [i for i in owned if (i & mask) != (me & mask)]
        steps.append(Step("gatherparts", dst="out", meta={"indices": give}))
        steps.append(Step("send", peer=partner, src="out"))
        steps.append(Step("recv", peer=partner, dst="inc"))
        steps.append(Step("combineparts", src="inc", meta={"indices": keep},
                          order="left" if me < partner else "right"))
        owned = keep
        mask >>= 1
    have = list(owned)
    mask = 1
    while mask < p:
        partner = me ^ mask
        steps.append(Step("gatherparts", dst="out", meta={"indices": list(have)}))
        steps.append(Step("send", peer=partner, src="out"))
        steps.append(Step("recv", peer=partner, dst="inc"))
        steps.append(Step("mergeparts", src="inc"))
        have = sorted(set(have) | set(_mirror(have, mask)))
        mask <<= 1
    steps.append(Step("assemble"))
    return steps


def _mirror(indices: list[int], mask: int) -> list[int]:
    return [i ^ mask for i in indices]


def sched_allgather_ring(p: int, me: int) -> list[Step]:
    steps: list[Step] = [Step("gatherparts", dst="_self", meta={"indices": []}),
                         Step("mergeparts", src="_selfblock")]
    left, right = (me - 1) % p, (me + 1) % p
    for k in range(p - 1):
        send_idx = (me - k) % p
        steps.append(Step("gatherparts", dst="out", meta={"indices": [send_idx]}))
        steps.append(Step("send", peer=right, src="out"))
        steps.append(Step("recv", peer=left, dst="inc"))
        steps.append(Step("mergeparts", src="inc"))
    steps.append(Step("assemble"))
    return steps


def sched_alltoall_pairwise(p: int, me: int) -> list[Step]:
    """Pairwise exchange: p-1 steps, each rank sends a distinct block to each peer.

    All-to-all is the one collective here whose send and receive buffers must
    stay distinct: block *j* of the outgoing partition is destined for rank *j*,
    while block *j* of the result is what rank *j* sent us.  Aliasing them makes
    a rank forward a block it received instead of the one it owns --- the
    in-place hazard that MPI handles with MPI_IN_PLACE and a separate recvbuf.
    """
    steps: list[Step] = [Step("partition")]
    for k in range(1, p):
        dst = (me + k) % p
        src = (me - k) % p
        steps.append(Step("gatherparts", dst="out",
                          meta={"indices": [dst], "from": "sendparts"}))
        steps.append(Step("send", peer=dst, src="out"))
        steps.append(Step("recv", peer=src, dst="inc"))
        steps.append(Step("mergeparts", src="inc", meta={"relabel": src}))
    steps.append(Step("assemble"))
    return steps


def sched_scan_chain(p: int, me: int) -> list[Step]:
    """Chain (Hillis-Steele would need a commutative op to be worth it).

    Depth p-1, but the prefix semantics is exactly right for
    non-commutative operators, which is the common case for agents: the
    running state after agent i is the fold of contributions 0..i in order.
    """
    steps: list[Step] = []
    if me > 0:
        steps.append(Step("recv", peer=me - 1, dst="inc"))
        steps.append(Step("combine", src="inc", order="right"))
    if me + 1 < p:
        steps.append(Step("send", peer=me + 1, src="acc"))
    return steps


SCHEDULES: dict[tuple[str, str], Callable[..., list[Step]]] = {
    (COLL_BARRIER, ALGO_DISSEMINATION): sched_barrier_dissemination,
    (COLL_BARRIER, ALGO_LINEAR): sched_barrier_linear,
    (COLL_BCAST, ALGO_BINOMIAL): sched_bcast_binomial,
    (COLL_BCAST, ALGO_LINEAR): sched_bcast_linear,
    (COLL_BCAST, ALGO_CHAIN): sched_bcast_chain,
    (COLL_REDUCE, ALGO_BINOMIAL): sched_reduce_binomial,
    (COLL_REDUCE, ALGO_LINEAR): sched_reduce_linear,
    (COLL_ALLREDUCE, ALGO_RECURSIVE_DOUBLING): sched_allreduce_recursive_doubling,
    (COLL_ALLREDUCE, ALGO_RING): sched_allreduce_ring,
    (COLL_ALLREDUCE, ALGO_RABENSEIFNER): sched_allreduce_rabenseifner,
    (COLL_ALLGATHER, ALGO_RING): sched_allgather_ring,
    (COLL_ALLTOALL, ALGO_LINEAR): sched_alltoall_pairwise,
    (COLL_SCAN, ALGO_CHAIN): sched_scan_chain,
}


# ===========================================================================
# The engine
# ===========================================================================


class CollectiveEngine:
    """Registers, schedules, executes and records one collective invocation."""

    def __init__(self, rt: Any, comm_name: str, coll: str, *, op: Op | None = None,
                 root: int | None = None, algo: str = ALGO_AUTO,
                 timeout: float = 1800.0) -> None:
        self.rt = rt
        self.comm = rt.comms.get(comm_name)
        rt._check_comm(self.comm)
        self.comm_name = comm_name
        self.coll = coll
        self.op = op
        self.root = root
        self.algo_request = algo
        self.timeout = timeout
        self.me = self.comm.rank_of(rt.rank)
        if self.me < 0:
            raise AmpiArgError(f"rank {rt.rank} is not a member of {comm_name!r}")
        self.p = self.comm.size
        self.seq = -1
        self.coll_id = ""
        self.algo = algo
        self.considered: list[dict[str, Any]] = []

    # -- registration and the collective-ordering check --------------------
    def register(self, payload_tokens: int, algo: str) -> dict[str, Any]:
        """Claim this rank's slot in the next collective on this communicator.

        Every rank keeps a private counter of how many collectives it has
        issued on the communicator.  The first rank to reach sequence *k*
        creates the record and writes down which collective it is; every later
        arrival at *k* must agree.  MPI declares a mismatch here undefined, and
        in practice it manifests as a hang diagnosed by attaching a debugger to
        a thousand ranks.  We can afford the check, and with LLM ranks we need
        it: an agent that skips a barrier because it misread its instructions
        is a routine event, not a bug that is found once and fixed forever.
        """
        rt = self.rt
        with rt.device.write_tx():
            rt._touch()
            resumed = self._resume_in_flight()
            if resumed is not None:
                return resumed
            seq = rt.device.counter_next(rt.job_id, f"collseq:{self.comm.comm_id}:{self.me}")
            row = rt.device.query_one(
                "SELECT * FROM coll WHERE comm_id=? AND seq=?", (self.comm.comm_id, seq)
            )
            if row is None:
                coll_id = util.new_id("coll")
                rt.device.execute(
                    "INSERT INTO coll (coll_id, job_id, comm_id, seq, op, root, algo, op_name, "
                    "expected, created_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (coll_id, rt.job_id, self.comm.comm_id, seq, self.coll, self.root, algo,
                     self.op.name if self.op else None, self.p, util.now(),
                     util.dumps({"initiator": self.me, "tokens": payload_tokens,
                                 "considered": self.considered})),
                )
                row = rt.device.query_one("SELECT * FROM coll WHERE coll_id=?", (coll_id,))
            else:
                mismatch: list[str] = []
                if row["op"] != self.coll:
                    mismatch.append(f"operation {row['op']!r} vs {self.coll!r}")
                mine_op = self.op.name if self.op else None
                if (row["op_name"] or None) != mine_op:
                    mismatch.append(f"operator {row['op_name']!r} vs {mine_op!r}")
                theirs_root = None if row["root"] is None else int(row["root"])
                if theirs_root != self.root:
                    mismatch.append(f"root {theirs_root} vs {self.root}")
                if mismatch:
                    rt.tracer.emit("AMPI_Collective_mismatch", "exit",
                                   comm_id=self.comm.comm_id, ok=False, seq=seq,
                                   detail="; ".join(mismatch))
                    raise AmpiCollectiveMismatch(
                        f"rank {self.me} issued {self.coll} as collective #{seq} on "
                        f"{self.comm_name!r}, but rank "
                        f"{util.loads(row['meta'], {}).get('initiator')} issued {row['op']} "
                        "there: " + "; ".join(mismatch),
                        seq=seq, expected=row["op"], got=self.coll,
                    )
        self.seq = seq
        self.coll_id = row["coll_id"]
        self.algo = row["algo"] or algo
        with rt.device.write_tx():
            rt.device.kv_set(rt.job_id, self._marker_key(),
                             {"coll_id": self.coll_id, "seq": seq, "op": self.coll,
                              "op_name": self.op.name if self.op else None, "root": self.root,
                              "algo": self.algo})
        return dict(row)

    # -- resumption --------------------------------------------------------
    def _marker_key(self) -> str:
        return f"inflight:{self.comm.comm_id}:{self.me}"

    def _resume_in_flight(self) -> dict[str, Any] | None:
        """Continue this rank's unfinished collective instead of starting a new one.

        A collective that suspended for a model upcall is re-entered by the
        agent issuing the identical call again.  Without an in-flight marker
        that second call would allocate the next sequence number and desynchronise
        this rank from every other one --- the collective-ordering bug that the
        sequence check exists to catch, introduced by the recovery mechanism
        itself.  With the marker, re-entry is exact and idempotent.
        """
        rt = self.rt
        marker = rt.device.kv_get(rt.job_id, self._marker_key(), None)
        if not marker:
            return None
        row = rt.device.query_one("SELECT * FROM coll WHERE coll_id=?", (marker["coll_id"],))
        if row is None:
            rt.device.kv_set(rt.job_id, self._marker_key(), None)
            return None
        mine = (self.coll, self.op.name if self.op else None, self.root)
        theirs = (marker["op"], marker.get("op_name"), marker.get("root"))
        if mine != theirs:
            raise AmpiCollectiveMismatch(
                f"rank {self.me} started {marker['op']} (operator {marker.get('op_name')}, "
                f"root {marker.get('root')}) on {self.comm_name!r} and has not finished it, "
                f"but is now issuing {self.coll} (operator {mine[1]}, root {mine[2]}). "
                "Re-issue the identical call to resume the suspended collective.",
                pending=marker, attempted={"op": self.coll, "op_name": mine[1], "root": mine[2]},
            )
        self.seq = int(marker["seq"])
        self.coll_id = marker["coll_id"]
        self.algo = marker.get("algo") or row["algo"]
        return dict(row)

    def clear_marker(self) -> None:
        with self.rt.device.write_tx():
            self.rt.device.kv_set(self.rt.job_id, self._marker_key(), None)

    # -- persisted state ---------------------------------------------------
    def _state_key(self) -> str:
        return f"cs:{self.coll_id}:{self.me}"

    def load_state(self) -> CollectiveState:
        raw = self.rt.device.kv_get(self.rt.job_id, self._state_key(), None)
        if raw is None:
            return CollectiveState(self.coll_id, 0, {})
        return CollectiveState(self.coll_id, int(raw["pc"]), raw["slots"])

    def save_state(self, state: CollectiveState) -> None:
        with self.rt.device.write_tx():
            self.rt.device.kv_set(self.rt.job_id, self._state_key(), state.to_dict())

    # -- operator evaluation -----------------------------------------------
    def _evaluate(self, operands: list[Any], step: int, label: str = "") -> Any:
        op = self.op
        assert op is not None
        if not op.is_semantic:
            acc = operands[0]
            for operand in operands[1:]:
                acc = op.fn(acc, operand)  # type: ignore[misc]
            return acc
        token = f"{self.coll_id}:{self.me}:{step}:{label}"
        rt = self.rt
        row = rt.device.query_one("SELECT * FROM pending_op WHERE op_token=?", (token,))
        if row is not None and row["state"] == "done":
            return util.loads(row["result"], row["result"])
        if row is None:
            with rt.device.write_tx():
                rt.device.execute(
                    "INSERT INTO pending_op (op_token, job_id, coll_id, assignee, op_name, step, "
                    "operands, created_at, meta) VALUES (?,?,?,?,?,?,?,?,?)",
                    (token, rt.job_id, self.coll_id, self.me, op.name, step,
                     util.dumps(operands), util.now(),
                     util.dumps({"label": label, "coll": self.coll})),
                )
                rt.tracer.emit("AMPI_Op_upcall", "enter", comm_id=self.comm.comm_id,
                               operator=op.name, step=step, key=label)
        raise SemanticUpcall(token, op, operands, step, label)

    def _combine(self, left: Any, right: Any, step: int, vector: bool, key: str = "") -> Any:
        if left is None:
            return right
        if right is None:
            return left
        if vector:
            return vec_combine(
                self.op,  # type: ignore[arg-type]
                left, right,
                lambda operands, k: self._evaluate(operands, step, f"{key}/{k}" if key else k),
            )
        return self._evaluate([left, right], step, key)

    # -- execution ---------------------------------------------------------
    def run(self, steps: list[Step], initial: dict[str, Any], vector: bool = False) -> Any:
        rt = self.rt
        state = self.load_state()
        if state.pc == 0 and not state.slots:
            state.slots = dict(initial)
            self.save_state(state)
        deadline = time.time() + self.timeout
        with rt.tracer.span("AMPI_" + self.coll.capitalize(), comm_id=self.comm.comm_id,
                            tag=self.seq) as span:
            while state.pc < len(steps):
                try:
                    self._exec_step(steps[state.pc], state, vector, deadline)
                except SemanticUpcall:
                    self.save_state(state)
                    raise
                state.pc += 1
                self.save_state(state)
            span["meta"] = {"algo": self.algo, "steps": len(steps), "p": self.p}
        result = state.slots.get("acc")
        self._record_result(result, len(steps))
        self.clear_marker()
        return result

    def _exec_step(self, step: Step, state: CollectiveState, vector: bool,
                   deadline: float) -> None:
        rt = self.rt
        slots = state.slots
        tag = _internal_tag(self.seq)
        if step.kind == "send":
            rt.send(self.comm_name, step.peer, tag, {"v": slots.get(step.src)},
                    meta={"coll": self.coll_id, "step": state.pc})
        elif step.kind == "recv":
            remaining = max(2.0, deadline - time.time())
            got = rt.recv(self.comm_name, step.peer, tag, timeout=remaining, deref=True,
                          charge_context=False)
            slots[step.dst] = util.loads(got["payload"], {}).get("v")
        elif step.kind == "combine":
            incoming = slots.get(step.src)
            if incoming is None:
                return
            left, right = ((slots.get("acc"), incoming) if step.order == "left"
                           else (incoming, slots.get("acc")))
            slots["acc"] = self._combine(left, right, state.pc, vector)
        elif step.kind == "move":
            slots[step.dst] = slots.get(step.src)
        elif step.kind == "partition":
            slots["parts"] = partition(slots.get("acc"), self.p)
            slots["sendparts"] = partition(slots.get("acc"), self.p)
        elif step.kind == "gatherparts":
            source = slots.setdefault(step.meta.get("from", "parts"), [None] * self.p)
            indices = step.meta.get("indices", [])
            slots[step.dst] = {str(i): source[i] for i in indices}
        elif step.kind == "combineparts":
            parts = slots.setdefault("parts", [None] * self.p)
            incoming = slots.get(step.src) or {}
            for i in step.meta.get("indices", []):
                theirs = incoming.get(str(i))
                if theirs is None:
                    continue
                mine = parts[i]
                left, right = (mine, theirs) if step.order == "left" else (theirs, mine)
                parts[i] = self._combine(left, right, state.pc, vector, key=f"b{i}")
            slots["parts"] = parts
        elif step.kind == "mergeparts":
            parts = slots.setdefault("parts", [None] * self.p)
            incoming = slots.get(step.src) or {}
            relabel = step.meta.get("relabel")
            if relabel is None:
                for key, value in incoming.items():
                    parts[int(key)] = value
            else:
                # All-to-all: the sender labels a block by its destination
                # index, but the receiver must file it under the *source*.
                for value in incoming.values():
                    parts[int(relabel)] = value
            slots["parts"] = parts
        elif step.kind == "assemble":
            slots["acc"] = assemble(slots.get("parts") or [])
        elif step.kind == "setacc":
            slots["acc"] = (slots.get("parts") or [None] * self.p)[step.meta["index"]]
        else:
            raise AmpiArgError(f"unknown schedule step {step.kind!r}")

    def _record_result(self, result: Any, nsteps: int) -> None:
        rt = self.rt
        text = util.dumps(result)
        with rt.device.write_tx():
            rt.device.execute(
                "INSERT INTO coll_contrib (coll_id, rank, body, tokens, arrived_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(coll_id, rank) DO UPDATE SET body=excluded.body",
                (self.coll_id, self.me, text, util.count_tokens(text), util.now()),
            )
            done = rt.device.query_one(
                "SELECT COUNT(*) AS n FROM coll_contrib WHERE coll_id=?", (self.coll_id,)
            )
            if int(done["n"]) >= self.p:
                rt.device.execute(
                    "UPDATE coll SET state='complete', completed_at=?, result=? WHERE coll_id=?",
                    (util.now(), util.clamp_text(text, 4000), self.coll_id),
                )


# ===========================================================================
# Public collective entry points
# ===========================================================================


def _prepare(rt: Any, comm_name: str, coll: str, op_name: str | None, root: int | None,
             algo: str, payload: Any, timeout: float) -> tuple[CollectiveEngine, Op | None, bool]:
    op = get_op(op_name) if op_name else None
    engine = CollectiveEngine(rt, comm_name, coll, op=op, root=root, algo=algo, timeout=timeout)
    vector = is_vector(payload)
    n = util.count_tokens(util.dumps(payload)) if payload is not None else 0
    if algo == ALGO_AUTO:
        ctx_limit = int(rt.rank_row()["ctx_limit"])
        chosen, considered = select_algorithm(coll, engine.p, n, op, ctx_limit, vector)
        engine.considered = considered
    else:
        chosen = algo
    engine.register(n, chosen)
    if engine.p == 1:
        # A single-member communicator completes without a schedule, so the
        # in-flight marker would never be cleared by run().
        engine.clear_marker()
    return engine, op, vector


def _schedule(coll: str, algo: str, p: int, me: int, root: int | None) -> list[Step]:
    generator = SCHEDULES.get((coll, algo))
    if generator is None:
        raise AmpiArgError(
            f"no schedule for {coll} with algorithm {algo!r}; available: "
            + ", ".join(sorted(a for c, a in SCHEDULES if c == coll))
        )
    if coll in (COLL_BCAST, COLL_REDUCE):
        return generator(p, me, root or 0)
    return generator(p, me)


def barrier(rt: Any, comm_name: str, *, algo: str = ALGO_AUTO,
            timeout: float = 1800.0) -> dict[str, Any]:
    """AMPI_Barrier: the only collective that is guaranteed to synchronise."""
    engine, _, _ = _prepare(rt, comm_name, COLL_BARRIER, None, None, algo, None, timeout)
    if engine.p == 1:
        return {"ok": True, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_BARRIER, engine.algo, engine.p, engine.me, None)
    engine.run(steps, {"tok": 1, "acc": 1})
    return {"ok": True, "algo": engine.algo, "p": engine.p, "steps": len(steps),
            "seq": engine.seq}


def bcast(rt: Any, comm_name: str, root: int, payload: Any = None, *, algo: str = ALGO_AUTO,
          timeout: float = 1800.0) -> dict[str, Any]:
    engine, _, _ = _prepare(rt, comm_name, COLL_BCAST, None, root, algo, payload, timeout)
    if engine.p == 1:
        return {"result": payload, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_BCAST, engine.algo, engine.p, engine.me, root)
    result = engine.run(steps, {"acc": payload if engine.me == root else None})
    return {"result": result, "algo": engine.algo, "p": engine.p, "steps": len(steps),
            "seq": engine.seq}


def reduce_(rt: Any, comm_name: str, root: int, payload: Any, op_name: str, *,
            algo: str = ALGO_AUTO, timeout: float = 1800.0) -> dict[str, Any]:
    engine, op, vector = _prepare(rt, comm_name, COLL_REDUCE, op_name, root, algo, payload,
                                  timeout)
    if engine.p == 1:
        return {"result": payload, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_REDUCE, engine.algo, engine.p, engine.me, root)
    result = engine.run(steps, {"acc": payload}, vector=vector)
    if op is not None and op.finalize and engine.me == root:
        result = op.finalize(result)
    return {"result": result if engine.me == root else None, "algo": engine.algo,
            "p": engine.p, "steps": len(steps), "seq": engine.seq,
            "considered": engine.considered}


def allreduce(rt: Any, comm_name: str, payload: Any, op_name: str, *, algo: str = ALGO_AUTO,
              timeout: float = 1800.0) -> dict[str, Any]:
    engine, op, vector = _prepare(rt, comm_name, COLL_ALLREDUCE, op_name, None, algo, payload,
                                  timeout)
    if engine.p == 1:
        result = op.finalize(payload) if op and op.finalize else payload
        return {"result": result, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_ALLREDUCE, engine.algo, engine.p, engine.me, None)
    result = engine.run(steps, {"acc": payload}, vector=vector)
    if op is not None and op.finalize:
        result = op.finalize(result)
    return {"result": result, "algo": engine.algo, "p": engine.p, "steps": len(steps),
            "seq": engine.seq, "considered": engine.considered}


def allgather(rt: Any, comm_name: str, payload: Any, *, algo: str = ALGO_AUTO,
              timeout: float = 1800.0) -> dict[str, Any]:
    """AMPI_Allgather: every rank ends holding every rank's contribution.

    Implemented as a ring so that no rank ever holds more than its own block
    plus one in flight until the final assembly, and so that the cost is
    p-1 steps of one block rather than lg p steps of a doubling payload.
    """
    engine, _, _ = _prepare(rt, comm_name, COLL_ALLGATHER, None, None, algo, payload, timeout)
    if engine.p == 1:
        return {"result": {"0": payload}, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_ALLGATHER, engine.algo, engine.p, engine.me, None)
    parts: list[Any] = [None] * engine.p
    parts[engine.me] = payload
    initial = {"acc": payload, "parts": parts,
               "_selfblock": {str(engine.me): payload}}
    result = engine.run(steps, initial)
    state = engine.load_state()
    blocks = state.slots.get("parts") or []
    return {"result": {str(i): blocks[i] for i in range(engine.p)}, "algo": engine.algo,
            "p": engine.p, "steps": len(steps), "seq": engine.seq}


def alltoall(rt: Any, comm_name: str, payload: Any, *, algo: str = ALGO_AUTO,
             timeout: float = 1800.0) -> dict[str, Any]:
    engine, _, _ = _prepare(rt, comm_name, COLL_ALLTOALL, None, None, algo, payload, timeout)
    if engine.p == 1:
        return {"result": payload, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_ALLTOALL, engine.algo, engine.p, engine.me, None)
    engine.run(steps, {"acc": payload})
    state = engine.load_state()
    blocks = state.slots.get("parts") or []
    return {"result": {str(i): blocks[i] for i in range(engine.p)}, "algo": engine.algo,
            "p": engine.p, "steps": len(steps), "seq": engine.seq}


def scan(rt: Any, comm_name: str, payload: Any, op_name: str, *, algo: str = ALGO_AUTO,
         timeout: float = 1800.0) -> dict[str, Any]:
    engine, op, vector = _prepare(rt, comm_name, COLL_SCAN, op_name, None, algo, payload, timeout)
    if engine.p == 1:
        return {"result": payload, "algo": engine.algo, "p": 1, "steps": 0}
    steps = _schedule(COLL_SCAN, engine.algo, engine.p, engine.me, None)
    result = engine.run(steps, {"acc": payload}, vector=vector)
    return {"result": result, "algo": engine.algo, "p": engine.p, "steps": len(steps),
            "seq": engine.seq}


def gather(rt: Any, comm_name: str, root: int, payload: Any, *, timeout: float = 1800.0,
           algo: str = ALGO_AUTO) -> dict[str, Any]:
    """AMPI_Gather, expressed as a reduce with the bag operator.

    MPI keeps gather and reduce separate because the datatype machinery makes
    concatenation into a contiguous buffer a different operation from a
    reduction.  With keyed artifacts they are the same operation, so we express
    gather as ``reduce`` under ``AMPI_BAG`` and inherit its algorithm selection
    and its tree.
    """
    tagged = {str(rt.comms.get(comm_name).rank_of(rt.rank)): payload}
    engine, op, _ = _prepare(rt, comm_name, COLL_GATHER, "AMPI_MERGE_JSON", root,
                             ALGO_BINOMIAL if algo == ALGO_AUTO else algo, tagged, timeout)
    if engine.p == 1:
        return {"result": tagged, "algo": engine.algo, "p": 1, "steps": 0}
    steps = sched_reduce_binomial(engine.p, engine.me, root) if engine.algo == ALGO_BINOMIAL \
        else sched_reduce_linear(engine.p, engine.me, root)
    result = engine.run(steps, {"acc": tagged}, vector=False)
    return {"result": result if engine.me == root else None, "algo": engine.algo,
            "p": engine.p, "steps": len(steps), "seq": engine.seq}


def scatter(rt: Any, comm_name: str, root: int, blocks: dict[str, Any] | None, *,
            timeout: float = 1800.0, algo: str = ALGO_AUTO) -> dict[str, Any]:
    """AMPI_Scatter: broadcast the block map, then keep only your own block.

    Naive, and deliberately so at this scale: the alternative binomial scatter
    halves the volume but the volume term is not what dominates when the units
    are agent turns.  The decision function is where a better choice would go.
    """
    engine, _, _ = _prepare(rt, comm_name, COLL_SCATTER, None, root, ALGO_BINOMIAL, blocks,
                            timeout)
    if engine.p == 1:
        return {"result": (blocks or {}).get("0"), "algo": engine.algo, "p": 1, "steps": 0}
    steps = sched_bcast_binomial(engine.p, engine.me, root)
    everything = engine.run(steps, {"acc": blocks if engine.me == root else None})
    mine = (everything or {}).get(str(engine.me))
    return {"result": mine, "algo": engine.algo, "p": engine.p, "steps": len(steps),
            "seq": engine.seq}


def allreduce_structural(rt: Any, comm_name: str, value: Any, op_name: str, *,
                         timeout: float = 300.0,
                         tolerate_failures: bool = False) -> dict[str, Any]:
    """Failure-tolerant allreduce over live ranks only, used by AMPI_Comm_agree.

    This does not go through the step machine.  Agreement has to work when the
    communicator is already damaged, so it collects contributions directly
    through the durable store, ignores ranks the failure detector has
    condemned, and returns the survivor set alongside the value.  Using a
    point-to-point schedule here would deadlock on precisely the failures the
    call exists to handle.
    """
    comm = rt.comms.get(comm_name)
    me = comm.rank_of(rt.rank)
    op = get_op(op_name)
    seq = rt.device.counter_next(rt.job_id, f"agreeseq:{comm.comm_id}:{me}")
    coll_id = f"agree:{comm.comm_id}:{seq}"
    with rt.device.write_tx():
        rt.device.execute(
            "INSERT INTO coll (coll_id, job_id, comm_id, seq, op, algo, op_name, expected, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(coll_id) DO NOTHING",
            (coll_id, rt.job_id, comm.comm_id, -seq, "agree", ALGO_OFFLOAD, op.name,
             comm.size, util.now()),
        )
        rt.device.execute(
            "INSERT INTO coll_contrib (coll_id, rank, body, tokens, arrived_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(coll_id, rank) DO UPDATE SET body=excluded.body",
            (coll_id, me, util.dumps(value), 1, util.now()),
        )
    deadline = time.time() + timeout
    while True:
        with rt.device.write_tx():
            rt._touch()
        for suspect in rt.suspected():
            rt.declare_failed(suspect, "heartbeat lease expired during agree")
        failed = rt.failed_ranks()
        expected = [comm.rank_of(w) for w in comm.members if w not in failed]
        rows = rt.device.query(
            "SELECT rank, body FROM coll_contrib WHERE coll_id=? ORDER BY rank", (coll_id,)
        )
        have = {int(r["rank"]): util.loads(r["body"], None) for r in rows}
        if all(r in have for r in expected):
            values = [have[r] for r in sorted(expected)]
            result = reduce_sequence(op, values)
            with rt.device.write_tx():
                rt.device.execute(
                    "UPDATE coll SET state='complete', completed_at=?, result=? WHERE coll_id=?",
                    (util.now(), util.dumps(result), coll_id),
                )
                rt.tracer.emit("AMPI_Comm_agree", "exit", comm_id=comm.comm_id,
                               participants=len(expected), failed=sorted(failed))
            return {"result": result, "participants": sorted(expected),
                    "failed": sorted(failed), "seq": seq}
        if time.time() >= deadline:
            if not tolerate_failures:
                raise AmpiTimeout(
                    f"agree on {comm_name!r} timed out; missing ranks "
                    f"{sorted(set(expected) - set(have))}",
                    missing=sorted(set(expected) - set(have)),
                )
            values = [have[r] for r in sorted(have)]
            return {"result": reduce_sequence(op, values) if values else None,
                    "participants": sorted(have), "failed": sorted(failed),
                    "timed_out": True, "seq": seq}
        time.sleep(rt.poll_interval)


def live_members(rt: Any, comm_name: str) -> list[int]:
    comm = rt.comms.get(comm_name)
    states = {int(r["rank"]): r["state"] for r in rt.all_ranks()}
    return [comm.rank_of(w) for w in comm.members if states.get(w) in LIVE_RANK_STATES]
