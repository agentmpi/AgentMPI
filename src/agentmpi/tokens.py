"""Token accounting.

Tokens are AgentMPI's unit of *size*, the way bytes are MPI's unit of size.
Every payload has a token cost, every rank has a context capacity measured
in tokens, and every collective is analysed in terms of the peak token
ingest it imposes on any participating rank.

The estimator is deliberately pluggable and deliberately cheap.  Exact
tokenisation requires the model's tokeniser, which a harness may not have;
the runtime therefore uses a conservative estimator by default and upgrades
to an exact tokeniser when one is importable.  Because the runtime uses
token counts for *admission control* rather than for billing, a
conservative over-estimate is the safe direction to err in.
"""

from __future__ import annotations

import functools
import os
import re
from typing import Callable, Protocol

#: Characters per token used by the fallback estimator.  Empirically the
#: BPE vocabularies used by current frontier models average 3.5-4.2
#: characters per token on English prose and 2.8-3.5 on source code; 3.6 is
#: a mildly conservative single constant across both.
_CHARS_PER_TOKEN = 3.6

#: Additive per-message overhead: role headers, delimiters, and the
#: envelope preamble the runtime renders around every payload.
ENVELOPE_TOKEN_OVERHEAD = 12


class Tokenizer(Protocol):
    def count(self, text: str) -> int: ...


class HeuristicTokenizer:
    """Character-ratio estimator with a whitespace-aware correction.

    Pure character division badly under-counts text with many short tokens
    (code, JSON, tables).  We take the max of the character estimate and a
    word-plus-punctuation estimate, which tracks real tokenisers within
    roughly 10% on mixed English/code corpora.
    """

    name = "heuristic"

    _WORDISH = re.compile(r"\w+|[^\w\s]")

    def count(self, text: str) -> int:
        if not text:
            return 0
        by_chars = len(text) / _CHARS_PER_TOKEN
        by_words = len(self._WORDISH.findall(text)) * 0.85
        return int(max(by_chars, by_words)) + 1


class TiktokenTokenizer:
    """Exact BPE tokenisation when :mod:`tiktoken` is available."""

    name = "tiktoken"

    def __init__(self, encoding: str = "cl100k_base") -> None:
        import tiktoken  # noqa: PLC0415 - optional dependency probed at runtime

        self._enc = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text, disallowed_special=()))


@functools.lru_cache(maxsize=1)
def default_tokenizer() -> Tokenizer:
    """Return the best tokenizer available in this environment."""
    if os.environ.get("AMPI_TOKENIZER", "").lower() == "heuristic":
        return HeuristicTokenizer()
    try:
        return TiktokenTokenizer()
    except Exception:  # pragma: no cover - depends on the environment
        return HeuristicTokenizer()


def count_tokens(text: str) -> int:
    """Estimated token cost of ``text``."""
    return default_tokenizer().count(text)


def message_tokens(text: str) -> int:
    """Token cost of ingesting ``text`` as one AgentMPI message."""
    return count_tokens(text) + ENVELOPE_TOKEN_OVERHEAD


def truncate_to_tokens(text: str, budget: int, marker: str = "\n...[truncated]...\n") -> str:
    """Hard-truncate ``text`` so that it costs at most ``budget`` tokens.

    This is the last-resort lossy path.  A harness should normally supply a
    semantic digest function instead; see :mod:`agentmpi.context`.
    """
    if budget <= 0:
        return ""
    if count_tokens(text) <= budget:
        return text
    # Binary search on the character prefix; token count is monotone in it.
    lo, hi = 0, len(text)
    marker_cost = count_tokens(marker)
    target = max(budget - marker_cost, 1)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid]) <= target:
            lo = mid
        else:
            hi = mid - 1
    head = int(lo * 0.7)
    tail_chars = lo - head
    if tail_chars > 0:
        return text[:head] + marker + text[len(text) - tail_chars:]
    return text[:lo] + marker


DigestFn = Callable[[str, int], str]
