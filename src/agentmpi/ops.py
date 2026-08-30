"""Reduction operators, and the algebra that constrains how they may be applied.

This module contains the sharpest technical difference between MPI and AgentMPI.

MPI requires that a user reduction operator passed to ``MPI_Op_create`` be
associative, and permits the implementation to evaluate it in any order and any
tree shape.  Commutativity is declared separately, via the ``commute`` flag,
and unlocks additional reorderings.  The standard's guarantee is deliberately
weak: an implementation may choose different trees for different process counts
or even different runs, which is why floating-point ``MPI_SUM`` is famously not
bitwise reproducible.  Practitioners live with this because the *magnitude* of
the discrepancy is bounded by rounding error.

Semantic reduction operators — "summarise these eight chapter summaries",
"merge these six glossaries", "pick the best of these four designs" — are
implemented by a language model.  They are:

* **not associative**, and not even approximately so in any uniform sense;
* **lossy**, so error compounds with the *depth* of the reduction tree rather
  than with the number of operations;
* **expensive**, so the number of operations matters for cost even though it
  does not matter much for quality;
* **non-deterministic**, so the same tree evaluated twice gives different
  answers.

Two consequences shape the design.  First, an operator must *declare* its
algebraic strength (:class:`~agentmpi.constants.Associativity`), and the
runtime refuses to apply a tree algorithm to an operator declared
non-associative — the analogue of MPI refusing to reorder a non-commutative
operator, but stricter.  Second, because quality degrades with tree *depth*
while cost grows with tree *width*, algorithm selection for a semantic
reduction is a genuine quality/latency trade-off rather than the pure cost
minimisation it is in MPI.  A linear chain over *p* ranks has depth *p−1* and
latency *p−1*; a binomial tree has depth and latency ⌈log₂ p⌉.  In MPI the tree
wins outright.  In AgentMPI the tree wins on latency and *may* lose on
fidelity, and which effect dominates is an empirical question this repository
measures (see ``benchmarks/bench_reduce.py``).

Operators additionally declare an ``idempotent`` flag and an ``identity``.
Idempotence matters because a rank may be re-executed after a suspected
failure, and a non-idempotent merge applied twice double-counts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .constants import Associativity
from .errors import AmpiUsageError

#: Signature of a binary reduction kernel.  ``ctx`` carries the invoking rank,
#: the fold depth so far, and an executor handle for agent-implemented
#: operators.
ReduceFn = Callable[[Any, Any, "ReduceContext"], Any]


@dataclass
class ReduceContext:
    """Context handed to a reduction kernel."""

    rank: int
    #: Depth of the current fold in the reduction tree.  An agent-implemented
    #: operator can use this to be more conservative deeper in the tree, where
    #: it is composing already-lossy inputs.
    depth: int = 0
    #: Number of leaf contributions summarised by the accumulator so far.  A
    #: summarisation operator that knows it is standing in for 32 chapters can
    #: allocate its output budget accordingly, which is how AgentMPI avoids the
    #: pathology where a tree reduce compresses 32 inputs into the same number
    #: of tokens as it compresses 2.
    weight: int = 1
    agent: Callable[..., Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Op:
    """A reduction operator with declared algebraic properties.

    Parameters
    ----------
    name:
        Identifier used in traces and in algorithm-selection decisions.
    fn:
        Binary kernel ``fn(acc, val, ctx) -> acc'``.
    commutative:
        Whether ``fn(a, b) ≡ fn(b, a)``.  Non-commutative operators force the
        runtime to preserve rank order, exactly as ``MPI_Op_create(..., commute=0)``
        does.  Order-preserving is the correct declaration for anything that
        concatenates or that treats earlier ranks as authoritative.
    associativity:
        ``EXACT`` permits any tree.  ``APPROX`` permits trees but the runtime
        records fold depth on every message so drift is attributable.  ``NONE``
        restricts the runtime to the serial left fold.
    idempotent:
        Whether ``fn(a, a) ≡ a``.  Required for safe re-execution after a
        suspected but unconfirmed failure; without it, at-least-once delivery
        of a contribution corrupts the result.
    identity:
        Value returned for an empty reduction.  Its existence is what makes a
        reduction over a *shrunken* communicator well-defined when a subtree
        has been entirely lost.
    """

    name: str
    fn: ReduceFn
    commutative: bool = True
    associativity: Associativity = Associativity.EXACT
    idempotent: bool = False
    identity: Any = None
    #: Estimated token cost of one application; used by the cost model.
    cost_tokens: int = 0

    def __call__(self, acc: Any, val: Any, ctx: ReduceContext | None = None) -> Any:
        return self.fn(acc, val, ctx or ReduceContext(rank=-1))

    @property
    def tree_legal(self) -> bool:
        """Whether the runtime may use a logarithmic-depth algorithm."""
        return self.associativity in (Associativity.EXACT, Associativity.APPROX)

    @property
    def lossy(self) -> bool:
        return self.associativity is not Associativity.EXACT

    def fold(self, values: Iterable[Any], ctx: ReduceContext | None = None) -> Any:
        """Serial left fold.  The reference semantics of every reduction.

        Every collective algorithm in :mod:`agentmpi.algorithms` must produce
        this result when the operator is ``EXACT``; the fold is therefore the
        oracle the test suite checks tree algorithms against.
        """
        ctx = ctx or ReduceContext(rank=-1)
        acc: Any = None
        started = False
        for i, v in enumerate(values):
            if not started:
                acc, started = v, True
                continue
            ctx.depth = i
            ctx.weight = i + 1
            acc = self.fn(acc, v, ctx)
        return acc if started else self.identity

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "commutative": self.commutative,
            "associativity": self.associativity.value,
            "idempotent": self.idempotent,
            "tree_legal": self.tree_legal,
            "lossy": self.lossy,
        }


# ------------------------------------------------------------- exact primitives
# These exist for the same reason MPI ships MPI_SUM: a large fraction of real
# reductions in a harness are over *metadata* (token counts, test pass counts,
# boolean health flags) rather than over prose, and those reductions should be
# exact, cheap, and tree-parallel.


def _sum(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    return a + b


def _max(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    return a if a >= b else b


def _min(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    return a if a <= b else b


def _land(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    return bool(a) and bool(b)


def _lor(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    return bool(a) or bool(b)


def _concat(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    if isinstance(a, list) or isinstance(b, list):
        return (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b])
    return f"{a}{b}"


def _union(a: Any, b: Any, _ctx: ReduceContext) -> Any:
    """Set union over lists, or key-wise union over dicts.

    Exact, associative, commutative *and* idempotent, which makes it the only
    fully well-behaved way to combine agent-produced collections.  A glossary
    merged with ``UNION`` is reproducible regardless of tree shape; a glossary
    merged by an agent is not.  Harnesses should prefer ``UNION`` wherever the
    combination is genuinely set-like and reserve semantic operators for cases
    that require judgement — the analogue of preferring ``MPI_SUM`` over a
    user-defined op.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k not in out:
                out[k] = v
            elif out[k] != v:
                # Conflict: keep both, deterministically ordered, so that the
                # result is independent of fold order and a later pass (human
                # or agent) can resolve it.  Silently preferring one side would
                # make UNION order-dependent and destroy its associativity.
                existing = out[k] if isinstance(out[k], list) else [out[k]]
                incoming = v if isinstance(v, list) else [v]
                merged = sorted({json.dumps(x, sort_keys=True, default=str): x for x in existing + incoming}.values(), key=lambda x: json.dumps(x, sort_keys=True, default=str))
                out[k] = merged if len(merged) > 1 else merged[0]
        return out
    seq_a = a if isinstance(a, list) else [a]
    seq_b = b if isinstance(b, list) else [b]
    seen: dict[str, Any] = {}
    for x in list(seq_a) + list(seq_b):
        seen.setdefault(json.dumps(x, sort_keys=True, default=str), x)
    return [seen[k] for k in sorted(seen)]


SUM = Op("SUM", _sum, commutative=True, associativity=Associativity.EXACT, identity=0)
MAX = Op("MAX", _max, commutative=True, associativity=Associativity.EXACT)
MIN = Op("MIN", _min, commutative=True, associativity=Associativity.EXACT)
LAND = Op("LAND", _land, commutative=True, associativity=Associativity.EXACT, idempotent=True, identity=True)
LOR = Op("LOR", _lor, commutative=True, associativity=Associativity.EXACT, idempotent=True, identity=False)
#: Order-preserving concatenation.  Associative but *not* commutative, so the
#: runtime may use a tree but must respect rank order — the same combination
#: MPI supports for user ops declared non-commutative.
CONCAT = Op("CONCAT", _concat, commutative=False, associativity=Associativity.EXACT, identity="")
UNION = Op("UNION", _union, commutative=True, associativity=Associativity.EXACT, idempotent=True, identity=[])

BUILTIN_OPS: dict[str, Op] = {op.name: op for op in (SUM, MAX, MIN, LAND, LOR, CONCAT, UNION)}


# --------------------------------------------------------- semantic constructors


def semantic_op(
    name: str,
    prompt: str,
    *,
    commutative: bool = True,
    associativity: Associativity = Associativity.APPROX,
    output_tokens: int = 1200,
    contract: Any = None,
) -> Op:
    """Build a reduction operator whose kernel is an agent invocation.

    The kernel is called with two accumulator values and must return their
    combination.  ``prompt`` is a template that receives ``{left}``,
    ``{right}``, ``{depth}``, ``{weight}`` and ``{budget}``.

    ``weight`` is threaded deliberately.  A naive summarising reduction over 64
    ranks compresses to a fixed length at every level, so the root's answer
    reflects the last few merges far more than the first — an *unfairness* that
    has no counterpart in MPI, where ``MPI_SUM`` weights every contribution
    equally by construction.  Exposing ``weight`` lets an operator allocate its
    output budget in proportion to the number of leaves it represents, which
    recovers approximate fairness.  The benchmark in
    ``benchmarks/bench_reduce.py`` measures both variants.
    """

    def _fn(a: Any, b: Any, ctx: ReduceContext) -> Any:
        if ctx.agent is None:
            raise AmpiUsageError(
                "semantic reduction operator requires an agent executor; "
                "call comm.reduce(..., op=op) from a rank with an executor bound",
                op=name,
            )
        budget = max(300, int(output_tokens * (1 + 0.25 * max(0, ctx.depth))))
        rendered = prompt.format(
            left=_render(a),
            right=_render(b),
            depth=ctx.depth,
            weight=ctx.weight,
            budget=budget,
        )
        return ctx.agent(rendered, label=f"reduce:{name}:d{ctx.depth}", contract=contract, max_tokens=budget)

    return Op(
        name=name,
        fn=_fn,
        commutative=commutative,
        associativity=associativity,
        idempotent=False,
        cost_tokens=output_tokens,
    )


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def get_op(spec: str | Op) -> Op:
    if isinstance(spec, Op):
        return spec
    try:
        return BUILTIN_OPS[spec.upper()]
    except KeyError as exc:
        raise AmpiUsageError("unknown built-in operator", op=spec, known=sorted(BUILTIN_OPS)) from exc
