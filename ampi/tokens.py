"""Token accounting.

In MPI the unit of transfer cost is the byte, and `MPI_Type_size` is exact. In
AgentMPI the unit of transfer cost is the *token*, because a message's real
price is the context window space and the prefill compute it consumes on the
receiving agent. Tokens are therefore the currency of the whole protocol: the
eager/rendezvous threshold, the context budget, the view budgets, and the cost
model are all denominated in tokens.

Exactness is not achievable in a model-agnostic protocol (every model family
tokenises differently), so AgentMPI specifies a *reference estimator* and
requires implementations to report which estimator they used. If ``tiktoken``
is importable we use the real ``cl100k_base`` BPE; otherwise we fall back to a
calibrated structural estimator. Both are deterministic, which matters because
the eager/rendezvous decision must be reproducible across replays.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_ENCODER = None
_ENCODER_NAME = "ampi-structural-v1"
_TRIED_TIKTOKEN = False


def _try_tiktoken() -> None:
    global _ENCODER, _ENCODER_NAME, _TRIED_TIKTOKEN
    if _TRIED_TIKTOKEN:
        return
    _TRIED_TIKTOKEN = True
    try:  # pragma: no cover - depends on optional dependency
        import tiktoken  # type: ignore

        _ENCODER = tiktoken.get_encoding("cl100k_base")
        _ENCODER_NAME = "tiktoken:cl100k_base"
    except Exception:
        _ENCODER = None


# A BPE tokeniser splits on a small number of structural boundaries and then
# merges frequent substrings. The dominant term for English prose is
# "whitespace-delimited words, with long or rare words split further"; for code
# and JSON, punctuation dominates. This regex approximates the pre-tokenisation
# step of GPT-family BPEs (contractions, letter runs, digit runs, punctuation
# runs, whitespace runs), after which we charge long alphabetic runs extra.
_PRE = re.compile(
    r"""'(?:[sdmt]|ll|ve|re)|[^\r\n\w]?[A-Za-z]+|\d{1,3}|\s*[^\s\w]+|\s+(?!\S)|\s+""",
    re.UNICODE,
)


def _structural_estimate(text: str) -> int:
    """Deterministic fallback token estimate.

    Calibration target: GPT-family BPEs average ~4 characters/token on English
    prose and ~3 characters/token on source code and JSON. We reproduce that by
    pre-tokenising and then charging one token per piece plus one extra token
    per 6 characters of any alphabetic piece longer than 6 characters (rare or
    compound words get split by BPE).
    """
    if not text:
        return 0
    total = 0
    for piece in _PRE.findall(text):
        total += 1
        stripped = piece.strip()
        n = len(stripped)
        if n > 6 and stripped.isalpha():
            total += (n - 1) // 6
        elif n > 3 and not stripped.isalpha():
            # Punctuation runs (e.g. "):{" or "-----") rarely merge fully.
            total += (n - 1) // 3
    # Non-ASCII text (e.g. CJK) is far denser per character than the regex
    # above assumes; charge it separately at ~1 token per character, which is
    # the documented behaviour for CJK under cl100k_base.
    non_ascii = sum(1 for ch in text if ord(ch) > 0x7F)
    if non_ascii:
        total += int(non_ascii * 0.7)
    return max(1, total)


def count(text: str) -> int:
    """Return the token count of ``text`` under the active estimator."""
    if not text:
        return 0
    _try_tiktoken()
    if _ENCODER is not None:  # pragma: no cover - optional dependency
        try:
            return len(_ENCODER.encode(text, disallowed_special=()))
        except Exception:
            pass
    return _structural_estimate(text)


def estimator_name() -> str:
    _try_tiktoken()
    return _ENCODER_NAME


def measure(text: str) -> Tuple[int, int]:
    """Return ``(tokens, bytes)`` for ``text``."""
    return count(text), len(text.encode("utf-8"))


def truncate_to_tokens(text: str, budget: int, *, marker: Optional[str] = None) -> str:
    """Truncate ``text`` so that it costs at most ``budget`` tokens.

    Uses binary search over character length against :func:`count`, which is
    exact for the tiktoken path and monotone for the fallback path.
    """
    if budget <= 0:
        return ""
    if count(text) <= budget:
        return text
    marker = marker if marker is not None else "\n...[truncated by AMPI_Type_view]..."
    # The marker must fit inside the budget, or the "truncation" would itself
    # overrun it. Degrade the marker before degrading the guarantee: a view that
    # silently exceeds its budget defeats the whole point of context accounting.
    for candidate in (marker, "\n...[truncated]...", " …", ""):
        if count(candidate) < budget:
            marker = candidate
            break
    else:
        marker = ""
    target = max(1, budget - count(marker))
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count(text[:mid]) <= target:
            lo = mid
        else:
            hi = mid - 1
    out = text[:lo] + marker
    while out and count(out) > budget:
        lo = max(0, lo - max(1, lo // 8))
        out = text[:lo] + marker
    return out
