"""Views: the AgentMPI analogue of MPI derived datatypes.

An MPI derived datatype is a *declarative description of how to access a
buffer*: ``MPI_Type_vector`` says "take 10 blocks of 4 doubles, stride 100", and
the library then gathers exactly those elements without the user writing a copy
loop. The point is that the access pattern is data the library can optimise,
rather than control flow it cannot see.

AgentMPI's scarce resource is context, not memory bandwidth, so the analogous
question is not "which bytes" but "which *tokens*, and how few". A view is a
declarative description of how to project a large payload into a bounded number
of tokens: take the first N tokens, take these JSON keys, take the lines
matching this pattern, take a structural digest. The receiver names a view
instead of a payload, and the runtime computes it -- deterministically, so a
replay charges exactly the same context.

Views are deliberately *not* LLM summarisation. They are free, exact and
reproducible. Semantic compression exists too, but it is an agent-evaluated
operation (see :mod:`ampi.ops`) with a cost the harness can see.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from . import tokens as tok
from .errors import ArgError
from .journal import Journal, now_ns

#: Built-in view operators.
VIEW_OPS = (
    "full",       # the whole payload (may still be budget-clipped)
    "head",       # first `budget` tokens
    "tail",       # last `budget` tokens
    "headtail",   # both ends, elided middle -- mitigates lost-in-the-middle
    "lines",      # a line range, like a strided datatype over a text buffer
    "keys",       # selected JSON keys (an MPI_Type_indexed over a dict)
    "shape",      # structural digest only: keys, lengths, types
    "grep",       # lines matching a regex, with context
    "chunk",      # the i-th of n equal chunks (an MPI_Type_create_darray)
    "outline",    # markdown/section headings only
    "stat",       # counts only: tokens, bytes, lines, digest
)


def parse_spec(spec: str) -> Dict[str, Any]:
    """Parse a compact view spec such as ``head:800`` or ``keys:a,b;budget=400``.

    A terse textual grammar matters here: the spec is typed by an LLM into a
    shell command, so it must be short and hard to get wrong.
    """
    out: Dict[str, Any] = {"op": "full"}
    spec = (spec or "").strip()
    if not spec:
        return out
    parts = [p for p in spec.split(";") if p.strip()]
    head = parts[0]
    if ":" in head:
        op, arg = head.split(":", 1)
    else:
        op, arg = head, ""
    op = op.strip().lower()
    if op not in VIEW_OPS:
        raise ArgError(
            f"unknown view op {op!r}",
            hint="available: " + ", ".join(VIEW_OPS),
        )
    out["op"] = op
    arg = arg.strip()
    if arg:
        if op in ("head", "tail", "headtail"):
            out["budget"] = int(arg)
        elif op == "lines":
            m = re.match(r"^(\d+)(?:-(\d+))?$", arg)
            if not m:
                raise ArgError("lines spec must be N or N-M (1-based, inclusive)")
            out["start"] = int(m.group(1))
            out["end"] = int(m.group(2) or m.group(1))
        elif op == "keys":
            out["keys"] = [k for k in re.split(r"[,\s]+", arg) if k]
        elif op == "grep":
            out["pattern"] = arg
        elif op == "chunk":
            m = re.match(r"^(\d+)/(\d+)$", arg)
            if not m:
                raise ArgError("chunk spec must be i/n (0-based i)")
            out["i"] = int(m.group(1))
            out["n"] = int(m.group(2))
    for extra in parts[1:]:
        if "=" not in extra:
            continue
        k, v = extra.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in ("budget", "ctx", "before", "after"):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def spec_key(spec: Dict[str, Any]) -> str:
    return json.dumps(spec, sort_keys=True, ensure_ascii=False)


def render_view(j: Journal, oid: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Materialise a view of object ``oid``. Cached and deterministic."""
    key = spec_key(spec)
    vid = "v:" + hashlib.sha256((oid + "|" + key).encode()).hexdigest()[:20]
    row = j.q1("SELECT * FROM objview WHERE id=?", (vid,))
    if row is not None:
        return {"id": vid, "body": row["body"], "tokens": int(row["tokens"]), "cached": True}
    text = j.object_text(oid)
    body = _apply(text, spec)
    budget = spec.get("budget")
    if budget:
        body = tok.truncate_to_tokens(body, int(budget))
    ntok = tok.count(body)
    with j.tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO objview(id,obj,spec,tokens,body,created_ns) VALUES(?,?,?,?,?,?)",
            (vid, oid, key, ntok, body, now_ns()),
        )
    return {"id": vid, "body": body, "tokens": ntok, "cached": False}


def _apply(text: str, spec: Dict[str, Any]) -> str:
    op = spec.get("op", "full")
    if op == "full":
        return text
    if op == "head":
        return tok.truncate_to_tokens(text, int(spec.get("budget", 600)))
    if op == "tail":
        return _tail_tokens(text, int(spec.get("budget", 600)))
    if op == "headtail":
        b = int(spec.get("budget", 800))
        half = max(100, b // 2)
        return (
            tok.truncate_to_tokens(text, half, marker="\n...[middle elided]...\n")
            + _tail_tokens(text, b - half)
        )
    if op == "lines":
        lines = text.splitlines()
        s = max(1, int(spec.get("start", 1)))
        e = min(len(lines), int(spec.get("end", s)))
        return "\n".join(lines[s - 1 : e])
    if op == "chunk":
        n = max(1, int(spec.get("n", 1)))
        i = max(0, min(n - 1, int(spec.get("i", 0))))
        lines = text.splitlines()
        per = (len(lines) + n - 1) // n
        return "\n".join(lines[i * per : (i + 1) * per])
    if op == "keys":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ArgError("view op 'keys' requires a JSON object payload")
        want = spec.get("keys") or []
        return json.dumps({k: data[k] for k in want if k in data}, ensure_ascii=False, indent=2)
    if op == "shape":
        try:
            data = json.loads(text)
        except Exception:
            lines = text.splitlines()
            return json.dumps(
                {
                    "kind": "text",
                    "lines": len(lines),
                    "tokens": tok.count(text),
                    "first_line": lines[0][:120] if lines else "",
                },
                ensure_ascii=False,
            )
        return json.dumps(_shape(data), ensure_ascii=False, indent=2)
    if op == "grep":
        pat = re.compile(str(spec.get("pattern", ".")), re.IGNORECASE)
        before = int(spec.get("before", 0))
        after = int(spec.get("after", 0))
        lines = text.splitlines()
        keep: set[int] = set()
        for i, line in enumerate(lines):
            if pat.search(line):
                for k in range(max(0, i - before), min(len(lines), i + after + 1)):
                    keep.add(k)
        out: List[str] = []
        prev = -2
        for i in sorted(keep):
            if i != prev + 1 and out:
                out.append("...")
            out.append(f"{i + 1}: {lines[i]}")
            prev = i
        return "\n".join(out) if out else "(no matching lines)"
    if op == "outline":
        out = []
        for i, line in enumerate(text.splitlines(), 1):
            if re.match(r"^\s{0,3}#{1,6}\s", line) or re.match(
                r"^\s*(?:class|def|function|func|fn|struct|interface|type)\s+\w+", line
            ):
                out.append(f"{i}: {line.strip()}")
        return "\n".join(out) if out else "(no headings or definitions found)"
    if op == "stat":
        return json.dumps(
            {
                "tokens": tok.count(text),
                "bytes": len(text.encode("utf-8")),
                "lines": len(text.splitlines()),
                "sha256_12": hashlib.sha256(text.encode()).hexdigest()[:12],
            },
            ensure_ascii=False,
        )
    raise ArgError(f"unhandled view op {op!r}")


def _tail_tokens(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if tok.count(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if tok.count(text[mid:]) <= budget:
            hi = mid
        else:
            lo = mid + 1
    return "...[head elided]...\n" + text[lo:]


def _shape(data: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "..."
    if isinstance(data, dict):
        return {k: _shape(v, depth + 1) for k, v in list(data.items())[:40]}
    if isinstance(data, list):
        return [f"list[{len(data)}]", _shape(data[0], depth + 1)] if data else []
    if isinstance(data, str):
        return f"str[{len(data)}]"
    return type(data).__name__
