"""Collective schedules, their costs, and algorithm selection.

Nothing in this module performs I/O.  A schedule is a list of rounds and a round
is a list of transfers, so a schedule can be costed, checked, drawn, and tested
without an executor or a device.  That separation is not tidiness: it is what lets
the paper's selection argument be verified rather than asserted, and it is what
lets ``ampi plan`` explain a decision before a job is paid for.

**Why selection has to be rederived.**  MPI's algorithm choices minimise the
alpha and beta terms of the Hockney model and treat the reduction operator, gamma,
as free.  That is correct for MPI: gamma is a floating-point addition.  Here gamma
is an executor applying a merge, which is seconds to minutes, and alpha is a
database write, which is milliseconds.  Gamma exceeds alpha and beta*n by three to
five orders of magnitude.  Two of MPI's rules invert:

*Recursive-doubling allreduce becomes wrong.*  MPI prefers it for short messages
precisely because the redundant arithmetic is free: every rank computes the whole
reduction itself, costing ``p*ceil(log2 p)`` operator applications in total
against reduce-then-broadcast's ``p-1``, and buying a factor of two in rounds.
When one application is a minute of an executor's time, that trade is a disaster,
and at ``p=128`` it also moves roughly 36 times as many payload tokens.

*Flat becomes right when the runtime owns the operator.*  If the implementation
can apply the operator --- set union, JSON merge, max --- then the shared journal
folds every contribution in one round with no messages at all.  A tree adds rounds
and buys nothing.  This is the in-network aggregation regime, and it is the common
case for the operators harnesses should be using.

**And a constraint MPI does not have.**  An algorithm here can be *infeasible*
rather than merely slow, because the peak data resident in one rank's context
exceeds a window that cannot be enlarged.  Selection is therefore an admissibility
test before it is an optimisation, and a rejected algorithm reports why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..errors import err
from .ops import Op

__all__ = [
    "Transfer",
    "Schedule",
    "Cost",
    "Decision",
    "CATALOGUE",
    "build_schedule",
    "cost_of",
    "select_algorithm",
    "explain_selection",
    "ALPHA_S",
    "BETA_S_PER_TOKEN",
    "HANDLE_TOKENS",
]

# Reference-implementation constants, fitted by ping-pong regression over the
# SQLite device (experiments/e0_micro).  They are reported rather than assumed
# because a different device will have different ones and the selection rules
# depend only on their ratio to gamma.
ALPHA_S: float = 0.0057
BETA_S_PER_TOKEN: float = 1.15e-6
HANDLE_TOKENS: int = 40


@dataclass(frozen=True)
class Transfer:
    """One message in one round of a schedule."""

    src: int
    dst: int
    tokens: int = 0
    combine: bool = False  # the destination applies the operator on arrival


@dataclass
class Schedule:
    """A collective expressed as point-to-point rounds.

    Expressing collectives over point-to-point is a deliberate discipline: the
    *shape* of the communication determines both cost and, for a lossy operator,
    quality, and a centralised implementation hides exactly the effects a harness
    author needs to see.  The one exception is the counting barrier, which is
    centralised precisely because it must be able to name the ranks that did not
    arrive.
    """

    collective: str
    algorithm: str
    p: int
    rounds: list[list[Transfer]] = field(default_factory=list)
    root: int = 0
    #: Ranks holding the result when the schedule completes.
    result_at: tuple[int, ...] = ()
    note: str = ""

    @property
    def n_rounds(self) -> int:
        return len(self.rounds)

    @property
    def n_messages(self) -> int:
        return sum(len(r) for r in self.rounds)

    @property
    def applications(self) -> int:
        return sum(1 for r in self.rounds for t in r if t.combine)

    @property
    def critical_path_applications(self) -> int:
        """Operator applications on the longest dependency chain.

        This, not the total, is what bounds wall-clock time when the operator is
        an executor, and it is the quantity the selection rule minimises.

        Applications arriving at the *same* rank in the same round are serialised,
        because one executor cannot merge two pairs at once.  Getting this wrong
        makes a flat reduction look like a one-application schedule when in fact
        the root applies ``p-1`` operators back to back --- which would invert the
        paper's central comparison, so it is worth stating explicitly: the depth
        of a rank after a round is the depth of its latest input plus the number
        of merges it must perform in that round.
        """
        depth: dict[int, int] = {r: 0 for r in range(self.p)}
        for rnd in self.rounds:
            arrivals: dict[int, list[Transfer]] = {}
            for t in rnd:
                arrivals.setdefault(t.dst, []).append(t)
            new = dict(depth)
            for dst, transfers in arrivals.items():
                ready = max([depth[dst]] + [depth[t.src] for t in transfers])
                merges = sum(1 for t in transfers if t.combine)
                new[dst] = max(new[dst], ready + merges)
            depth = new
        return max(depth.values()) if depth else 0

    def volume(self) -> int:
        return sum(t.tokens for r in self.rounds for t in r)

    def peak_resident(self) -> int:
        """Greatest cumulative token ingest at any single rank."""
        acc: dict[int, int] = {r: 0 for r in range(self.p)}
        for rnd in self.rounds:
            for t in rnd:
                acc[t.dst] += t.tokens
        return max(acc.values()) if acc else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "collective": self.collective,
            "algorithm": self.algorithm,
            "p": self.p,
            "root": self.root,
            "rounds": self.n_rounds,
            "messages": self.n_messages,
            "applications": self.applications,
            "critical_path_applications": self.critical_path_applications,
            "volume_tokens": self.volume(),
            "peak_resident_tokens": self.peak_resident(),
            "result_at": list(self.result_at),
            "note": self.note,
        }


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

CATALOGUE: dict[str, tuple[str, ...]] = {
    "barrier": ("central", "linear", "dissemination"),
    "bcast": ("flat", "binomial", "chain", "scatter_allgather"),
    "scatter": ("flat", "binomial"),
    "gather": ("flat", "binomial"),
    "allgather": ("flat", "ring", "recursive_doubling", "bruck"),
    "reduce": ("flat", "binomial", "chain"),
    "allreduce": ("flat", "reduce_bcast", "recursive_doubling", "rabenseifner"),
    "reduce_scatter": ("flat", "halving", "ring"),
    "scan": ("chain", "recursive_doubling"),
    "exscan": ("chain", "recursive_doubling"),
    "alltoall": ("flat", "pairwise", "bruck"),
    "neighbor_allgather": ("flat",),
}


def _binomial_children(root: int, p: int) -> dict[int, list[int]]:
    """Children of each node in a binomial tree rooted at ``root``.

    Built over *virtual* ranks so that any root works, then rotated.  Rotation is
    also why a non-commutative operator may only use this tree when the root is
    zero: for any other root the leaves are no longer visited in rank order.
    """
    children: dict[int, list[int]] = {r: [] for r in range(p)}
    for vrank in range(p):
        real = (vrank + root) % p
        mask = 1
        while mask < p:
            if vrank & mask:
                parent_v = vrank & ~mask
                children[(parent_v + root) % p].append(real)
                break
            mask <<= 1
    return children


def build_schedule(
    collective: str,
    algorithm: str,
    p: int,
    *,
    root: int = 0,
    tokens: int = 0,
    handle_tokens: int = HANDLE_TOKENS,
    inline: bool = True,
) -> Schedule:
    """Construct the round-by-round schedule for one collective.

    ``tokens`` is the per-contribution payload size; ``inline`` selects whether
    bodies travel or only handles do, which is the single largest determinant of
    peak residency and therefore of feasibility.
    """
    if collective not in CATALOGUE:
        raise err("AMPI_ERR_ARG", f"unknown collective {collective!r}", known=sorted(CATALOGUE))
    if algorithm not in CATALOGUE[collective]:
        raise err(
            "AMPI_ERR_ARG",
            f"{algorithm!r} is not an algorithm for {collective!r}",
            known=list(CATALOGUE[collective]),
        )
    if p < 1:
        raise err("AMPI_ERR_ARG", "p must be at least 1")

    w = tokens if inline else handle_tokens
    s = Schedule(collective, algorithm, p, root=root)
    if p == 1:
        s.result_at = (0,)
        return s

    match (collective, algorithm):
        # -- barrier -------------------------------------------------------
        case ("barrier", "central"):
            s.rounds = [
                [Transfer(r, root, 0) for r in range(p) if r != root],
                [Transfer(root, r, 0) for r in range(p) if r != root],
            ]
            s.note = "the only algorithm that can name the ranks that did not arrive"
        case ("barrier", "linear"):
            s.rounds = [[Transfer(r, (r + 1) % p, 0)] for r in range(p)]
        case ("barrier", "dissemination"):
            for d in range(math.ceil(math.log2(p))):
                s.rounds.append([Transfer(r, (r + (1 << d)) % p, 0) for r in range(p)])
            s.note = "ceil(log2 p) rounds, correct for any p, but cannot identify absentees"

        # -- broadcast -----------------------------------------------------
        case ("bcast", "flat"):
            s.rounds = [[Transfer(root, r, w) for r in range(p) if r != root]]
        case ("bcast", "binomial"):
            children = _binomial_children(root, p)
            frontier, done = [root], {root}
            while frontier:
                rnd = []
                nxt = []
                for parent in frontier:
                    for c in children[parent]:
                        if c not in done:
                            rnd.append(Transfer(parent, c, w))
                            done.add(c)
                            nxt.append(c)
                if rnd:
                    s.rounds.append(rnd)
                frontier = nxt
        case ("bcast", "chain"):
            order = [(root + i) % p for i in range(p)]
            s.rounds = [[Transfer(order[i], order[i + 1], w)] for i in range(p - 1)]
        case ("bcast", "scatter_allgather"):
            piece = max(1, w // p)
            s.rounds = [[Transfer(root, r, piece) for r in range(p) if r != root]]
            for d in range(math.ceil(math.log2(p))):
                s.rounds.append(
                    [Transfer(r, (r + (1 << d)) % p, piece * (1 << d)) for r in range(p)]
                )
            s.note = "van de Geijn: bandwidth-optimal for long messages"
        case ("bcast", _):
            raise err("AMPI_ERR_INTERN", "unreachable")

        # -- scatter / gather ---------------------------------------------
        case ("scatter", "flat"):
            s.rounds = [[Transfer(root, r, w) for r in range(p) if r != root]]
        case ("scatter", "binomial"):
            children = _binomial_children(root, p)

            def subtree(node: int) -> int:
                return 1 + sum(subtree(c) for c in children[node])

            frontier, done = [root], {root}
            while frontier:
                rnd, nxt = [], []
                for parent in frontier:
                    for c in children[parent]:
                        if c not in done:
                            rnd.append(Transfer(parent, c, w * subtree(c)))
                            done.add(c)
                            nxt.append(c)
                if rnd:
                    s.rounds.append(rnd)
                frontier = nxt
        case ("gather", "flat"):
            s.rounds = [[Transfer(r, root, w) for r in range(p) if r != root]]
        case ("gather", "binomial"):
            children = _binomial_children(root, p)

            def depth_of(node: int, d: int = 0) -> int:
                return max([d] + [depth_of(c, d + 1) for c in children[node]])

            levels: dict[int, list[Transfer]] = {}

            def walk(node: int, d: int) -> int:
                size = 1
                for c in children[node]:
                    size += walk(c, d + 1)
                if node != root:
                    levels.setdefault(d, [])
                return size

            def collect(node: int) -> int:
                size = 1
                for c in children[node]:
                    csize = collect(c)
                    levels.setdefault(depth_of(c), []).append(Transfer(c, node, w * csize))
                    size += csize
                return size

            collect(root)
            for d in sorted(levels, reverse=True):
                if levels[d]:
                    s.rounds.append(levels[d])

        # -- allgather -----------------------------------------------------
        case ("allgather", "flat"):
            s.rounds = [[Transfer(a, b, w) for a in range(p) for b in range(p) if a != b]]
        case ("allgather", "ring"):
            for _ in range(p - 1):
                s.rounds.append([Transfer(r, (r + 1) % p, w) for r in range(p)])
            s.note = "bandwidth-optimal: each rank sends exactly (p-1) blocks"
        case ("allgather", "recursive_doubling"):
            for d in range(math.ceil(math.log2(p))):
                s.rounds.append(
                    [Transfer(r, r ^ (1 << d), w * (1 << d)) for r in range(p) if r ^ (1 << d) < p]
                )
        case ("allgather", "bruck"):
            for d in range(math.ceil(math.log2(p))):
                s.rounds.append(
                    [Transfer(r, (r - (1 << d)) % p, w * min(1 << d, p - (1 << d))) for r in range(p)]
                )
            s.note = "Bruck: ceil(log2 p) rounds for any p, best for short messages"

        # -- reduce --------------------------------------------------------
        case ("reduce", "flat"):
            s.rounds = [[Transfer(r, root, w, combine=True) for r in range(p) if r != root]]
            s.note = "one round; the root applies p-1 operators back to back"
        case ("reduce", "binomial"):
            children = _binomial_children(root, p)

            def rdepth(node: int) -> int:
                return 1 + max([0] + [rdepth(c) for c in children[node]])

            levels: dict[int, list[Transfer]] = {}
            for parent in range(p):
                for c in children[parent]:
                    levels.setdefault(rdepth(c), []).append(Transfer(c, parent, w, combine=True))
            for d in sorted(levels):
                s.rounds.append(levels[d])
            s.note = "ceil(log2 p) applications on the critical path"
        case ("reduce", "chain"):
            order = [(root + 1 + i) % p for i in range(p - 1)] + [root]
            s.rounds = [
                [Transfer(order[i], order[i + 1], w, combine=True)] for i in range(len(order) - 1)
            ]
            s.note = "p-1 applications on the critical path; the only sound schedule for a "
            "non-associative operator"

        # -- allreduce -----------------------------------------------------
        case ("allreduce", "flat"):
            s.rounds = [
                [Transfer(r, root, w, combine=True) for r in range(p) if r != root],
                [Transfer(root, r, w) for r in range(p) if r != root],
            ]
            s.note = "the runtime-operator case: fold in the journal, publish once"
        case ("allreduce", "reduce_bcast"):
            red = build_schedule("reduce", "binomial", p, root=root, tokens=tokens, inline=inline)
            bc = build_schedule("bcast", "binomial", p, root=root, tokens=tokens, inline=inline)
            s.rounds = red.rounds + bc.rounds
            s.note = "one result, broadcast: 'agreement' actually means agreement"
        case ("allreduce", "recursive_doubling"):
            for d in range(math.ceil(math.log2(p))):
                s.rounds.append(
                    [
                        Transfer(r, r ^ (1 << d), w, combine=True)
                        for r in range(p)
                        if r ^ (1 << d) < p
                    ]
                )
            s.note = (
                "MPI's short-message choice; p*ceil(log2 p) applications in total, and every "
                "rank folds independently so a lossy operator makes them disagree"
            )
        case ("allreduce", "rabenseifner"):
            rs = build_schedule("reduce_scatter", "halving", p, tokens=tokens, inline=inline)
            ag = build_schedule("allgather", "recursive_doubling", p, tokens=max(1, tokens // p), inline=inline)
            s.rounds = rs.rounds + ag.rounds
            s.note = "reduce-scatter then allgather: bandwidth-optimal for long messages"

        # -- reduce_scatter -------------------------------------------------
        case ("reduce_scatter", "flat"):
            s.rounds = [
                [Transfer(a, b, max(1, w // p), combine=True) for a in range(p) for b in range(p) if a != b]
            ]
        case ("reduce_scatter", "halving"):
            block = w
            for d in range(math.ceil(math.log2(p))):
                block = max(1, block // 2)
                s.rounds.append(
                    [
                        Transfer(r, r ^ (1 << (math.ceil(math.log2(p)) - 1 - d)), block, combine=True)
                        for r in range(p)
                        if r ^ (1 << (math.ceil(math.log2(p)) - 1 - d)) < p
                    ]
                )
        case ("reduce_scatter", "ring"):
            for _ in range(p - 1):
                s.rounds.append(
                    [Transfer(r, (r + 1) % p, max(1, w // p), combine=True) for r in range(p)]
                )

        # -- scan ----------------------------------------------------------
        case (("scan" | "exscan"), "chain"):
            s.rounds = [[Transfer(i, i + 1, w, combine=True)] for i in range(p - 1)]
            s.note = "p-1 rounds, p-1 applications: sequential in both"
        case (("scan" | "exscan"), "recursive_doubling"):
            for d in range(math.ceil(math.log2(p))):
                stride = 1 << d
                s.rounds.append(
                    [Transfer(r, r + stride, w, combine=True) for r in range(p - stride)]
                )
            s.note = (
                "Hillis-Steele: every prefix in ceil(log2 p) rounds at the price of about "
                "p*log p applications -- more operator work for fewer rounds, which is the "
                "right direction only when per-invocation latency dominates"
            )

        # -- alltoall ---------------------------------------------------------
        case ("alltoall", "flat"):
            s.rounds = [[Transfer(a, b, w) for a in range(p) for b in range(p) if a != b]]
        case ("alltoall", "pairwise"):
            for step in range(1, p):
                s.rounds.append([Transfer(r, (r + step) % p, w) for r in range(p)])
            s.note = "p-1 rounds, no contention: the natural expression of all-way review"
        case ("alltoall", "bruck"):
            for d in range(math.ceil(math.log2(p))):
                s.rounds.append(
                    [Transfer(r, (r - (1 << d)) % p, w * (p // 2)) for r in range(p)]
                )

        case ("neighbor_allgather", "flat"):
            s.rounds = [[Transfer(r, (r + 1) % p, w) for r in range(p)]]

        case _:  # pragma: no cover - the catalogue guards this
            raise err("AMPI_ERR_INTERN", f"no schedule builder for {collective}/{algorithm}")

    if collective in ("bcast", "scatter", "allreduce", "allgather", "alltoall", "scan", "exscan"):
        s.result_at = tuple(range(p))
    elif collective in ("reduce", "gather"):
        s.result_at = (root,)
    else:
        s.result_at = tuple(range(p))
    return s


# --------------------------------------------------------------------------
# Costing
# --------------------------------------------------------------------------


@dataclass
class Cost:
    algorithm: str
    rounds: int
    messages: int
    volume_tokens: int
    applications: int
    critical_path_applications: int
    peak_resident_tokens: int
    protocol_seconds: float
    operator_seconds: float
    admissible: bool = True
    reason: str = ""

    @property
    def total_seconds(self) -> float:
        return self.protocol_seconds + self.operator_seconds

    @property
    def price_units(self) -> int:
        """What the schedule *costs*, as distinct from how long it takes.

        Wall time and money are not proportional here and must be reported
        separately.  Running ``p`` ranks divides time by up to ``p`` and divides
        price by nothing, and a redundant operator application is free in wall
        time if it is off the critical path but is fully charged in tokens.  This
        is why recursive-doubling allreduce can *tie* reduce-then-broadcast on
        latency at ``p=64`` while costing six times as much: 384 operator
        applications against 63.
        """
        return self.applications

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "rounds": self.rounds,
            "messages": self.messages,
            "volume_tokens": self.volume_tokens,
            "applications": self.applications,
            "critical_path_applications": self.critical_path_applications,
            "peak_resident_tokens": self.peak_resident_tokens,
            "protocol_seconds": round(self.protocol_seconds, 6),
            "operator_seconds": round(self.operator_seconds, 6),
            "total_seconds": round(self.total_seconds, 6),
            "price_units": self.price_units,
            "admissible": self.admissible,
            "reason": self.reason,
        }


def cost_of(
    schedule: Schedule,
    *,
    gamma_s: float = 0.0,
    alpha_s: float = ALPHA_S,
    beta_s_per_token: float = BETA_S_PER_TOKEN,
    ctx_limit: int | None = None,
) -> Cost:
    """Cost a schedule in the alpha-beta-gamma model.

    ``gamma_s`` is the cost of one operator application.  Setting it to zero
    recovers MPI's regime; setting it to thirty seconds recovers ours.  The
    selection rules invert somewhere between, and ``explain_selection`` reports
    where.
    """
    protocol = schedule.n_rounds * alpha_s + schedule.volume() * beta_s_per_token
    operator = schedule.critical_path_applications * gamma_s
    cost = Cost(
        algorithm=schedule.algorithm,
        rounds=schedule.n_rounds,
        messages=schedule.n_messages,
        volume_tokens=schedule.volume(),
        applications=schedule.applications,
        critical_path_applications=schedule.critical_path_applications,
        peak_resident_tokens=schedule.peak_resident(),
        protocol_seconds=protocol,
        operator_seconds=operator,
    )
    if ctx_limit is not None and cost.peak_resident_tokens > ctx_limit:
        cost.admissible = False
        cost.reason = (
            f"peak residency of {cost.peak_resident_tokens} tokens exceeds the rank context "
            f"limit of {ctx_limit} tokens: this algorithm cannot run, not merely slowly"
        )
    return cost


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


#: Latency differences below this relative band are treated as ties, and broken
#: on price.  The alpha-beta model is not accurate to better than a few percent,
#: and a 5% latency saving does not justify a 6x token bill.
TIE_BAND = 0.05


def _better(candidate: Cost, incumbent: Cost) -> bool:
    """Lexicographic comparison: latency outside the tie band, then price, then peak."""
    a, b = candidate.total_seconds, incumbent.total_seconds
    scale = max(a, b, 1e-9)
    if abs(a - b) / scale > TIE_BAND:
        return a < b
    return (candidate.price_units, candidate.peak_resident_tokens) < (
        incumbent.price_units,
        incumbent.peak_resident_tokens,
    )


@dataclass
class Decision:
    collective: str
    chosen: str
    p: int
    tokens: int
    considered: list[Cost] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "collective": self.collective,
            "chosen": self.chosen,
            "p": self.p,
            "tokens": self.tokens,
            "rule": self.rule,
            "considered": [c.to_dict() for c in self.considered],
            "rejected": self.rejected,
        }


def select_algorithm(
    collective: str,
    p: int,
    *,
    tokens: int = 0,
    op: Op | None = None,
    root: int = 0,
    ctx_limit: int | None = None,
    gamma_s: float | None = None,
    inline: bool = False,
    override: str | None = None,
) -> Decision:
    """Choose a schedule, and record why every alternative was not chosen.

    An implementation MUST let the caller override the choice, and MUST be able to
    explain it.  For a semantic reduction the algorithm is a *quality* decision,
    not merely a cost decision, so hiding it behind a tuning table --- which is
    exactly what MPI does, correctly, for floating-point addition --- would be
    wrong here.
    """
    if collective not in CATALOGUE:
        raise err("AMPI_ERR_ARG", f"unknown collective {collective!r}", known=sorted(CATALOGUE))

    agent_op = op is not None and op.evaluator == "agent"
    if gamma_s is None:
        gamma_s = 30.0 if agent_op else 0.0

    decision = Decision(collective, "", p, tokens)
    best: tuple[float, Cost, str] | None = None

    for algorithm in CATALOGUE[collective]:
        try:
            sched = build_schedule(
                collective, algorithm, p, root=root, tokens=tokens, inline=inline
            )
        except Exception as exc:  # pragma: no cover - defensive
            decision.rejected[algorithm] = f"could not be constructed: {exc}"
            continue

        if op is not None:
            try:
                op.check_schedule(algorithm, root=root)
            except err("AMPI_SUCCESS", "").__class__ as exc:
                decision.rejected[algorithm] = exc.message
                continue

        cost = cost_of(sched, gamma_s=gamma_s, ctx_limit=ctx_limit)
        decision.considered.append(cost)
        if not cost.admissible:
            decision.rejected[algorithm] = cost.reason
            continue
        # Two schedules that finish at the same time are not equally good.  Break
        # near-ties on latency by price -- total operator applications -- and then
        # by peak residency, because a schedule that leaves a rank near its context
        # limit is fragile even when it is fast.  TIE_BAND is 5%: inside it, the
        # latency model is not accurate enough to justify paying six times more.
        if best is None or _better(cost, best[1]):
            best = (cost.total_seconds, cost, algorithm)

    if override is not None:
        if override not in CATALOGUE[collective]:
            raise err(
                "AMPI_ERR_ARG",
                f"{override!r} is not an algorithm for {collective!r}",
                known=list(CATALOGUE[collective]),
            )
        if op is not None:
            op.check_schedule(override, root=root)
        decision.chosen = override
        decision.rule = "caller override"
        return decision

    if best is None:
        raise err(
            "AMPI_ERR_CTX_EXCEEDED",
            f"no admissible algorithm for {collective} at p={p} with {tokens}-token payloads",
            hint="Send handles instead of bodies (--no-inline), or use a view.",
            rejected=decision.rejected,
        )

    decision.chosen = best[2]
    decision.rule = _rule_for(collective, best[2], agent_op, p, tokens)
    return decision


def _rule_for(collective: str, chosen: str, agent_op: bool, p: int, tokens: int) -> str:
    if collective == "barrier":
        if chosen == "central":
            # Worth stating plainly, because it contradicts MPI practice.  In MPI
            # a counting barrier is avoided at scale: the root is a genuine
            # bottleneck on a point-to-point network, so dissemination's
            # ceil(log2 p) rounds win as p grows.  Here the control plane is a
            # shared medium that every rank can read, so "centralised" costs
            # nothing extra -- 2(p-1) device operations against dissemination's
            # p*log2(p) -- and it is the only algorithm that can name the ranks
            # that have not arrived.  The crossover MPI has does not exist here;
            # dissemination is retained for devices whose control plane is not
            # shared.
            return (
                "counting barrier: on a shared control plane it costs 2(p-1) operations "
                f"against dissemination's p*log2(p) = {p * math.ceil(math.log2(max(2, p)))}, and it "
                "is the only algorithm that can name the ranks that have not arrived"
            )
        return "dissemination: ceil(log2 p) rounds, for a device without a shared control plane"
    if agent_op:
        if chosen in ("binomial", "reduce_bcast"):
            return (
                "the operator is applied by an executor, so selection minimises applications "
                "on the critical path; a tree gives ceil(log2 p) against a chain's p-1"
            )
        if chosen == "chain":
            return "the operator's declared algebra licenses no tree"
    if chosen == "flat":
        return (
            "the runtime can apply the operator, so the journal folds every contribution in "
            "one round with no messages: a tree would add rounds and buy nothing"
        )
    return f"lowest modelled cost at p={p}, {tokens} tokens"


def explain_selection(
    collective: str,
    p: int,
    *,
    tokens: int = 4000,
    gammas: tuple[float, ...] = (0.0, 0.001, 0.1, 1.0, 30.0),
    op: Op | None = None,
    ctx_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Sweep gamma to show where MPI's answer and ours diverge.

    This is the table behind the paper's central claim.  At ``gamma = 0`` the
    winners are MPI's; as gamma grows past roughly ``alpha`` the ordering changes,
    and by ``gamma = 30 s`` --- one executor turn --- the critical-path term
    dominates everything else.
    """
    out = []
    for gamma in gammas:
        row: dict[str, Any] = {"gamma_s": gamma}
        best, best_cost = None, None
        for algorithm in CATALOGUE[collective]:
            sched = build_schedule(collective, algorithm, p, tokens=tokens, inline=False)
            if op is not None:
                try:
                    op.check_schedule(algorithm)
                except Exception:
                    continue
            cost = cost_of(sched, gamma_s=gamma, ctx_limit=ctx_limit)
            row[algorithm] = round(cost.total_seconds, 6) if cost.admissible else None
            if cost.admissible and (best_cost is None or cost.total_seconds < best_cost):
                best, best_cost = algorithm, cost.total_seconds
        row["winner"] = best
        out.append(row)
    return out
