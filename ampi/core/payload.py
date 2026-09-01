"""Payloads: envelopes, content-addressed handles, views, and contracts.

Three ideas from MPI are transplanted here, each with the unit changed from bytes
to tokens.

**The envelope/body split.**  MPI separates a message's envelope (source, tag,
communicator, count) from its body because the envelope is what matching needs and
the body is what costs bandwidth.  Here the split is sharper: the envelope is a
handful of tokens and the body may be forty thousand, so an operation that
delivers a payload reports the envelope and *may* withhold the body.  Everything
the eager/rendezvous machinery does rests on this.

**Derived datatypes become views.**  ``MPI_Type_vector`` is a declarative
description of how to access a buffer --- "ten blocks of four doubles, stride one
hundred" --- and its value is that the access pattern is *data the library can
optimise* rather than control flow it cannot see.  The scarce resource here is
context rather than memory bandwidth, so the analogous question is not "which
bytes" but "which tokens, and how few".  A view answers it declaratively, which is
why the runtime can cache it, cost it, and reproduce it.  Views MUST be
deterministic and MUST NOT call a model: a replay has to charge identical context.

**Datatype matching becomes contracts.**  An MPI type signature checked at both
ends turns a class of silent corruption into a loud error.  The agent analogue is
immediate --- an executor routinely returns prose where an object was asked for,
or six sections where eight were requested --- and catching it at the boundary
stops a malformed artifact from poisoning a downstream reduction.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..errors import err
from ..tokens import count_tokens

__all__ = [
    "Payload",
    "Envelope",
    "Contract",
    "digest_of",
    "canonical",
    "summarise",
    "apply_view",
    "VIEW_SPECS",
    "check_contract",
    "contracts_match",
]


# --------------------------------------------------------------------------
# Canonical form and digests
# --------------------------------------------------------------------------


def canonical(value: Any) -> str:
    """The one serialisation a digest is taken over.

    Content addressing is only useful if two ranks that hold the same value
    compute the same name for it, so the serialisation must be canonical: sorted
    keys, no incidental whitespace, and strings passed through unchanged rather
    than JSON-quoted (an agent that writes a paragraph and an agent that writes
    the same paragraph must agree).
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:32]


def summarise(value: Any, *, limit: int = 24) -> str:
    """A cheap, deterministic, model-free one-line synopsis.

    Without it, a receiver holding only an envelope must materialise the body in
    order to decide whether to materialise the body.  The synopsis is what breaks
    that circularity, and it must be deterministic or a replay will not reproduce
    the receiver's decision.
    """
    text = canonical(value)
    head = " ".join(text.split())[: limit * 6]
    if isinstance(value, dict):
        keys = ", ".join(list(value)[:6])
        return f"object with keys [{keys}]" + ("..." if len(value) > 6 else "")
    if isinstance(value, list):
        return f"array of {len(value)} items; first: {summarise(value[0]) if value else '-'}"
    return head + ("..." if len(text) > len(head) else "")


# --------------------------------------------------------------------------
# Envelope and payload
# --------------------------------------------------------------------------


@dataclass
class Envelope:
    """What an operation reports even when it withholds the body."""

    handle: str
    tokens: int
    bytes: int
    kind: str
    summary: str
    schema: str = ""
    source: int = -1
    tag: int = 0
    comm: str = "world"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "tokens": self.tokens,
            "bytes": self.bytes,
            "kind": self.kind,
            "summary": self.summary,
            "schema": self.schema,
            "source": self.source,
            "tag": self.tag,
            "comm": self.comm,
            "label": self.label,
        }


@dataclass
class Payload:
    """A body plus the envelope that describes it."""

    value: Any
    envelope: Envelope

    @classmethod
    def of(cls, value: Any, *, schema: str = "", **env: Any) -> Payload:
        text = canonical(value)
        kind = (
            "text"
            if isinstance(value, str)
            else "array"
            if isinstance(value, list)
            else "object"
            if isinstance(value, dict)
            else "scalar"
        )
        return cls(
            value=value,
            envelope=Envelope(
                handle=digest_of(value),
                tokens=count_tokens(text),
                bytes=len(text.encode("utf-8")),
                kind=kind,
                summary=summarise(value),
                schema=schema,
                **env,
            ),
        )


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------

VIEW_SPECS: dict[str, str] = {
    "full": "the whole body",
    "head:N": "the first N tokens",
    "tail:N": "the last N tokens",
    "headtail:N": "the first and last N/2 tokens with an elision marker between",
    "lines:A-B": "lines A through B, one-based inclusive",
    "keys:K1,K2": "an object restricted to the named keys",
    "shape": "the structure with every leaf replaced by its type and size",
    "stat": "token count, byte count, line count, digest -- no content at all",
    "grep:PATTERN": "lines matching a regular expression, with their line numbers",
    "chunk:I/N": "the I-th of N equal token-count pieces, one-based",
    "outline": "headings and list markers only, preserving nesting",
}


def _tokens_prefix(text: str, n: int) -> str:
    """Take approximately ``n`` tokens from the front, cutting at a word boundary."""
    if n <= 0:
        return ""
    # Four characters per token is the wrong constant for any specific tokeniser
    # but the right shape for all of them; we then correct by measurement, which
    # keeps the view exact under whichever estimator is configured.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid]) <= n:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    if lo < len(text) and not text[lo].isspace():
        space = cut.rfind(" ")
        if space > len(cut) * 0.6:
            cut = cut[:space]
    return cut


def _tokens_suffix(text: str, n: int) -> str:
    if n <= 0:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[-mid:]) <= n:
            lo = mid
        else:
            hi = mid - 1
    return text[-lo:] if lo else ""


def apply_view(value: Any, spec: str) -> Any:
    """Project ``value`` through a view specification.

    Deterministic and model-free, by construction.  Semantic (model-evaluated)
    compression is deliberately *not* a view: it costs an operator application,
    and the harness has to be able to see that cost.
    """
    spec = (spec or "full").strip()
    if spec == "full":
        return value

    text = canonical(value)

    if spec == "stat":
        return {
            "tokens": count_tokens(text),
            "bytes": len(text.encode("utf-8")),
            "lines": text.count("\n") + 1,
            "digest": digest_of(value),
            "kind": type(value).__name__,
        }

    if spec == "shape":
        return _shape(value)

    if spec == "outline":
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#") or re.match(r"^(\d+\.|[-*+])\s", s) or re.match(r"^\S.*:$", s):
                out.append(line.rstrip())
        return "\n".join(out) if out else _tokens_prefix(text, 200)

    head, _, arg = spec.partition(":")
    if head == "head":
        return _tokens_prefix(text, _int(arg, spec))
    if head == "tail":
        return _tokens_suffix(text, _int(arg, spec))
    if head == "headtail":
        n = _int(arg, spec)
        return _tokens_prefix(text, n // 2) + "\n...[elided]...\n" + _tokens_suffix(text, n // 2)
    if head == "lines":
        m = re.fullmatch(r"(\d+)-(\d+)", arg)
        if not m:
            raise err("AMPI_ERR_ARG", f"view {spec!r} needs lines:A-B")
        a, b = int(m.group(1)), int(m.group(2))
        return "\n".join(text.splitlines()[max(0, a - 1) : b])
    if head == "keys":
        if not isinstance(value, dict):
            raise err("AMPI_ERR_ARG", "view keys: requires an object payload")
        want = [k.strip() for k in arg.split(",") if k.strip()]
        return {k: value[k] for k in want if k in value}
    if head == "grep":
        rx = re.compile(arg)
        return "\n".join(
            f"{i + 1}: {ln}" for i, ln in enumerate(text.splitlines()) if rx.search(ln)
        )
    if head == "chunk":
        m = re.fullmatch(r"(\d+)/(\d+)", arg)
        if not m:
            raise err("AMPI_ERR_ARG", f"view {spec!r} needs chunk:I/N")
        i, n = int(m.group(1)), int(m.group(2))
        if not 1 <= i <= n:
            raise err("AMPI_ERR_ARG", f"chunk index {i} outside 1..{n}")
        total = count_tokens(text)
        per = max(1, (total + n - 1) // n)
        return _tokens_prefix(_tokens_suffix(text, total - per * (i - 1)), per)

    raise err(
        "AMPI_ERR_ARG",
        f"unknown view {spec!r}",
        hint="Known views: " + ", ".join(VIEW_SPECS),
    )


def _int(arg: str, spec: str) -> int:
    try:
        return int(arg)
    except ValueError:
        raise err("AMPI_ERR_ARG", f"view {spec!r} needs an integer argument") from None


def _shape(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [f"list[{len(value)}]", _shape(value[0], depth + 1)] if value else "list[0]"
    if isinstance(value, str):
        return f"str[{count_tokens(value)}tok]"
    return type(value).__name__


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------


@dataclass
class Contract:
    """A typed description of a payload: structure, volume, and intent.

    ``nonempty`` is deliberately separate from ``required``.  Conflating them is a
    trap that costs correctness: a term sheet for a section containing no proper
    nouns legitimately has an empty term map, and a contract that rejected it
    would make the rank retry, fail, and abandon peers already blocked in a
    collective --- over a correct answer.
    """

    name: str = ""
    kind: str = "none"  # text | json | patch | none
    required: tuple[str, ...] = ()
    nonempty: tuple[str, ...] = ()
    min_tokens: int | None = None
    max_tokens: int | None = None
    must_match: str = ""
    must_not_match: str = ""
    #: A natural-language postcondition.  AgentMPI does not check it; it is
    #: carried to the consumer so a human or an agent operator can.
    semantics: str = ""
    #: Fields whose value must equal a per-rank expansion, e.g. ``rank={rank}``.
    #: This is how a payload identifies itself: a scatter slice that says which
    #: rank it is for turns a misrouted block into a loud error at the receiver
    #: rather than a plausible wrong answer three phases later.
    expect: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, spec: str | dict[str, Any] | None) -> Contract | None:
        if spec is None or spec == "":
            return None
        if isinstance(spec, Contract):
            return spec
        if isinstance(spec, str):
            spec = json.loads(spec)
        if not isinstance(spec, dict):
            raise err("AMPI_ERR_ARG", "a contract must be an object")
        return cls(
            name=spec.get("name", ""),
            kind=spec.get("kind", "none"),
            required=tuple(spec.get("required", ())),
            nonempty=tuple(spec.get("nonempty", ())),
            min_tokens=spec.get("min_tokens"),
            max_tokens=spec.get("max_tokens"),
            must_match=spec.get("must_match", ""),
            must_not_match=spec.get("must_not_match", ""),
            semantics=spec.get("semantics", ""),
            expect=dict(spec.get("expect", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        if self.required:
            out["required"] = list(self.required)
        if self.nonempty:
            out["nonempty"] = list(self.nonempty)
        for k in ("min_tokens", "max_tokens"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        for k in ("must_match", "must_not_match", "semantics"):
            if getattr(self, k):
                out[k] = getattr(self, k)
        if self.expect:
            out["expect"] = dict(self.expect)
        return out


def contracts_match(send: Contract | None, recv: Contract | None) -> tuple[bool, str]:
    """MPI's type-signature matching rule, adapted.

    Only ``name``, ``kind`` and ``required`` participate: the receiver's required
    set must be a subset of the sender's, so a receiver may ask for less than is
    offered but never for more.  Volume bounds and semantics do not participate
    in matching --- they are checked, not matched --- because a bound is a
    property of one endpoint's budget, not of the wire.
    """
    if send is None or recv is None:
        return True, ""
    if recv.name and send.name and recv.name != send.name:
        return False, f"contract name {recv.name!r} does not match sender's {send.name!r}"
    if recv.kind != "none" and send.kind != "none" and recv.kind != send.kind:
        return False, f"contract kind {recv.kind!r} does not match sender's {send.kind!r}"
    missing = set(recv.required) - set(send.required)
    if missing:
        return False, f"receiver requires keys the sender does not declare: {sorted(missing)}"
    return True, ""


def check_contract(
    value: Any, contract: Contract | None, *, subs: dict[str, Any] | None = None
) -> list[str]:
    """Validate a payload; return a list of violations (empty means conforming)."""
    if contract is None:
        return []
    v: list[str] = []
    subs = subs or {}

    if contract.kind == "json" and not isinstance(value, (dict, list)):
        v.append(f"kind is 'json' but the payload is {type(value).__name__}")
    if contract.kind == "text" and not isinstance(value, str):
        v.append(f"kind is 'text' but the payload is {type(value).__name__}")

    if contract.required or contract.nonempty or contract.expect:
        if not isinstance(value, dict):
            v.append("required/nonempty/expect need an object payload")
        else:
            for k in contract.required:
                if k not in value:
                    v.append(f"required key {k!r} is absent")
            for k in contract.nonempty:
                if k not in value:
                    v.append(f"nonempty key {k!r} is absent")
                elif value[k] in (None, "", [], {}):
                    v.append(f"key {k!r} is present but empty")
            for k, tmpl in contract.expect.items():
                want = tmpl.format(**subs) if subs else tmpl
                have = value.get(k)
                if str(have) != str(want):
                    v.append(f"self-identifying field {k!r} is {have!r}, expected {want!r}")

    text = canonical(value)
    n = count_tokens(text)
    if contract.min_tokens is not None and n < contract.min_tokens:
        v.append(f"{n} tokens is below the declared minimum of {contract.min_tokens}")
    if contract.max_tokens is not None and n > contract.max_tokens:
        v.append(f"{n} tokens exceeds the declared maximum of {contract.max_tokens}")
    if contract.must_match and not re.search(contract.must_match, text, re.S):
        v.append(f"payload does not match required pattern {contract.must_match!r}")
    if contract.must_not_match and re.search(contract.must_not_match, text, re.S):
        v.append(f"payload matches forbidden pattern {contract.must_not_match!r}")
    return v
