"""Reduction operators and the algebra that constrains collective algorithms.

MPI requires that every operator passed to MPI_Reduce be associative, and asks
the user to declare whether it is also commutative (the ``commute`` flag of
MPI_Op_create).  The standard then grants the implementation licence to
evaluate the reduction in *any* order consistent with those properties, which
is exactly why a tree reduction may legally be substituted for a linear one and
why MPI does not promise bitwise-reproducible floating-point sums.

AgentMPI inherits the mechanism and inverts its significance.  For an agent
system the interesting operators are things like "merge these two partial
glossaries" or "reconcile these two API designs", and those are, at best,
*approximately* associative: merging A into B and then C rarely yields exactly
what merging B into C and then A yields.  So we make the algebra an explicit,
machine-checked part of the operator declaration, and we let it *constrain the
algorithm*:

    associative and commutative  -> any tree, any order      (depth O(log p))
    associative only             -> any tree, rank order      (depth O(log p))
    neither                      -> canonical linear order    (depth O(p))

That single rule is the reason AgentMPI can offer a logarithmic-depth semantic
allreduce at all and, when the operator does not earn it, refuse to.

Operators come in two kinds.  A STRUCTURAL operator executes inside the
library: it is free, exact, and reproducible.  A SEMANTIC operator requires a
language model, so the library cannot evaluate it; instead it suspends the
collective and performs an *upcall* to the rank that invoked it, which is the
direct analogue of MPI invoking a user-registered MPI_User_function --- the
difference being that here the callback runs inside an LLM turn and costs
tokens.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import util
from ..constants import OP_SEMANTIC, OP_STRUCTURAL
from ..errors import AmpiArgError


@dataclass(frozen=True)
class Op:
    """An AgentMPI reduction operator declaration."""

    name: str
    kind: str
    associative: bool
    commutative: bool
    doc: str
    fn: Callable[[Any, Any], Any] | None = None
    finalize: Callable[[Any], Any] | None = None
    identity: Any = None
    prompt: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_semantic(self) -> bool:
        return self.kind == OP_SEMANTIC

    def allows_tree(self) -> bool:
        return self.associative

    def allows_reorder(self) -> bool:
        return self.associative and self.commutative

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "associative": self.associative,
            "commutative": self.commutative,
            "doc": self.doc,
            "prompt": self.prompt,
        }


# ---------------------------------------------------------------------------
# Structural operator implementations
# ---------------------------------------------------------------------------


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else util.dumps(value)


def _op_concat(a: Any, b: Any) -> str:
    """Ordered concatenation: associative, deliberately NOT commutative.

    This is the operator that proves the algebra matters.  Concatenating
    chapter translations is associative, so a tree reduction is legal; it is
    not commutative, so the tree must respect rank order.  Getting that wrong
    silently shuffles a book.
    """
    left, right = _as_text(a), _as_text(b)
    if not left:
        return right
    if not right:
        return left
    return f"{left}\n{right}"


def _op_bag(a: Any, b: Any) -> list[Any]:
    """Multiset union.  Associative and commutative."""
    la = a if isinstance(a, list) else ([] if a is None else [a])
    lb = b if isinstance(b, list) else ([] if b is None else [b])
    return la + lb


def _op_union(a: Any, b: Any) -> list[Any]:
    """Set union with a canonical order, so the result is order independent."""
    merged = {util.dumps(x): x for x in _op_bag(a, b)}
    return [merged[k] for k in sorted(merged)]


def _deep_merge(a: Any, b: Any) -> Any:
    """Deterministic deep merge; associative and commutative by construction.

    Dicts union their keys and recurse.  Lists become canonically sorted set
    unions.  Conflicting scalars resolve to the lexicographically smaller
    canonical encoding --- an arbitrary but *total, associative, commutative*
    rule, which is what buys the right to use a tree.  A conflict-resolution
    rule that consulted arrival order would forfeit that right.
    """
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: _deep_merge(a.get(k), b.get(k)) for k in sorted(set(a) | set(b))}
    if isinstance(a, list) and isinstance(b, list):
        return _op_union(a, b)
    if a == b:
        return a
    return a if util.dumps(a) <= util.dumps(b) else b


def _op_merge_json(a: Any, b: Any) -> Any:
    return _deep_merge(a, b)


def _num(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "value" in value:
        return _num(value["value"])
    raise AmpiArgError(f"operand is not numeric: {value!r}")


def _op_sum(a: Any, b: Any) -> float:
    return _num(a) + _num(b)


def _op_max(a: Any, b: Any) -> Any:
    return a if _num(a) >= _num(b) else b


def _op_min(a: Any, b: Any) -> Any:
    return a if _num(a) <= _num(b) else b


def _op_maxloc(a: Any, b: Any) -> Any:
    """MAXLOC: maximum score together with the rank that produced it.

    Ties break toward the lower rank, which keeps the operator commutative;
    MPI's MAXLOC makes exactly the same choice for exactly the same reason.
    """
    if a is None:
        return b
    if b is None:
        return a
    sa, sb = _num(a.get("score")), _num(b.get("score"))
    if sa > sb:
        return a
    if sb > sa:
        return b
    return a if a.get("rank", 0) <= b.get("rank", 0) else b


def _op_minloc(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a
    sa, sb = _num(a.get("score")), _num(b.get("score"))
    if sa < sb:
        return a
    if sb < sa:
        return b
    return a if a.get("rank", 0) <= b.get("rank", 0) else b


def _op_land(a: Any, b: Any) -> bool:
    return bool(a) and bool(b)


def _op_lor(a: Any, b: Any) -> bool:
    return bool(a) or bool(b)


def _vote_finalize(bag: Any) -> dict[str, Any]:
    """Majority vote, computed as multiset-union followed by an argmax.

    Pairwise majority is not associative, so we do not implement it pairwise.
    Instead the reduction accumulates a commutative multiset and the *finalize*
    step takes the mode.  This monoid-plus-projection shape has no MPI
    counterpart (MPI reductions have no finalize hook) but is the honest way to
    express quorum-style agent operators without lying about associativity.
    """
    items = bag if isinstance(bag, list) else ([] if bag is None else [bag])
    tally: dict[str, int] = {}
    examples: dict[str, Any] = {}
    for item in items:
        key = util.dumps(item).strip().lower()
        tally[key] = tally.get(key, 0) + 1
        examples.setdefault(key, item)
    if not tally:
        return {"winner": None, "votes": 0, "total": 0, "tally": {}, "unanimous": False}
    best = max(sorted(tally), key=lambda k: tally[k])
    total = sum(tally.values())
    return {
        "winner": examples[best],
        "votes": tally[best],
        "total": total,
        "agreement": tally[best] / total,
        "unanimous": tally[best] == total,
        "tally": {k: tally[k] for k in sorted(tally)},
    }


def _topk_factory(k: int) -> Callable[[Any, Any], Any]:
    def _op_topk(a: Any, b: Any) -> list[Any]:
        pool = _op_bag(a, b)
        ranked = sorted(
            pool,
            key=lambda x: (-_num(x.get("score", 0) if isinstance(x, dict) else 0), util.dumps(x)),
        )
        return ranked[:k]

    return _op_topk


# ---------------------------------------------------------------------------
# The predefined operator table
# ---------------------------------------------------------------------------

PREDEFINED: dict[str, Op] = {
    op.name: op
    for op in [
        Op(
            "AMPI_CONCAT",
            OP_STRUCTURAL,
            associative=True,
            commutative=False,
            doc="Ordered text concatenation. Tree-legal, order-sensitive.",
            fn=_op_concat,
            identity="",
        ),
        Op(
            "AMPI_BAG",
            OP_STRUCTURAL,
            associative=True,
            commutative=True,
            doc="Multiset union of contributions.",
            fn=_op_bag,
            identity=[],
        ),
        Op(
            "AMPI_UNION",
            OP_STRUCTURAL,
            associative=True,
            commutative=True,
            doc="Canonically ordered set union (deduplicating).",
            fn=_op_union,
            identity=[],
        ),
        Op(
            "AMPI_MERGE_JSON",
            OP_STRUCTURAL,
            associative=True,
            commutative=True,
            doc="Deterministic deep merge of JSON objects; scalar conflicts "
            "resolve to the lexicographically smaller encoding.",
            fn=_op_merge_json,
            identity=None,
        ),
        Op("AMPI_SUM", OP_STRUCTURAL, True, True, "Numeric sum.", _op_sum, identity=0),
        Op("AMPI_MAX", OP_STRUCTURAL, True, True, "Numeric maximum.", _op_max),
        Op("AMPI_MIN", OP_STRUCTURAL, True, True, "Numeric minimum.", _op_min),
        Op(
            "AMPI_MAXLOC",
            OP_STRUCTURAL,
            True,
            True,
            "Maximum {score, rank, payload}; ties break to the lower rank.",
            _op_maxloc,
        ),
        Op("AMPI_MINLOC", OP_STRUCTURAL, True, True, "Minimum {score, rank, payload}.", _op_minloc),
        Op("AMPI_LAND", OP_STRUCTURAL, True, True, "Logical and (used by agree).", _op_land, identity=True),
        Op("AMPI_LOR", OP_STRUCTURAL, True, True, "Logical or.", _op_lor, identity=False),
        Op(
            "AMPI_VOTE",
            OP_STRUCTURAL,
            True,
            True,
            "Quorum vote: commutative multiset accumulation with an argmax "
            "finalize step. The agent analogue of algorithm-based fault "
            "tolerance -- disagreement is detected, not averaged away.",
            fn=_op_bag,
            finalize=_vote_finalize,
            identity=[],
        ),
        Op(
            "AMPI_TOPK",
            OP_STRUCTURAL,
            True,
            True,
            "Keep the k highest-scoring contributions (k from op params).",
            fn=_topk_factory(5),
        ),
        # -- semantic operators: evaluated by an LLM through an upcall -----
        Op(
            "AMPI_SYNTHESIZE",
            OP_SEMANTIC,
            associative=False,
            commutative=False,
            doc="Fuse two artifacts into one coherent artifact.",
            prompt=(
                "You are evaluating one step of an AgentMPI semantic reduction. "
                "Fuse the two operands below into a SINGLE coherent artifact that "
                "preserves every substantive claim from both and removes duplication. "
                "Do not summarise away detail; this result will be reduced again."
            ),
        ),
        Op(
            "AMPI_RECONCILE",
            OP_SEMANTIC,
            associative=False,
            commutative=False,
            doc="Resolve contradictions between two artifacts, recording the resolution.",
            prompt=(
                "You are evaluating one step of an AgentMPI semantic reduction. "
                "The two operands may contradict each other. Produce a single "
                "reconciled artifact. Where they conflict, choose the better-"
                "justified option and add a short 'RESOLVED:' note explaining the "
                "choice. Never silently drop a conflict."
            ),
        ),
        Op(
            "AMPI_SUMMARIZE",
            OP_SEMANTIC,
            associative=True,
            commutative=True,
            doc="Lossy semantic compaction of two artifacts; declared associative "
            "because the result is explicitly approximate and order effects are "
            "within the operator's stated tolerance.",
            prompt=(
                "You are evaluating one step of an AgentMPI semantic reduction. "
                "Produce a compact summary that covers both operands. The output "
                "must be no longer than the longer operand."
            ),
        ),
        Op(
            "AMPI_CRITIQUE_MERGE",
            OP_SEMANTIC,
            associative=False,
            commutative=False,
            doc="Critique the second operand against the first, then merge.",
            prompt=(
                "You are evaluating one step of an AgentMPI semantic reduction. "
                "Treat operand A as the incumbent and operand B as the challenger. "
                "Critique B against A, then emit the merged artifact that survives "
                "the critique."
            ),
        ),
    ]
}


def get_op(name: str, params: dict[str, Any] | None = None) -> Op:
    """Look up a predefined operator, applying any parameters."""
    key = name if name.startswith("AMPI_") else f"AMPI_{name.upper()}"
    op = PREDEFINED.get(key) or PREDEFINED.get(name)
    if op is None:
        raise AmpiArgError(
            f"unknown reduction operator {name!r}; known: {sorted(PREDEFINED)}",
            known=sorted(PREDEFINED),
        )
    if params and op.name == "AMPI_TOPK" and "k" in params:
        return Op(**{**op.__dict__, "fn": _topk_factory(int(params["k"]))})
    return op


def op_create(
    name: str,
    fn: Callable[[Any, Any], Any],
    associative: bool,
    commutative: bool,
    doc: str = "",
) -> Op:
    """AMPI_Op_create: register a user-defined structural operator.

    The associativity and commutativity flags are not documentation.  The
    collective layer reads them to decide which algorithms are admissible, so
    an incorrect declaration is the AgentMPI analogue of lying to
    MPI_Op_create about ``commute``: the program still runs, and the answer
    silently depends on the process count.
    """
    op = Op(name, OP_STRUCTURAL, associative, commutative, doc or name, fn=fn)
    PREDEFINED[name] = op
    return op


def reduce_sequence(op: Op, operands: list[Any]) -> Any:
    """Fold a list left-to-right with a structural operator."""
    if op.is_semantic:
        raise AmpiArgError(f"{op.name} is semantic and cannot be evaluated in the library")
    if not operands:
        return op.identity
    acc = operands[0]
    for operand in operands[1:]:
        acc = op.fn(acc, operand)  # type: ignore[misc]
    return op.finalize(acc) if op.finalize else acc
