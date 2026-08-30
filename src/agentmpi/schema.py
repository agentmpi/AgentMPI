"""Contracts and views: AgentMPI's answer to MPI datatypes.

MPI datatypes do two jobs that are usually conflated.

*Job one is matching.*  Every send and receive carries a type signature, and
the implementation checks that they agree.  This turns a class of silent
corruption bugs into loud runtime errors.  The agent analogue is obvious once
stated: an agent rank routinely returns something of the wrong *shape* — prose
where a JSON object was expected, six sections where eight were requested, a
patch that does not apply.  AgentMPI attaches a :class:`Contract` to every
message and every agent invocation and checks it at the boundary, so a
malformed artifact is rejected at the sender rather than silently poisoning a
downstream reduction.  A contract has a structural part (checked mechanically)
and a semantic part (natural language, carried to the receiving agent as part
of the message, and optionally checked by a validator).

*Job two is describing non-contiguous data so that it can be communicated
without being packed.*  ``MPI_Type_vector`` exists so that a program can send
the boundary column of a matrix without materialising a copy of it.  This is
the job that matters most in the agent setting, and it is the job every
existing agent framework omits.  A :class:`View` names a projection of a stored
artifact — a slice of chapters, the signatures but not the bodies of a module,
the diff rather than the file — so that a rank can receive *what it needs* out
of a 400k-token artifact without admitting the artifact into its context.
Views are the mechanism by which AgentMPI programs stay within their context
budgets; the transport mode (eager vs rendezvous) is the policy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import tokens as _tokens
from .errors import AmpiTypeError, AmpiValidationError

# --------------------------------------------------------------------- contracts

JsonType = str


@dataclass(frozen=True)
class Contract:
    """A structural + semantic type for an artifact.

    Parameters
    ----------
    name:
        Stable identifier.  Two contracts *match* when their names and
        structural signatures agree; see :meth:`matches`.
    kind:
        ``"text"``, ``"json"``, ``"patch"`` or ``"none"``.
    required:
        For ``kind="json"``, top-level keys that must be present.  This is a
        deliberately weak structural language: experience with agent output is
        that presence-and-type checks catch nearly all mechanical breakage,
        while a full JSON Schema tempts harness authors into over-constraining
        an artifact whose interesting properties are semantic anyway.
    min_tokens / max_tokens:
        Volume bounds.  ``max_tokens`` is what lets a receiver reject an
        artifact that would blow its budget *before* reading it — the check
        that distinguishes an ``ERR_TRUNCATE`` from a context overflow.
    must_match / must_not_match:
        Regular expressions applied to the rendered artifact.
    semantics:
        Natural-language postcondition.  Delivered to the receiving agent so
        that the contract is legible to the consumer, and to a validator agent
        when one is configured.
    """

    name: str
    kind: str = "json"
    required: tuple[str, ...] = ()
    min_tokens: int = 0
    max_tokens: int | None = None
    must_match: tuple[str, ...] = ()
    must_not_match: tuple[str, ...] = ()
    semantics: str = ""

    def signature(self) -> str:
        """The part of the contract used for type matching.

        Deliberately excludes ``semantics`` and the token bounds: a receiver
        that expects tighter bounds than the sender promised should get a
        truncation error at receive time, not a type mismatch at match time.
        This mirrors MPI, where the *type signature* (the sequence of basic
        types) governs matching while the count may differ.
        """
        return json.dumps(
            {"name": self.name, "kind": self.kind, "required": sorted(self.required)},
            sort_keys=True,
            separators=(",", ":"),
        )

    def matches(self, other: Contract | None) -> bool:
        """Type-matching rule.

        ``None`` is the untyped contract and matches anything, which keeps the
        common case of ad-hoc control messages ergonomic.  Otherwise names must
        agree and the receiver's required keys must be a subset of the
        sender's, so a receiver may legally under-specify.
        """
        if other is None:
            return True
        if self.name != other.name:
            return False
        if self.kind != other.kind:
            return False
        return set(other.required) <= set(self.required)

    def check(self, payload: Any, *, where: str = "payload") -> list[str]:
        """Return a list of structural violations; empty means conformant."""
        problems: list[str] = []
        if self.kind == "none":
            return problems
        if self.kind == "json":
            if not isinstance(payload, dict):
                return [f"{where}: expected a JSON object, got {type(payload).__name__}"]
            for key in self.required:
                if key not in payload:
                    problems.append(f"{where}: missing required key {key!r}")
                elif payload[key] in (None, "", [], {}):
                    problems.append(f"{where}: required key {key!r} is empty")
        elif self.kind in ("text", "patch") and not isinstance(payload, str):
            problems.append(f"{where}: expected text, got {type(payload).__name__}")
        if self.kind == "patch" and isinstance(payload, str) and payload.strip():
            if not re.search(r"^(diff --git |--- |\+\+\+ |@@ )", payload, re.M):
                problems.append(f"{where}: does not look like a unified diff")

        rendered = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
        n = _tokens.count(rendered)
        if n < self.min_tokens:
            problems.append(f"{where}: {n} tokens is below min_tokens={self.min_tokens}")
        if self.max_tokens is not None and n > self.max_tokens:
            problems.append(f"{where}: {n} tokens exceeds max_tokens={self.max_tokens}")
        for pat in self.must_match:
            if not re.search(pat, rendered, re.S):
                problems.append(f"{where}: does not match required pattern /{pat}/")
        for pat in self.must_not_match:
            if re.search(pat, rendered, re.S):
                problems.append(f"{where}: matches forbidden pattern /{pat}/")
        return problems

    def validate(self, payload: Any, *, where: str = "payload") -> None:
        problems = self.check(payload, where=where)
        if problems:
            raise AmpiValidationError("contract violation", contract=self.name, problems=problems)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "required": list(self.required),
            "min_tokens": self.min_tokens,
            "max_tokens": self.max_tokens,
            "must_match": list(self.must_match),
            "must_not_match": list(self.must_not_match),
            "semantics": self.semantics,
        }

    @staticmethod
    def from_json(data: dict[str, Any] | str | None) -> Contract | None:
        if data is None:
            return None
        if isinstance(data, str):
            data = json.loads(data)
        return Contract(
            name=data["name"],
            kind=data.get("kind", "json"),
            required=tuple(data.get("required", ())),
            min_tokens=int(data.get("min_tokens", 0)),
            max_tokens=data.get("max_tokens"),
            must_match=tuple(data.get("must_match", ())),
            must_not_match=tuple(data.get("must_not_match", ())),
            semantics=data.get("semantics", ""),
        )


#: The untyped contract: matches everything, checks nothing.  Named so harness
#: code can be explicit about opting out.
ANY_CONTRACT: Contract | None = None


def check_match(send_contract: Contract | None, recv_contract: Contract | None, *, ctx: int, src: int, dst: int, tag: str) -> None:
    """Enforce the type-matching rule at message match time."""
    if recv_contract is None or send_contract is None:
        return
    if not send_contract.matches(recv_contract):
        raise AmpiTypeError(
            "contract mismatch between send and receive",
            sent=send_contract.name,
            expected=recv_contract.name,
            ctx=ctx,
            src=src,
            dst=dst,
            tag=tag,
        )


# ------------------------------------------------------------------------- views


@dataclass(frozen=True)
class View:
    """A projection of a stored artifact, the analogue of a derived datatype.

    A view is *declarative*: it names the projection rather than performing it
    eagerly, so a rendezvous message can carry a 12-byte handle plus a view
    specification and the receiver materialises only the part it needs.

    Supported projections mirror the MPI datatype constructors that actually
    get used:

    ``contiguous(n)``
        The first ``n`` elements — a prefix.
    ``vector(offset, count, stride)``
        Strided selection, ``MPI_Type_vector``.  Used to give rank *r* every
        *p*-th chapter of a book (a block-cyclic decomposition).
    ``indexed(indices)``
        An explicit element list, ``MPI_Type_indexed``.
    ``keys(names)``
        For a JSON artifact, a struct projection, ``MPI_Type_create_struct``.
    ``jsonpath(path)``
        A single nested selection.
    ``head(tokens)`` / ``tail(tokens)``
        Token-bounded truncation.  Not an MPI analogue; it is the escape hatch
        that makes budget compliance always achievable, and the runtime records
        when it fires because a silent truncation is a correctness hazard.
    ``digest_only``
        Deliver the handle and metadata, nothing else.  This is what a
        ``probe`` returns and what a rendezvous envelope carries.
    """

    op: str
    args: tuple[Any, ...] = ()
    #: Optional element separator for text artifacts; defaults to blank-line
    #: paragraphs, which is the natural "element" of a prose artifact.
    sep: str = "\n\n"

    def describe(self) -> str:
        if not self.args:
            return self.op
        return f"{self.op}({', '.join(map(repr, self.args))})"

    # ---- constructors ----

    @staticmethod
    def contiguous(n: int) -> View:
        return View("contiguous", (int(n),))

    @staticmethod
    def vector(offset: int, count: int, stride: int) -> View:
        return View("vector", (int(offset), int(count), int(stride)))

    @staticmethod
    def indexed(indices: Sequence[int]) -> View:
        return View("indexed", (tuple(int(i) for i in indices),))

    @staticmethod
    def keys(names: Sequence[str]) -> View:
        return View("keys", (tuple(str(k) for k in names),))

    @staticmethod
    def jsonpath(path: str) -> View:
        return View("jsonpath", (path,))

    @staticmethod
    def head(max_tokens: int) -> View:
        return View("head", (int(max_tokens),))

    @staticmethod
    def tail(max_tokens: int) -> View:
        return View("tail", (int(max_tokens),))

    @staticmethod
    def digest_only() -> View:
        return View("digest_only")

    # ---- application ----

    def apply(self, payload: Any) -> Any:
        """Materialise the projection."""
        op = self.op
        if op == "digest_only":
            return None
        if op == "keys":
            (names,) = self.args
            if not isinstance(payload, dict):
                raise AmpiTypeError("keys view requires a JSON object", got=type(payload).__name__)
            return {k: payload[k] for k in names if k in payload}
        if op == "jsonpath":
            (path,) = self.args
            cur = payload
            for part in [p for p in path.replace("]", "").replace("[", ".").split(".") if p]:
                if isinstance(cur, dict):
                    cur = cur.get(part)
                elif isinstance(cur, list) and part.lstrip("-").isdigit():
                    idx = int(part)
                    cur = cur[idx] if -len(cur) <= idx < len(cur) else None
                else:
                    cur = None
            return cur
        if op in ("head", "tail"):
            (budget,) = self.args
            return _truncate_tokens(payload, budget, tail=(op == "tail"))

        elements, rebuild = _elements(payload, self.sep)
        if op == "contiguous":
            (n,) = self.args
            return rebuild(elements[: max(0, n)])
        if op == "vector":
            offset, count, stride = self.args
            picked = [elements[i] for i in range(offset, len(elements), max(1, stride))][:count]
            return rebuild(picked)
        if op == "indexed":
            (indices,) = self.args
            picked = [elements[i] for i in indices if 0 <= i < len(elements)]
            return rebuild(picked)
        raise AmpiTypeError("unknown view op", op=op)

    def to_json(self) -> dict[str, Any]:
        return {"op": self.op, "args": list(self.args), "sep": self.sep}

    @staticmethod
    def from_json(data: dict[str, Any] | str | None) -> View | None:
        if data is None:
            return None
        if isinstance(data, str):
            data = json.loads(data)
        args = tuple(tuple(a) if isinstance(a, list) else a for a in data.get("args", ()))
        return View(op=data["op"], args=args, sep=data.get("sep", "\n\n"))


def _elements(payload: Any, sep: str) -> tuple[list[Any], Callable[[list[Any]], Any]]:
    """Decompose a payload into addressable elements and a way to reassemble."""
    if isinstance(payload, list):
        return list(payload), lambda xs: xs
    if isinstance(payload, str):
        parts = payload.split(sep)
        return parts, lambda xs: sep.join(xs)
    if isinstance(payload, dict):
        items = sorted(payload.items())
        return [{k: v} for k, v in items], lambda xs: {k: v for d in xs for k, v in d.items()}
    return [payload], lambda xs: xs[0] if xs else None


def _truncate_tokens(payload: Any, budget: int, *, tail: bool) -> Any:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    if _tokens.count(text) <= budget:
        return payload
    # Token counts are monotone in characters for the estimators we support, so
    # a bisection on character length converges to the largest conformant cut.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        chunk = text[-mid:] if tail else text[:mid]
        if _tokens.count(chunk) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[-lo:] if tail else text[:lo]


# ------------------------------------------------------------------- validators


@dataclass
class Validator:
    """A checker applied to an artifact, used for failure classes F3 and F4.

    Structural checks catch *fail-noisy*: cheap, local, deterministic.  Only an
    independent computation catches *fail-plausible*: a compiler, a test suite,
    a cross-checking agent, or agreement among replicated ranks.  The
    :class:`Validator` interface covers both so a harness can declare its
    verification budget in one place.

    ``fn`` returns ``(ok, detail)``.  ``cost_tokens`` lets the cost model
    account for verification, which is not free and in agent systems is often
    the dominant term.
    """

    name: str
    fn: Callable[[Any], tuple[bool, str]]
    cost_tokens: int = 0
    classifies: str = "fail_noisy"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __call__(self, payload: Any) -> tuple[bool, str]:
        try:
            return self.fn(payload)
        except Exception as exc:  # a crashing validator is a failed check
            return False, f"validator raised: {exc!r}"


def contract_validator(contract: Contract) -> Validator:
    def _check(payload: Any) -> tuple[bool, str]:
        problems = contract.check(payload)
        return (not problems), "; ".join(problems)

    return Validator(name=f"contract:{contract.name}", fn=_check, classifies="fail_noisy")
