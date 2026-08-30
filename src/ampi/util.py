"""Small shared helpers: token accounting, hashing, JSON, and time."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------
# AgentMPI measures every transfer in tokens rather than bytes, because tokens
# are what the receiver's context window is denominated in and what the
# provider bills for.  We deliberately use a tokenizer-free estimator: the
# protocol must not depend on any particular model's vocabulary, exactly as MPI
# does not depend on any particular wire encoding.  Implementations are free to
# substitute an exact tokenizer; the spec requires only that the estimate be
# deterministic, monotone in input length, and consistent across ranks.

_WORDISH = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Empirically ~0.75 words per token for English prose and ~2.8 chars/token for
# structured text; we take the max of a word-based and a character-based
# estimate so neither dense JSON nor sparse prose is badly underestimated.
_CHARS_PER_TOKEN = 3.6
_TOKENS_PER_WORDISH = 1.15


def count_tokens(text: str | None) -> int:
    """Deterministic, tokenizer-independent token estimate."""
    if not text:
        return 0
    by_chars = len(text) / _CHARS_PER_TOKEN
    by_words = len(_WORDISH.findall(text)) * _TOKENS_PER_WORDISH
    return int(math.ceil(max(by_chars, by_words)))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def dumps(obj: Any) -> str:
    """Canonical JSON: sorted keys so hashes are stable across ranks."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Identity and time
# ---------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Digests: the lossy projection used by the rendezvous protocol
# ---------------------------------------------------------------------------


def structural_digest(text: str, budget_tokens: int = 120) -> str:
    """Cheap, deterministic summary of an artifact.

    This is intentionally *structural*, not semantic: it costs no LLM call, so
    the rendezvous handshake itself is free.  A rank that needs a semantic
    summary asks for one explicitly with an AMPI_Op upcall, and pays for it.
    """
    if not text:
        return ""
    lines = [ln.rstrip() for ln in text.splitlines()]
    nonempty = [ln for ln in lines if ln.strip()]
    head: list[str] = []
    used = 0
    for line in nonempty:
        cost = count_tokens(line) + 1
        if used + cost > budget_tokens:
            break
        head.append(line)
        used += cost
    body = "\n".join(head)
    stats = (
        f"[{len(text)} chars, {len(lines)} lines, ~{count_tokens(text)} tokens, "
        f"sha256:{sha256_text(text)[:12]}]"
    )
    if len(head) < len(nonempty):
        return f"{body}\n... ({len(nonempty) - len(head)} more lines) {stats}"
    return f"{body} {stats}" if body else stats


def clamp_text(text: str, budget_tokens: int) -> str:
    """Truncate to a token budget, marking the truncation explicitly."""
    if count_tokens(text) <= budget_tokens:
        return text
    approx_chars = int(budget_tokens * _CHARS_PER_TOKEN)
    return text[:approx_chars] + f"\n...[truncated to ~{budget_tokens} tokens]"
