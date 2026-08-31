"""Reduction operators: their algebra, and the locality of merge.

MPI requires a user-defined reduction operator to be associative and then
reorders freely.  The guarantee is deliberately weak --- an implementation may
choose a different tree for a different process count, which is why
floating-point ``MPI_SUM`` is not bitwise reproducible --- and practitioners
accept it because the discrepancy is bounded by rounding error.

An operator implemented by a language model is not associative, is lossy, is
expensive, and is non-deterministic, and the discrepancy is not bounded by
anything.  So AgentMPI makes the algebra *declared* rather than assumed, makes
non-commutativity the default, and refuses schedules that the declaration does
not license.  That much is straightforward.

The part that is not straightforward, and that this module exists to address, is a
failure we observed rather than predicted.

    In an eight-rank reduction over interface proposals, two branches of the tree
    independently encountered the *same* conflict --- which module defines the
    shared exception type --- and resolved it in *opposite* directions.  Both
    resolutions were recorded, so the merged result contained two contradictory
    rulings under one identifier, and the tree had no way to notice: each merge
    saw a locally consistent pair of operands.  Two ranks then implemented
    different halves of the result.  Separately, the same reduction dropped four
    of eight modules from one section of its output despite an explicit
    instruction never to drop a module.

A canonical tree shape makes a reduction *reproducible*.  It does not make it
*consistent*.  These are different properties and conflating them is what the
observation exposed.

**The locality of merge.**  Let a reduction be a fold of a binary operator over a
tree ``T`` whose leaves are the ranks' contributions.  Call a predicate ``phi`` a
*global invariant* if its truth depends on the whole leaf multiset --- "every key
has exactly one ruling", "no module is dropped", "the total is conserved".  An
internal node of ``T`` sees exactly two operands.  If two nodes ``u`` and ``v``,
neither an ancestor of the other, both encounter evidence bearing on ``phi``, then
no node of ``T`` is in a position to reconcile them: their lowest common ancestor
receives ``u``'s and ``v``'s *outputs*, and the information that would reveal the
inconsistency --- what each of them decided and why --- is not in the operator's
codomain.  Enlarging the tree cannot help; only enlarging the codomain can.

**Two mechanisms.**  This module provides both, because the observation produced
two distinct failures and they need different fixes.

*Conflict lifting* (:data:`LIFT`) enlarges the operator's codomain from ``V`` to
``V x C``, where ``C`` is a set of undecided conflicts.  An operator that cannot
decide a contested item from its two operands alone must *lift* it into ``C``
rather than decide it.  ``C`` merges by set union, which is associative,
commutative and idempotent --- a semilattice --- so the set of conflicts arriving
at the root is **the same for every tree shape**.  The root then arbitrates each
one exactly once.  This buys the "one ruling per key" invariant back at a cost of
one extra operator application and ``O(|C|)`` tokens, against the ``p-1``
critical-path applications a serial chain would cost.  The proof is two lines: the
conflict component is a semilattice fold, and a semilattice fold is
shape-independent; the value component never decides a contested item, so it
cannot decide one twice.

*Invariant verification* (:attr:`Op.invariant`) catches the other class.  "Never
drop a module" is not a conflict any pair of operands exhibits --- each local
merge is individually faithful --- so lifting cannot see it.  It is a property of
the leaf multiset and the result, so it is checked once, after the reduction
closes, and reported as ``AMPI_ERR_INVARIANT`` naming the missing items.

Neither mechanism makes an agent operator correct.  They make two specific,
observed, expensive failure modes into loud errors, which is the most a protocol
can honestly claim.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..errors import err

__all__ = [
    "Op",
    "OPS",
    "get_op",
    "register_op",
    "EXACT",
    "APPROX",
    "NONE",
    "DECIDE",
    "LIFT",
    "CONFLICT_KEY",
    "lift_conflicts",
    "conflicts_of",
    "value_of",
    "merge_lifted",
    "arbitrate",
    "fold",
    "serial_fold",
    "check_invariant",
]

# Associativity classes.  EXACT means every algorithm must produce the serial
# fold's result; APPROX means results may differ but remain acceptable; NONE means
# only the serial chain is licensed.
EXACT: Literal["EXACT"] = "EXACT"
APPROX: Literal["APPROX"] = "APPROX"
NONE: Literal["NONE"] = "NONE"

# Conflict policies.
DECIDE: Literal["DECIDE"] = "DECIDE"
LIFT: Literal["LIFT"] = "LIFT"

#: The reserved key carrying the semilattice component through the wire format.
#: Keeping it inside the ordinary JSON value means no operand needs a second
#: channel, and an operator that ignores it degrades to ``DECIDE`` rather than
#: breaking.
CONFLICT_KEY = "_ampi_conflicts"


# --------------------------------------------------------------------------
# The lifted representation
# --------------------------------------------------------------------------


def value_of(operand: Any) -> Any:
    """The value component of a possibly-lifted operand."""
    if isinstance(operand, dict) and CONFLICT_KEY in operand:
        return {k: v for k, v in operand.items() if k != CONFLICT_KEY}
    return operand


def conflicts_of(operand: Any) -> dict[str, list[Any]]:
    """The conflict component, or empty."""
    if isinstance(operand, dict):
        raw = operand.get(CONFLICT_KEY)
        if isinstance(raw, dict):
            return {k: list(v) for k, v in raw.items()}
    return {}


def lift_conflicts(value: Any, conflicts: dict[str, list[Any]]) -> Any:
    """Attach a conflict set to a value, dropping the marker when it is empty."""
    conflicts = {k: v for k, v in conflicts.items() if v}
    if not conflicts:
        return value
    if not isinstance(value, dict):
        value = {"value": value}
    out = dict(value)
    out[CONFLICT_KEY] = {k: _dedupe(v) for k, v in conflicts.items()}
    return out


def _dedupe(items: list[Any]) -> list[Any]:
    seen, out = set(), []
    for it in items:
        key = json.dumps(it, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def merge_lifted(a: dict[str, list[Any]], b: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """The semilattice join on conflict sets.

    Associative, commutative, idempotent --- which is exactly why the conflict
    set arriving at the root does not depend on the tree's shape.
    """
    out = {k: list(v) for k, v in a.items()}
    for k, v in b.items():
        out[k] = _dedupe(out.get(k, []) + list(v))
    return out


def arbitrate(
    lifted: Any, decide: Callable[[str, list[Any]], Any] | None = None
) -> tuple[Any, dict[str, Any]]:
    """Resolve every lifted conflict once, at the root.

    The default arbiter is deliberately dumb --- it takes the modal candidate, and
    the first on a tie --- because the interesting arbiters are agent operators and
    the harness supplies them.  What matters is that arbitration happens **once**,
    in **one** place, over the **whole** conflict set.
    """
    conflicts = conflicts_of(lifted)
    value = value_of(lifted)
    rulings: dict[str, Any] = {}
    for key, candidates in sorted(conflicts.items()):
        if decide is not None:
            rulings[key] = decide(key, candidates)
        else:
            counts = Counter(
                json.dumps(c, sort_keys=True, ensure_ascii=False, default=str) for c in candidates
            )
            rulings[key] = json.loads(counts.most_common(1)[0][0])
    if rulings and isinstance(value, dict):
        value = {**value, **rulings}
    return value, rulings


# --------------------------------------------------------------------------
# The operator record
# --------------------------------------------------------------------------


@dataclass
class Op:
    """A declared reduction operator.

    The declaration is normative: the runtime refuses schedules the algebra does
    not license, rather than reordering and hoping.  ``commutative`` defaults to
    ``False`` --- the inverse of MPI's convention --- because "merge these two
    draft glossaries" is neither associative nor commutative nor deterministic,
    and the conservative default is the one that does not silently produce a
    different answer on a different rank count.
    """

    name: str
    fn: Callable[[Any, Any], Any] | None = None
    associativity: str = NONE
    commutative: bool = False
    idempotent: bool = False
    identity: Any = None
    #: ``runtime`` operators are applied by the implementation: exact,
    #: deterministic, free.  ``agent`` operators are applied by an executor ---
    #: ``MPI_Op_create`` with a language model as the callback.
    evaluator: Literal["runtime", "agent"] = "runtime"
    deterministic: bool = True
    conflict_policy: str = DECIDE
    #: A budget for the merged *result*.  A result may be summarised because its
    #: consumer is a reader; an *operand* may not, because its consumer is a
    #: function.
    output_tokens: int | None = None
    #: A global invariant over ``(leaves, result)``.  Returns a list of
    #: violations; empty means it holds.
    invariant: Callable[[list[Any], Any], list[str]] | None = None
    doc: str = ""
    #: Number of leaves this accumulator represents, threaded through folds so a
    #: summarising operator can allocate its output budget proportionally rather
    #: than compressing two inputs and sixteen to the same length.
    weighted: bool = False

    # -- what the algebra licenses ----------------------------------------
    def allows_tree(self) -> bool:
        return self.associativity in (EXACT, APPROX)

    def allows_reorder(self) -> bool:
        return self.allows_tree() and self.commutative

    def check_schedule(self, algorithm: str, *, root: int = 0) -> None:
        """Raise if ``algorithm`` is unsound for this operator's declaration."""
        tree_algorithms = {"binomial", "recursive_doubling", "rabenseifner", "ring", "halving"}
        if algorithm in tree_algorithms and not self.allows_tree():
            raise err(
                "AMPI_ERR_OP_UNSOUND",
                f"operator {self.name!r} declares associativity={self.associativity}, "
                f"so the {algorithm!r} schedule is not sound for it",
                hint="Either declare the operator associative, or ask for --algorithm chain.",
                op=self.name,
                algorithm=algorithm,
            )
        if algorithm == "binomial" and not self.commutative and root != 0:
            # A binomial tree visits leaves in rank order only when rooted at 0.
            # Silently reordering a non-commutative operator is the failure MPI
            # warns about and that nobody notices until the answer is wrong.
            raise err(
                "AMPI_ERR_OP_UNSOUND",
                f"operator {self.name!r} is non-commutative, and a binomial tree rooted at "
                f"{root} does not visit leaves in rank order",
                hint="Use root 0, declare the operator commutative, or use --algorithm chain.",
                op=self.name,
                root=root,
            )
        if algorithm == "recursive_doubling" and not self.deterministic:
            # Every rank computes its own fold, so p ranks obtain p different
            # answers and the population silently disagrees about the value it
            # just agreed on.
            raise err(
                "AMPI_ERR_OP_UNSOUND",
                f"operator {self.name!r} is non-deterministic, and recursive doubling has each "
                "rank fold independently, so ranks would disagree about the agreed value",
                hint="Use --algorithm reduce_bcast, which computes once and broadcasts.",
                op=self.name,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "associativity": self.associativity,
            "commutative": self.commutative,
            "idempotent": self.idempotent,
            "evaluator": self.evaluator,
            "deterministic": self.deterministic,
            "conflict_policy": self.conflict_policy,
            "output_tokens": self.output_tokens,
            "allows_tree": self.allows_tree(),
            "allows_reorder": self.allows_reorder(),
            "has_invariant": self.invariant is not None,
            "doc": self.doc,
        }


OPS: dict[str, Op] = {}


def register_op(op: Op) -> Op:
    OPS[op.name] = op
    return op


def get_op(name: str) -> Op:
    if name.startswith("agent:"):
        label = name.split(":", 1)[1]
        return Op(
            name=name,
            fn=None,
            associativity=APPROX,
            commutative=False,
            evaluator="agent",
            deterministic=False,
            doc=f"agent-evaluated operator {label!r}",
        )
    if name not in OPS:
        raise err(
            "AMPI_ERR_OP",
            f"no such reduction operator {name!r}",
            hint="Run 'ampi op list'. Agent operators are written 'agent:<label>'.",
            known=sorted(OPS),
        )
    return OPS[name]


# --------------------------------------------------------------------------
# Runtime operators
# --------------------------------------------------------------------------


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


register_op(
    Op(
        "concat",
        lambda a, b: (a if isinstance(a, str) else json.dumps(a)) + (b if isinstance(b, str) else json.dumps(b))
        if isinstance(a, str) or isinstance(b, str)
        else _as_list(a) + _as_list(b),
        associativity=EXACT,
        commutative=False,
        identity="",
        doc="Concatenate in rank order. Exact and associative, but not commutative.",
    )
)

register_op(
    Op(
        "bag",
        lambda a, b: _as_list(a) + _as_list(b),
        associativity=EXACT,
        commutative=True,
        identity=[],
        doc="Collect every contribution into one list, order unspecified.",
    )
)


def _union(a: Any, b: Any) -> Any:
    """Key-wise union with conflict lifting.

    The exact operator harnesses should reach for first.  A glossary merged with
    ``union`` is identical at every rank regardless of tree shape; a glossary
    merged by an agent is not.

    The implementation is deliberately written as a *set-valued* join and then
    presented as a value/conflict pair, because that is what makes the shape
    independence provable rather than hoped for.  Underneath, the state is a map
    ``key -> set of distinct values seen``.  That map's join is pointwise set
    union, hence associative, commutative and idempotent, hence independent of
    fold order.  A key whose set is a singleton is presented in the value
    component; a key whose set is larger is presented in the conflict component.
    Both presentations are functions of the same shape-independent state, so both
    are shape-independent.

    Writing it the obvious way instead --- lift a key on first disagreement and
    drop it from the value --- is *not* idempotent: a third contributor finds the
    key absent from the accumulator and reinstates it, so the conflict set depends
    on fold order after all.  The exhaustive shape test in ``tests/test_ops.py``
    caught exactly that.

    Two ranks *disagreeing* about a key is representable.  One key having two
    legitimate *senses* is not, and no amount of conflict machinery fixes it: in a
    translation run a glossary bound "pounds" to the currency, correct nearly
    everywhere, and a rank correctly obeying a binding glossary produced a wrong
    sentence in the one section that meant body weight.  That lesson is about key
    design, not about the operator: choose the key so that a globally correct
    answer exists.  Agreement is not correctness, and a protocol that makes
    agreement cheap makes it cheap to agree on something false.
    """
    if isinstance(a, list) or isinstance(b, list):
        return _dedupe(_as_list(a) + _as_list(b))
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise err("AMPI_ERR_ARG", "union needs object or array operands")

    # Recover the set-valued state from each operand's presentation.
    seen: dict[str, list[Any]] = {}
    for operand in (a, b):
        for k, v in value_of(operand).items():
            seen[k] = _dedupe(seen.get(k, []) + [v])
        for k, candidates in conflicts_of(operand).items():
            seen[k] = _dedupe(seen.get(k, []) + list(candidates))

    value = {k: vs[0] for k, vs in seen.items() if len(vs) == 1}
    conflicts = {k: vs for k, vs in seen.items() if len(vs) > 1}
    return lift_conflicts(value, conflicts)


register_op(
    Op(
        "union",
        _union,
        associativity=EXACT,
        commutative=True,
        idempotent=True,
        identity={},
        conflict_policy=LIFT,
        doc="Key-wise union; disagreements are retained as lifted conflicts, never decided locally.",
    )
)


def _jsonmerge(a: Any, b: Any) -> Any:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return b
    out = dict(a)
    for k, v in b.items():
        out[k] = _jsonmerge(out[k], v) if k in out and isinstance(out[k], dict) else v
    return out


register_op(
    Op(
        "jsonmerge",
        _jsonmerge,
        associativity=EXACT,
        commutative=False,
        identity={},
        doc="Deep merge; the later rank wins a collision. Associative but not commutative.",
    )
)

for _name, _fn, _ident, _doc in [
    ("sum", lambda a, b: a + b, 0, "Numeric sum."),
    ("max", max, -math.inf, "Numeric maximum."),
    ("min", min, math.inf, "Numeric minimum."),
    ("and", lambda a, b: bool(a) and bool(b), True, "Logical conjunction."),
    ("or", lambda a, b: bool(a) or bool(b), False, "Logical disjunction."),
]:
    register_op(
        Op(_name, _fn, associativity=EXACT, commutative=True, identity=_ident, doc=_doc)
    )

register_op(
    Op(
        "count",
        lambda a, b: (a if isinstance(a, int) else 1) + (b if isinstance(b, int) else 1),
        associativity=EXACT,
        commutative=True,
        identity=0,
        doc="Count contributions.",
    )
)

register_op(
    Op(
        "first",
        lambda a, b: a,
        associativity=EXACT,
        commutative=False,
        doc="Take the lowest-ranked contribution. Associative, not commutative.",
    )
)

register_op(
    Op(
        "last",
        lambda a, b: b,
        associativity=EXACT,
        commutative=False,
        doc="Take the highest-ranked contribution.",
    )
)


def _maxby(a: Any, b: Any) -> Any:
    def score(x: Any) -> float:
        return float(x.get("score", -math.inf)) if isinstance(x, dict) else -math.inf

    return a if score(a) >= score(b) else b


register_op(
    Op(
        "maxby",
        _maxby,
        associativity=EXACT,
        commutative=True,
        doc="Select the operand with the greatest 'score' field; ties go to the lower rank.",
    )
)


def _vote(a: Any, b: Any) -> Any:
    """Accumulate a tally.  ``finalise_vote`` turns it into a decision.

    Replication does not give independent failures --- correlated model errors are
    the norm --- so a vote reports the consensus *fraction* rather than a bare
    winner.  Agreement is evidence, not proof.
    """
    tally: Counter = Counter()
    for operand in (a, b):
        if isinstance(operand, dict) and operand.get("_tally"):
            tally.update(operand["_tally"])
        else:
            tally.update([json.dumps(operand, sort_keys=True, ensure_ascii=False, default=str)])
    return {"_tally": dict(tally)}


def finalise_vote(acc: Any) -> Any:
    if not (isinstance(acc, dict) and "_tally" in acc):
        return acc
    tally = Counter(acc["_tally"])
    total = sum(tally.values()) or 1
    winner, n = tally.most_common(1)[0]
    return {
        "winner": json.loads(winner),
        "consensus": n / total,
        "distinct": len(tally),
        "votes": total,
    }


register_op(
    Op(
        "vote",
        _vote,
        associativity=EXACT,
        commutative=True,
        identity={"_tally": {}},
        doc="Tally identical contributions; the result carries the consensus fraction.",
    )
)


def _topk(k: int) -> Callable[[Any, Any], Any]:
    def go(a: Any, b: Any) -> Any:
        items = _as_list(a) + _as_list(b)
        items.sort(key=lambda x: -(x.get("score", 0) if isinstance(x, dict) else 0))
        return items[:k]

    return go


register_op(
    Op(
        "topk",
        _topk(5),
        associativity=EXACT,
        commutative=True,
        identity=[],
        doc="Keep the five highest-scoring contributions.",
    )
)


# --------------------------------------------------------------------------
# Folds
# --------------------------------------------------------------------------


def serial_fold(op: Op, values: list[Any]) -> Any:
    """The reference semantics.  Every EXACT algorithm must reproduce this.

    Stating a reference explicitly is what makes "exact" testable rather than
    aspirational; ``conformance/test_protocol.py`` checks every schedule against
    it for every EXACT operator.
    """
    if op.fn is None:
        raise err("AMPI_ERR_OP", f"operator {op.name!r} has no runtime evaluator")
    if not values:
        return op.identity
    acc = values[0]
    for v in values[1:]:
        acc = op.fn(acc, v)
    return acc


def fold(op: Op, values: list[Any], *, algorithm: str = "chain", root: int = 0) -> dict[str, Any]:
    """Apply ``op`` over ``values`` on the named schedule, reporting the fold depth.

    Fold *depth* is reported because it is the quantity that governs quality for a
    lossy operator: depth counts how many times a contribution is re-summarised on
    its way to the root, and loss compounds with depth rather than with the number
    of applications.  A chain and a binomial tree both perform ``p-1``
    applications; their depths are ``p-1`` and ``ceil(log2 p)``.
    """
    op.check_schedule(algorithm, root=root)
    if not values:
        return {"value": op.identity, "depth": 0, "applications": 0, "algorithm": algorithm}
    if op.fn is None:
        raise err("AMPI_ERR_OP", f"operator {op.name!r} must be evaluated by an executor")

    if algorithm == "chain" or len(values) == 1:
        return {
            "value": serial_fold(op, values),
            "depth": max(0, len(values) - 1),
            "applications": max(0, len(values) - 1),
            "algorithm": "chain",
        }

    if algorithm in ("binomial", "tree"):
        level = list(values)
        depth = 0
        applications = 0
        while len(level) > 1:
            nxt = []
            for i in range(0, len(level) - 1, 2):
                nxt.append(op.fn(level[i], level[i + 1]))
                applications += 1
            if len(level) % 2:
                nxt.append(level[-1])
            level = nxt
            depth += 1
        return {
            "value": level[0],
            "depth": depth,
            "applications": applications,
            "algorithm": "binomial",
        }

    if algorithm == "recursive_doubling":
        # Every rank folds independently.  Total applications are p*ceil(log2 p),
        # not p-1: MPI accepts that because redundant arithmetic is free, and it
        # is catastrophic when each application costs an executor a minute.
        p = len(values)
        rounds = max(1, math.ceil(math.log2(p)))
        state = list(values)
        for d in range(rounds):
            partner_stride = 1 << d
            nxt = []
            for i in range(p):
                j = i ^ partner_stride
                nxt.append(op.fn(state[i], state[j]) if j < p else state[i])
            state = nxt
        return {
            "value": state[0],
            "depth": rounds,
            "applications": p * rounds,
            "algorithm": "recursive_doubling",
            "per_rank": state,
        }

    raise err("AMPI_ERR_ARG", f"unknown reduction schedule {algorithm!r}")


def check_invariant(op: Op, leaves: list[Any], result: Any) -> list[str]:
    """Check a declared global invariant after the reduction closes.

    Local merges cannot enforce a global property, so it is verified rather than
    maintained.  "Never drop a module" is not a conflict any pair of operands
    exhibits --- every local merge is individually faithful --- which is precisely
    why it needs a check that sees the leaf multiset and the result together.
    """
    if op.invariant is None:
        return []
    return op.invariant(leaves, result)


def keys_conserved(key_path: str = "") -> Callable[[list[Any], Any], list[str]]:
    """An invariant factory: every key present in any leaf must be in the result.

    This is the invariant whose violation cost us four of eight modules.
    """

    def go(leaves: list[Any], result: Any) -> list[str]:
        def keys(v: Any) -> set[str]:
            node = v
            if key_path:
                for part in key_path.split("."):
                    node = node.get(part, {}) if isinstance(node, dict) else {}
            return set(node) if isinstance(node, dict) else set()

        want: set[str] = set()
        for leaf in leaves:
            want |= keys(leaf)
        have = keys(result) | set(conflicts_of(result))
        missing = sorted(want - have)
        return (
            [f"{len(missing)} key(s) present in the contributions are absent from the "
             f"result: {missing[:20]}"]
            if missing
            else []
        )

    return go


def items_conserved(field: str) -> Callable[[list[Any], Any], list[str]]:
    """Every element of ``field`` in any leaf must survive to the result."""

    def go(leaves: list[Any], result: Any) -> list[str]:
        def items(v: Any) -> set[str]:
            node = v.get(field) if isinstance(v, dict) else None
            if isinstance(node, list):
                return {json.dumps(x, sort_keys=True, default=str) for x in node}
            return set()

        want: set[str] = set()
        for leaf in leaves:
            want |= items(leaf)
        missing = sorted(want - items(result))
        return [f"{len(missing)} item(s) of {field!r} were dropped: {missing[:10]}"] if missing else []

    return go
