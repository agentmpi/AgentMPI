"""Reduction operators.

MPI's reduction operators are things like ``MPI_SUM``.  They are associative
and commutative, their output is the same size as their input, and applying
one costs nanoseconds.  Every one of those three properties fails for agents,
and each failure has a consequence the protocol must handle:

**Associativity and commutativity are not free.**  ``MPI_Op_create`` takes a
``commute`` flag and MPI uses it to pick an algorithm.  AgentMPI needs the
same flag and two more.  A "merge these two drafts" operator is neither
associative nor commutative in any exact sense; a "take a majority vote"
operator is commutative and idempotent; a "concatenate in rank order"
operator is associative but not commutative.  Getting this wrong does not
produce a rounding difference, it produces a different document.

**Output size is not input size.**  ``MPI_SUM`` of two doubles is a double.
"Summarise these two chapter summaries" had better be smaller than their
concatenation, or a reduction tree of depth *d* produces an output of size
*2^d* and the root cannot read its own result.  AgentMPI therefore requires
an operator to declare an output bound, and the runtime refuses to build a
multi-level tree from a non-contracting operator (see
:func:`agentmpi.context.plan_reduction`).

**Application is expensive and fallible.**  A reduction step is an agent
turn: seconds, cents, and a nonzero probability of returning something
malformed.  So operators are retried, memoised by content address, and their
outputs are contract-checked.

The built-in operators below are the ones we found sufficient to express
every collective in our experiments.  User operators are created with
:func:`op_create`, exactly as in MPI.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .datatypes import TypeDescriptor
from .errors import OpError
from .tokens import count_tokens

Reducer = Callable[[Any, Any], Any]
"""A binary reduction ``f(accumulated, incoming) -> accumulated``."""

NaryReducer = Callable[[Sequence[Any]], Any]


@dataclass(frozen=True)
class Op:
    """A reduction operator handle (``AMPI_Op``)."""

    name: str
    fn: Reducer
    commute: bool = False
    idempotent: bool = False
    associative: bool = True
    #: Bound on the operator's output size, in tokens.  ``None`` = unbounded,
    #: which restricts the operator to flat (single-level) reductions.
    output_tokens: int | None = None
    #: Optional n-ary form.  Semantic operators are usually *better* applied
    #: to all inputs at once than pairwise -- an agent asked to merge eight
    #: drafts at once does a better job than one asked to merge two at a time
    #: three times -- so the runtime prefers this form when the inputs fit.
    nary: NaryReducer | None = None
    identity: Any = None
    doc: str = ""

    def __call__(self, a: Any, b: Any) -> Any:
        return self.fn(a, b)

    def apply(self, values: Sequence[Any]) -> Any:
        if not values:
            return self.identity
        if len(values) == 1:
            return values[0]
        if self.nary is not None:
            return self.nary(list(values))
        acc = values[0]
        for v in values[1:]:
            acc = self.fn(acc, v)
        return acc

    @property
    def contracting(self) -> bool:
        return self.output_tokens is not None


def op_create(
    fn: Reducer,
    *,
    name: str = "user_op",
    commute: bool = False,
    idempotent: bool = False,
    associative: bool = True,
    output_tokens: int | None = None,
    nary: NaryReducer | None = None,
    identity: Any = None,
    doc: str = "",
) -> Op:
    """``AMPI_Op_create``."""
    return Op(
        name=name, fn=fn, commute=commute, idempotent=idempotent,
        associative=associative, output_tokens=output_tokens, nary=nary,
        identity=identity, doc=doc,
    )


# --------------------------------------------------------------------------
# Structural operators: exact, cheap, no model call
# --------------------------------------------------------------------------

def _as_list(x: Any) -> list[Any]:
    if x is None:
        return []
    return list(x) if isinstance(x, list) else [x]


CONCAT = Op(
    name="AMPI_CONCAT",
    fn=lambda a, b: _as_list(a) + _as_list(b),
    commute=False,
    associative=True,
    identity=[],
    doc="Ordered concatenation.  Associative, not commutative: the runtime "
        "must preserve rank order, which rules out algorithms that reassociate "
        "arbitrarily.",
)

MERGE_JSON = Op(
    name="AMPI_MERGE_JSON",
    fn=lambda a, b: {**(a or {}), **(b or {})},
    commute=False,
    associative=True,
    identity={},
    doc="Shallow dictionary merge; later keys win, so it is order sensitive.",
)


def _union_merge(a: Any, b: Any) -> Any:
    """Set union over dictionaries of lists -- the glossary operator."""
    out: dict[str, Any] = {}
    for src in (a or {}, b or {}):
        for k, v in src.items():
            if k not in out:
                out[k] = v
            elif isinstance(out[k], list) and isinstance(v, list):
                merged = list(out[k])
                for item in v:
                    if item not in merged:
                        merged.append(item)
                out[k] = merged
    return out


UNION = Op(
    name="AMPI_UNION",
    fn=_union_merge,
    commute=True,
    idempotent=True,
    associative=True,
    identity={},
    doc="Key-wise union; commutative and idempotent, so any tree shape and "
        "any amount of duplicate delivery yields the same result.  This is "
        "the operator to reach for under at-least-once delivery.",
)


def _majority(values: Sequence[Any]) -> Any:
    keys = [json.dumps(v, sort_keys=True, ensure_ascii=False) if not isinstance(v, str) else v
            for v in values]
    counts = Counter(keys)
    winner, votes = counts.most_common(1)[0]
    for v, k in zip(values, keys):
        if k == winner:
            return {"value": v, "votes": votes, "of": len(values),
                    "agreement": round(votes / len(values), 3)}
    return None


VOTE = Op(
    name="AMPI_VOTE",
    fn=lambda a, b: _majority([a, b]),
    nary=_majority,
    commute=True,
    idempotent=False,
    associative=False,
    doc="Plurality vote over exactly-equal answers.  Commutative but NOT "
        "associative: a pairwise tree over votes computes a different answer "
        "than a flat vote, so the runtime forces a flat reduction for it. "
        "This is the AgentMPI counterpart of the well-known fact that "
        "floating-point MPI_SUM is not associative either -- except that here "
        "the discrepancy is not in the last bit.",
)

MAX_BY_SCORE = Op(
    name="AMPI_MAXLOC",
    fn=lambda a, b: a if (a or {}).get("score", float("-inf")) >= (b or {}).get("score", float("-inf")) else b,
    commute=True,
    idempotent=True,
    associative=True,
    doc="Select the highest-scoring contribution, carrying its rank -- the "
        "direct analogue of MPI_MAXLOC.  Requires each contribution to be a "
        "dict with 'score' and 'rank'.",
)

SUM = Op(name="AMPI_SUM", fn=lambda a, b: (a or 0) + (b or 0), commute=True,
         associative=True, identity=0, output_tokens=8)
MAX = Op(name="AMPI_MAX", fn=lambda a, b: max(a, b), commute=True, idempotent=True,
         associative=True, output_tokens=8)
MIN = Op(name="AMPI_MIN", fn=lambda a, b: min(a, b), commute=True, idempotent=True,
         associative=True, output_tokens=8)
LAND = Op(name="AMPI_LAND", fn=lambda a, b: bool(a) and bool(b), commute=True,
          idempotent=True, associative=True, identity=True, output_tokens=4)
LOR = Op(name="AMPI_LOR", fn=lambda a, b: bool(a) or bool(b), commute=True,
         idempotent=True, associative=True, identity=False, output_tokens=4)


def _patch_apply(a: Any, b: Any) -> Any:
    """Sequential patch composition with conflict detection.

    Deliberately conservative: if two patches touch the same hunk header the
    merge is reported as conflicted rather than silently resolved.  A silent
    merge is the worst outcome in a code-writing harness, because it produces
    a plausible file that nobody wrote.
    """
    left = a if isinstance(a, dict) else {"patches": _as_list(a), "conflicts": []}
    right = b if isinstance(b, dict) else {"patches": _as_list(b), "conflicts": []}
    patches = list(left.get("patches", [])) + list(right.get("patches", []))
    conflicts = list(left.get("conflicts", [])) + list(right.get("conflicts", []))
    seen_targets: dict[str, int] = {}
    for i, p in enumerate(patches):
        text = p if isinstance(p, str) else json.dumps(p, sort_keys=True)
        for m in re.finditer(r"^\+\+\+ (?:b/)?(\S+)", text, re.M):
            target = m.group(1)
            if target in seen_targets:
                conflicts.append({"file": target, "patches": [seen_targets[target], i]})
            else:
                seen_targets[target] = i
    return {"patches": patches, "conflicts": conflicts, "files": sorted(seen_targets)}


PATCH_MERGE = Op(
    name="AMPI_PATCH_MERGE",
    fn=_patch_apply,
    commute=False,
    associative=True,
    identity={"patches": [], "conflicts": []},
    doc="Compose unified diffs, reporting rather than resolving conflicts.",
)


# --------------------------------------------------------------------------
# Semantic operators: require an agent to evaluate them
# --------------------------------------------------------------------------

SemanticFn = Callable[[Sequence[Any], dict[str, Any]], Any]
"""``(inputs, context) -> output``.  Supplied by the harness; the runtime
never calls a model itself."""


@dataclass
class SemanticOpRegistry:
    """Binds semantic operator names to harness-supplied implementations.

    Keeping the binding in a registry rather than in the operator object is
    what lets the *same* harness program run against a live model, a recorded
    trace, or a deterministic stub -- the mechanism that makes AgentMPI
    experiments reproducible despite non-deterministic executors.
    """

    impls: dict[str, SemanticFn] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    stats: Counter = field(default_factory=Counter)

    def bind(self, name: str, fn: SemanticFn) -> None:
        self.impls[name] = fn

    def call(self, name: str, inputs: Sequence[Any], ctx: dict[str, Any] | None = None) -> Any:
        ctx = ctx or {}
        if name not in self.impls:
            raise OpError(
                f"semantic operator {name!r} has no bound implementation; "
                f"call registry.bind({name!r}, fn) before using it"
            )
        key = self._key(name, inputs, ctx)
        if key in self.cache:
            self.stats["hits"] += 1
            return self.cache[key]
        self.stats["misses"] += 1
        result = self.impls[name](list(inputs), ctx)
        self.cache[key] = result
        return result

    @staticmethod
    def _key(name: str, inputs: Sequence[Any], ctx: dict[str, Any]) -> str:
        """Content-addressed memoisation key.

        Because agent outputs are non-deterministic, byte-exact replay is
        impossible.  Content addressing gives the next best thing: identical
        inputs reuse the previous output, so a re-run after a failure is both
        cheap and *consistent with what the failed run already told other
        ranks*.  That consistency is the property message-log replay normally
        gets from the piecewise-deterministic assumption, which agents
        violate outright.
        """
        blob = json.dumps(
            {"op": name, "inputs": inputs,
             "ctx": {k: v for k, v in sorted(ctx.items()) if k != "_nondeterministic"}},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def semantic_op(
    name: str,
    registry: SemanticOpRegistry,
    *,
    output_tokens: int,
    commute: bool = False,
    idempotent: bool = False,
    associative: bool = True,
    doc: str = "",
) -> Op:
    """Build an :class:`Op` backed by a harness-supplied semantic function."""

    def _pair(a: Any, b: Any) -> Any:
        return registry.call(name, [a, b], {"arity": 2, "bound": output_tokens})

    def _nary(values: Sequence[Any]) -> Any:
        return registry.call(name, list(values), {"arity": len(values), "bound": output_tokens})

    return Op(
        name=name, fn=_pair, nary=_nary, commute=commute, idempotent=idempotent,
        associative=associative, output_tokens=output_tokens, doc=doc,
    )


def summarize_op(
    registry: SemanticOpRegistry, budget: int = 800, name: str = "AMPI_SUMMARIZE"
) -> Op:
    """The canonical contracting operator: reduce many texts to one, bounded.

    This is the operator that makes deep reduction trees possible at all.  A
    non-contracting operator forces a flat reduction, which costs the root
    ``(p-1) * item`` tokens of ingest and therefore caps the number of ranks
    at roughly ``context / item``; a contracting operator with bound *m*
    caps ingest at ``k * m`` per node regardless of *p*, so the reduction
    scales logarithmically in turns and constantly in context.
    """
    return semantic_op(
        name, registry, output_tokens=budget, commute=False, associative=True,
        doc="Bounded semantic summarisation; contracting, so it admits "
            "multi-level reduction trees.",
    )


BUILTIN_OPS: dict[str, Op] = {
    op.name.lower(): op
    for op in (CONCAT, MERGE_JSON, UNION, VOTE, MAX_BY_SCORE, SUM, MAX, MIN,
               LAND, LOR, PATCH_MERGE)
}
BUILTIN_OPS.update({k.removeprefix("ampi_"): v for k, v in list(BUILTIN_OPS.items())})


def lookup_op(name: str | Op) -> Op:
    if isinstance(name, Op):
        return name
    key = name.lower()
    if key in BUILTIN_OPS:
        return BUILTIN_OPS[key]
    raise OpError(f"unknown reduction operator {name!r}",
                  known=sorted({o.name for o in BUILTIN_OPS.values()}))


def check_op_for_tree(op: Op, depth: int) -> None:
    """Reject operator/algorithm combinations that are semantically unsound.

    MPI silently reassociates reductions and accepts the resulting
    floating-point discrepancy.  AgentMPI refuses, because the discrepancy is
    not numerical: reassociating a vote or a "pick the best" operator changes
    which answer wins, and a protocol that silently changes the answer
    depending on the number of ranks is not one anybody can build on.
    """
    if depth > 1 and not op.associative:
        raise OpError(
            f"operator {op.name} is not associative and cannot be evaluated in a "
            f"tree of depth {depth}; use a flat reduction (algorithm='flat')",
            op=op.name,
        )
    if depth > 1 and not op.contracting:
        raise OpError(
            f"operator {op.name} declares no output bound, so a depth-{depth} "
            f"tree has unbounded peak ingest; declare output_tokens or use a "
            f"flat reduction",
            op=op.name,
        )


def estimate_output_tokens(op: Op, inputs: Sequence[Any]) -> int:
    if op.output_tokens is not None:
        return op.output_tokens
    return sum(count_tokens(json.dumps(v, default=str, ensure_ascii=False)) for v in inputs)
