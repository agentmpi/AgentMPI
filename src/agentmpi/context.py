"""Context accounting, admission control, and budgets.

This module is where AgentMPI departs most sharply from MPI, so it is worth
stating the difference precisely.

In MPI, a receive buffer is *reusable*: a rank may receive a terabyte over
the life of a job while never holding more than a megabyte at once.  Memory
pressure is a function of the *peak live set*.  In an agent, every token
that is read is retained for the remainder of the conversation; the pressure
is a function of the *cumulative ingest*.  A rank that receives 200 messages
of 5k tokens each has spent a million tokens whether or not it ever needed
two of them simultaneously.  Context is therefore not memory, it is a
consumable, and the right systems analogue is not ``malloc`` but a **budget
with admission control** -- closer to an energy budget or an I/O quota than
to an address space.

Three mechanisms follow from that observation, and all three are
implemented here:

**Admission control.**  A receive is not merely a data movement, it is an
allocation against a quota that cannot be freed.  ``ContextBudget.admit``
decides, *before* the payload is materialised, whether it may enter.

**Eviction and compaction.**  Because the resource is cumulative, the only
way to continue past the budget is to rewrite history: replace a span of
consumed context with a smaller summary of it.  This is a garbage collector
whose "liveness" analysis is semantic, so it must be under the harness's
control -- the runtime supplies the mechanism and the accounting, and the
harness supplies the policy.  ``ContextBudget.compact`` performs the
accounting side.

**Capacity-aware collectives.**  If every datatype carries a token bound
(see :mod:`agentmpi.datatypes`), the runtime can compute the peak ingest a
collective will impose on any participant *before* running it, and choose an
algorithm -- or a tree degree -- that fits.  :func:`safe_fanout` and
:func:`plan_reduction` implement that calculation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable

from .errors import BudgetError, ContextOverflowError
from .tokens import count_tokens, truncate_to_tokens


@dataclass
class ContextBudget:
    """Per-rank accounting of the consumable context resource.

    ``capacity`` is the model's context window.  ``reserve`` is the fraction
    held back for the agent's own reasoning and output -- an agent whose
    window is exactly filled by incoming messages cannot do any work, so the
    usable ingest budget is strictly smaller than the window.
    """

    capacity: int = 128_000
    reserve_fraction: float = 0.35
    ingested: int = 0
    emitted: int = 0
    compacted_away: int = 0
    admissions: int = 0
    rejections: int = 0
    digests: int = 0
    #: Optional hard cap on cumulative ingest across the whole run, used to
    #: bound cost.  ``None`` means "bounded only by the window".
    lifetime_tokens: int | None = None
    currency_spent: float = 0.0
    currency_budget: float | None = None

    @property
    def usable(self) -> int:
        return max(int(self.capacity * (1.0 - self.reserve_fraction)), 1)

    @property
    def live(self) -> int:
        """Tokens currently occupying the window."""
        return max(self.ingested - self.compacted_away, 0)

    @property
    def headroom(self) -> int:
        return max(self.usable - self.live, 0)

    @property
    def pressure(self) -> float:
        return self.live / self.usable if self.usable else 1.0

    def admit(self, tokens: int, *, strict: bool = False) -> None:
        """Charge ``tokens`` against the budget or refuse them."""
        if self.lifetime_tokens is not None and self.ingested + tokens > self.lifetime_tokens:
            self.rejections += 1
            raise BudgetError(
                "lifetime token budget exhausted",
                ingested=self.ingested,
                requested=tokens,
                limit=self.lifetime_tokens,
            )
        if tokens > self.headroom:
            self.rejections += 1
            raise ContextOverflowError(
                "message does not fit the receiver's remaining context",
                requested=tokens,
                headroom=self.headroom,
                live=self.live,
                usable=self.usable,
            )
        if strict and tokens > self.usable:
            self.rejections += 1
            raise ContextOverflowError("message larger than the whole usable window")
        self.ingested += tokens
        self.admissions += 1

    def can_admit(self, tokens: int) -> bool:
        return tokens <= self.headroom and (
            self.lifetime_tokens is None or self.ingested + tokens <= self.lifetime_tokens
        )

    def emit(self, tokens: int) -> None:
        self.emitted += tokens

    def spend(self, amount: float) -> None:
        self.currency_spent += amount
        if self.currency_budget is not None and self.currency_spent > self.currency_budget:
            raise BudgetError(
                "currency budget exhausted",
                spent=self.currency_spent,
                limit=self.currency_budget,
            )

    def compact(self, freed: int, cost: int = 0) -> None:
        """Record that ``freed`` tokens were replaced by ``cost`` tokens."""
        self.compacted_away += max(freed - cost, 0)
        self.digests += 1

    def snapshot(self) -> dict[str, float | int]:
        return {
            "capacity": self.capacity,
            "usable": self.usable,
            "ingested": self.ingested,
            "emitted": self.emitted,
            "live": self.live,
            "compacted_away": self.compacted_away,
            "pressure": round(self.pressure, 4),
            "admissions": self.admissions,
            "rejections": self.rejections,
            "digests": self.digests,
            "currency_spent": round(self.currency_spent, 6),
        }

    def to_json(self) -> str:
        return json.dumps(self.snapshot())


# --------------------------------------------------------------------------
# Digest functions
# --------------------------------------------------------------------------

DigestFn = Callable[[str, int], str]


def truncating_digest(text: str, budget: int) -> str:
    return truncate_to_tokens(text, budget)


def head_tail_digest(text: str, budget: int, head_frac: float = 0.6) -> str:
    """Keep a head and a tail.

    Justified by the *lost in the middle* effect: models attend most
    reliably to the beginning and end of a long input, so when forced to
    discard, discarding the middle preserves the most usable signal.
    """
    if count_tokens(text) <= budget:
        return text
    lines = text.splitlines()
    if len(lines) < 4:
        return truncate_to_tokens(text, budget)
    head_budget = int(budget * head_frac)
    tail_budget = max(budget - head_budget, 1)
    head: list[str] = []
    used = 0
    for line in lines:
        cost = count_tokens(line) + 1
        if used + cost > head_budget:
            break
        head.append(line)
        used += cost
    tail: list[str] = []
    used = 0
    for line in reversed(lines[len(head):]):
        cost = count_tokens(line) + 1
        if used + cost > tail_budget:
            break
        tail.append(line)
        used += cost
    tail.reverse()
    dropped = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... [{dropped} lines elided by AMPI digest] ..."] + tail)


def structural_digest(text: str, budget: int) -> str:
    """Keep structural anchors (headings, signatures) and elide bodies.

    For source code and structured documents this retains far more usable
    information per token than truncation, because the anchors are what a
    downstream agent needs in order to *ask* for the right detail later.
    """
    if count_tokens(text) <= budget:
        return text
    anchors: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("#")
            or stripped.startswith(("def ", "class ", "func ", "fn ", "type ", "interface "))
            or stripped.endswith(":")
            and len(stripped) < 120
            or stripped.startswith(("export ", "public ", "private ", "- ", "* "))
        ):
            anchors.append(line)
    joined = "\n".join(anchors)
    if not anchors or count_tokens(joined) > budget:
        return head_tail_digest(text, budget)
    return joined


DIGESTS: dict[str, DigestFn] = {
    "truncate": truncating_digest,
    "head_tail": head_tail_digest,
    "structural": structural_digest,
}


# --------------------------------------------------------------------------
# Capacity-aware collective planning
# --------------------------------------------------------------------------

def safe_fanout(budget: int, item_tokens: int, working_set: int = 0) -> int:
    """Largest number of inbound messages a rank can ingest in one step.

    This is the quantity that determines the *degree* of a capacity-aware
    reduction tree.  In MPI the degree of a reduction tree is chosen to
    trade latency against bandwidth; in AgentMPI it is chosen to trade
    latency against context, and the constraint is hard: exceed it and the
    rank cannot perform its reduction at all.
    """
    usable = max(budget - working_set, 0)
    if item_tokens <= 0:
        return max(usable, 1)
    return max(usable // item_tokens, 1)


@dataclass
class ReductionPlan:
    """A capacity-feasible reduction schedule."""

    n: int
    fanout: int
    rounds: int
    peak_ingest: int
    feasible: bool
    reason: str = ""
    schedule: list[list[tuple[int, list[int]]]] = field(default_factory=list)
    """Per round, a list of ``(parent, children)`` pairs."""

    def describe(self) -> str:
        status = "feasible" if self.feasible else f"INFEASIBLE ({self.reason})"
        return (
            f"ReductionPlan(n={self.n}, k={self.fanout}, rounds={self.rounds}, "
            f"peak_ingest={self.peak_ingest} tok, {status})"
        )


def plan_reduction(
    n: int,
    item_tokens: int,
    budget: int,
    *,
    output_tokens: int | None = None,
    working_set: int = 0,
) -> ReductionPlan:
    """Plan a k-ary reduction tree over ``n`` ranks that fits ``budget``.

    ``item_tokens`` is the size of one contribution; ``output_tokens`` is the
    size bound of the operator's output (its *contraction*).  An operator
    whose output is no smaller than its inputs cannot be reduced in a tree
    of depth greater than one without unbounded growth -- which is exactly
    why AgentMPI requires reduction operators to declare a bound.
    """
    if n <= 0:
        return ReductionPlan(n, 1, 0, 0, False, "empty communicator")
    if n == 1:
        return ReductionPlan(1, 1, 0, item_tokens, True)
    out = item_tokens if output_tokens is None else output_tokens
    m = max(item_tokens, out)

    if item_tokens + working_set > budget:
        return ReductionPlan(
            n, 1, 0, item_tokens, False,
            f"a single contribution of {item_tokens} tokens does not fit a "
            f"{budget}-token budget (working set {working_set})",
        )
    if out > item_tokens and n > safe_fanout(budget, m, working_set):
        return ReductionPlan(
            n, 1, 0, out, False,
            "reduction operator is not contracting (output bound exceeds input "
            "bound), so a multi-level tree has unbounded ingest; declare a "
            "bounded output type or use a flat reduction",
        )

    # Choose the tree degree by *cumulative* ingest at the root, not by
    # per-round ingest.  This is the distinction that makes capacity planning
    # for agents unlike anything in MPI.  An MPI rank that receives k-1
    # buffers per round reuses the same memory every round, so its peak is
    # one round's worth and depth is free.  An agent retains everything it
    # reads, so the root of a depth-d tree pays (k-1)*d contributions over
    # the life of the reduction.  Planning against the per-round figure
    # yields trees that look feasible, run two rounds, and then exhaust the
    # root -- which is exactly the failure this planner exists to prevent.
    best: tuple[int, int, int] | None = None
    for k in range(2, n + 1):
        rounds = max(int(math.ceil(math.log(n, k) - 1e-9)), 1)
        cumulative = (k - 1) * rounds * m + working_set
        if cumulative > budget:
            continue
        candidate = (rounds, cumulative, k)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    if best is None:
        binary_cost = max(int(math.ceil(math.log2(n))), 1) * m
        return ReductionPlan(
            n, 2, 0, binary_cost, False,
            f"no tree degree fits: even a binary tree over {n} ranks costs "
            f"{binary_cost} tokens of cumulative ingest at the root, over the "
            f"{budget}-token budget; lower the operator's output bound or add "
            f"a pre-aggregation stage",
        )
    rounds, cumulative, k = best

    schedule: list[list[tuple[int, list[int]]]] = []
    active = list(range(n))
    while len(active) > 1:
        round_pairs: list[tuple[int, list[int]]] = []
        next_active: list[int] = []
        for i in range(0, len(active), k):
            group = active[i: i + k]
            round_pairs.append((group[0], group[1:]))
            next_active.append(group[0])
        schedule.append(round_pairs)
        active = next_active
    return ReductionPlan(n, k, len(schedule), cumulative, True, schedule=schedule)


def peak_ingest_bcast(n: int, degree: int, item_tokens: int) -> int:
    """Peak ingest of one participant in a ``degree``-ary broadcast tree."""
    return item_tokens  # each node receives the message exactly once


def peak_ingest_allgather(n: int, item_tokens: int) -> int:
    """Peak ingest of allgather: everybody ends up holding everything.

    Reported explicitly because it is the collective that most reliably
    causes context exhaustion in practice, and the runtime warns on it.
    """
    return (n - 1) * item_tokens


def feasible_allgather(n: int, item_tokens: int, budget: int) -> bool:
    return peak_ingest_allgather(n, item_tokens) <= budget
