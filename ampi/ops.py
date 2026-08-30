"""Reduction operators.

``MPI_Op`` is a binary operator on buffers, plus two declared properties:
associativity (always assumed) and commutativity (declared by the user). Those
two bits are what license an implementation to pick any reduction tree it
likes -- and the reason floating-point ``MPI_SUM`` results can differ between
runs and between process counts.

AgentMPI inherits the structure and inherits the problem in a much sharper
form. A "merge two draft glossaries" operator is neither associative nor
commutative nor deterministic. So AgentMPI splits operators into two families:

* **Runtime ops** -- executed by the runtime, in-process, for free. They are
  exact, deterministic and cheap: concatenation, set union, JSON deep merge,
  numeric reductions, majority vote. These behave like MPI's predefined ops.
* **Agent ops** (``agent:<name>``) -- executed by an *agent* at a node of the
  reduction tree. This is ``MPI_Op_create`` with an LLM as the callback
  function. They are expensive, nondeterministic and generally non-associative.

For agent ops the protocol makes reproducibility an explicit, declared property
rather than a hope: an operator declares ``commute=false`` by default, and the
runtime then pins the reduction tree shape to a canonical order so that the
same inputs on the same rank count give the same tree. A harness that knows its
operator is order-insensitive can opt in to ``commute=true`` and get the faster
schedule -- exactly the trade MPI offers, with much larger stakes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .errors import ArgError, OpError


@dataclass(frozen=True)
class Op:
    name: str
    #: ``None`` for agent-evaluated ops.
    fn: Optional[Callable[[str, str], str]]
    commute: bool
    associative: bool
    deterministic: bool
    doc: str

    @property
    def is_agent(self) -> bool:
        return self.fn is None


# --------------------------------------------------------------------------
# Runtime operator implementations
# --------------------------------------------------------------------------


def _as_json(text: str) -> Any:
    return json.loads(text)


def _num(text: str) -> float:
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        try:
            data = json.loads(text)
        except Exception as exc:
            raise OpError(
                f"numeric reduction needs a number, got {text[:60]!r}", hint="send a bare number"
            ) from exc
        if isinstance(data, (int, float)):
            return float(data)
        if isinstance(data, dict) and "value" in data:
            return float(data["value"])
    raise OpError(f"numeric reduction needs a number, got {text[:60]!r}")


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(v)


def _op_concat(a: str, b: str) -> str:
    return a.rstrip("\n") + "\n" + b.lstrip("\n") if a and b else (a or b)


def _op_concat_json(a: str, b: str) -> str:
    """Concatenate two JSON arrays, preserving order."""
    la, lb = _as_json(a), _as_json(b)
    if not isinstance(la, list) or not isinstance(lb, list):
        raise OpError("concat_json requires JSON arrays on both sides")
    return json.dumps(la + lb, ensure_ascii=False)


def _op_union(a: str, b: str) -> str:
    """Set union over JSON arrays, or over line sets for plain text."""
    try:
        la, lb = _as_json(a), _as_json(b)
    except Exception:
        seen: Dict[str, None] = {}
        for line in (a + "\n" + b).splitlines():
            s = line.strip()
            if s:
                seen.setdefault(s, None)
        return "\n".join(seen)
    if isinstance(la, list) and isinstance(lb, list):
        out: List[Any] = []
        seen_keys: set[str] = set()
        for item in la + lb:
            k = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if k not in seen_keys:
                seen_keys.add(k)
                out.append(item)
        return json.dumps(out, ensure_ascii=False)
    if isinstance(la, dict) and isinstance(lb, dict):
        return _op_jsonmerge(a, b)
    raise OpError("union requires two JSON arrays or two JSON objects")


def _op_jsonmerge(a: str, b: str) -> str:
    """Deep merge of JSON objects. Right-biased on scalar conflict, and
    conflicts are recorded rather than silently dropped -- a harness needs to
    know when two agents disagreed about the same key."""
    la, lb = _as_json(a), _as_json(b)
    conflicts: List[str] = []

    def merge(x: Any, y: Any, path: str = "") -> Any:
        if isinstance(x, dict) and isinstance(y, dict):
            out = dict(x)
            for k, v in y.items():
                out[k] = merge(x[k], v, f"{path}.{k}") if k in x else v
            return out
        if isinstance(x, list) and isinstance(y, list):
            out_l: List[Any] = []
            seen: set[str] = set()
            for item in x + y:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    out_l.append(item)
            return out_l
        if x != y:
            conflicts.append(path.lstrip("."))
        return y

    merged = merge(la, lb)
    if conflicts and isinstance(merged, dict):
        prev = merged.get("_ampi_conflicts") or []
        merged["_ampi_conflicts"] = sorted(set(list(prev) + conflicts))
    return json.dumps(merged, ensure_ascii=False)


def _op_sum(a: str, b: str) -> str:
    return _fmt_num(_num(a) + _num(b))


def _op_max(a: str, b: str) -> str:
    return _fmt_num(max(_num(a), _num(b)))


def _op_min(a: str, b: str) -> str:
    return _fmt_num(min(_num(a), _num(b)))


def _op_land(a: str, b: str) -> str:
    return json.dumps(bool(_truthy(a) and _truthy(b)))


def _op_lor(a: str, b: str) -> str:
    return json.dumps(bool(_truthy(a) or _truthy(b)))


def _truthy(text: str) -> bool:
    s = text.strip().lower()
    if s in ("true", "1", "yes", "ok", "pass"):
        return True
    if s in ("false", "0", "no", "fail"):
        return False
    try:
        return bool(json.loads(text))
    except Exception:
        return bool(s)


def _norm_answer(text: str) -> str:
    """Normalise a short answer for voting: lowercase, collapse whitespace,
    strip terminal punctuation and common wrapper phrasing."""
    s = text.strip().lower()
    s = re.sub(r"^(?:the\s+)?(?:answer|result|output)\s*(?:is|:)\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .!\"'`")


def _op_vote(a: str, b: str) -> str:
    """Majority vote with running tallies -- self-consistency as a collective.

    The intermediate representation is a tally object, which makes the operator
    genuinely associative and commutative (unlike naive pairwise voting), so the
    runtime is free to use any tree.
    """
    ta = _tally(a)
    tb = _tally(b)
    for k, v in tb["tally"].items():
        ta["tally"][k] = ta["tally"].get(k, 0) + v
    for k, v in tb.get("samples", {}).items():
        ta.setdefault("samples", {}).setdefault(k, v)
    best = max(ta["tally"].items(), key=lambda kv: (kv[1], kv[0]))
    total = sum(ta["tally"].values())
    ta["winner"] = best[0]
    ta["winner_votes"] = best[1]
    ta["total_votes"] = total
    ta["agreement"] = round(best[1] / total, 4) if total else 0.0
    ta["answer"] = ta.get("samples", {}).get(best[0], best[0])
    return json.dumps(ta, ensure_ascii=False)


def _tally(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tally" in data:
            return {"tally": dict(data["tally"]), "samples": dict(data.get("samples") or {})}
    except Exception:
        pass
    key = _norm_answer(text)
    return {"tally": {key: 1}, "samples": {key: text.strip()}}


def _op_maxby(a: str, b: str) -> str:
    """Pick the operand with the larger ``score`` field. Argmax as a reduction:
    the basis of best-of-n selection."""
    da, db = _as_json(a), _as_json(b)
    for d, name in ((da, "left"), (db, "right")):
        if not isinstance(d, dict) or "score" not in d:
            raise OpError(
                f"maxby requires JSON objects with a numeric 'score' field ({name} operand lacks it)"
            )
    return a if float(da["score"]) >= float(db["score"]) else b


def _op_count(a: str, b: str) -> str:
    return _fmt_num(_count_val(a) + _count_val(b))


def _count_val(text: str) -> float:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "count" in data:
            return float(data["count"])
        if isinstance(data, list):
            return float(len(data))
        if isinstance(data, (int, float)):
            return float(data)
    except Exception:
        pass
    return 1.0


def _op_first(a: str, b: str) -> str:
    return a


def _op_bandwidth(a: str, b: str) -> str:
    """Keep the larger operand. Used by microbenchmarks to exercise the
    reduction schedule at a controlled payload size without the payload
    growing with P (which would confound latency and bandwidth terms)."""
    return a if len(a) >= len(b) else b


REGISTRY: Dict[str, Op] = {
    op.name: op
    for op in [
        Op("concat", _op_concat, False, True, True, "text concatenation in canonical rank order"),
        Op("concat_json", _op_concat_json, False, True, True, "JSON array concatenation, order preserved"),
        Op("union", _op_union, True, True, True, "set union over JSON arrays or line sets"),
        Op("jsonmerge", _op_jsonmerge, False, True, True, "deep JSON merge, recording key conflicts"),
        Op("sum", _op_sum, True, True, True, "numeric sum"),
        Op("max", _op_max, True, True, True, "numeric maximum"),
        Op("min", _op_min, True, True, True, "numeric minimum"),
        Op("and", _op_land, True, True, True, "logical AND"),
        Op("or", _op_lor, True, True, True, "logical OR"),
        Op("vote", _op_vote, True, True, True, "majority vote over normalised answers (self-consistency)"),
        Op("maxby", _op_maxby, True, True, True, "argmax over a 'score' field (best-of-n selection)"),
        Op("count", _op_count, True, True, True, "count contributions"),
        Op("first", _op_first, False, True, True, "keep the lowest-rank contribution"),
        Op("bandwidth", _op_bandwidth, True, True, True, "benchmark op: keep the larger operand"),
    ]
}


def get_op(name: str, *, commute: Optional[bool] = None) -> Op:
    """Resolve an operator name.

    ``agent:<label>`` denotes an agent-evaluated operator. The label is opaque
    to the runtime but is passed to the reducing agent as the instruction key,
    so a harness can define ``agent:merge_glossary`` and describe it in the
    rank prompt.
    """
    name = (name or "concat").strip()
    if name.startswith("agent:") or name == "agent":
        label = name.split(":", 1)[1] if ":" in name else "merge"
        return Op(
            name=f"agent:{label}",
            fn=None,
            commute=bool(commute) if commute is not None else False,
            associative=False,
            deterministic=False,
            doc=f"agent-evaluated reduction ({label})",
        )
    if name not in REGISTRY:
        raise ArgError(
            f"unknown reduction op {name!r}",
            hint="built-ins: " + ", ".join(sorted(REGISTRY)) + "; or agent:<label>",
        )
    op = REGISTRY[name]
    if commute is not None and commute != op.commute:
        return Op(op.name, op.fn, commute, op.associative, op.deterministic, op.doc)
    return op


def apply_op(op: Op, left: str, right: str) -> str:
    if op.fn is None:
        raise OpError(f"operator {op.name} must be evaluated by an agent")
    try:
        return op.fn(left, right)
    except OpError:
        raise
    except Exception as exc:
        raise OpError(
            f"reduction op {op.name} failed: {exc}",
            hint="check that every contribution has the shape the operator expects",
        ) from exc


def reduce_sequence(op: Op, values: Sequence[str]) -> str:
    """Left fold in the given order. Used for the runtime's local reductions
    and as the reference result when checking tree-shape independence."""
    if not values:
        raise OpError("cannot reduce an empty sequence")
    acc = values[0]
    for v in values[1:]:
        acc = apply_op(op, acc, v)
    return acc


def describe_ops() -> List[Dict[str, Any]]:
    return [
        {
            "name": op.name,
            "kind": "runtime",
            "commutative": op.commute,
            "deterministic": op.deterministic,
            "doc": op.doc,
        }
        for op in sorted(REGISTRY.values(), key=lambda o: o.name)
    ] + [
        {
            "name": "agent:<label>",
            "kind": "agent",
            "commutative": False,
            "deterministic": False,
            "doc": "reduction evaluated by an agent at each tree node (AMPI_Op_create analogue)",
        }
    ]
