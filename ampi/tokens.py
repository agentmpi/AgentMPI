"""Token accounting.

The unit of transfer cost in AgentMPI is the token, not the byte, because the
scarce resource is the receiver's context window rather than its memory.  Every
threshold in the protocol is denominated in tokens, so the estimator is part of
the observable behaviour of an implementation and S2.1 requires it to be reported.

Two estimators are provided.  ``tiktoken`` (``cl100k_base``) when it is installed,
and a structural estimator otherwise.  The structural estimator is not a guess at
a byte ratio: it counts word-like runs, punctuation runs and whitespace-delimited
symbols separately, because JSON and prose have very different tokens-per-byte and
agent payloads are usually one or the other.

Two implementations using different estimators may make different eager/rendezvous
decisions for the same payload.  This is permitted --- the decision affects cost,
not correctness --- but it is *not* permitted for a producer and the checker that
grades it to use different estimators.  A constraint the constrained party cannot
evaluate is not a constraint but a guess, and parties guess conservatively: in an
earlier run a rank submitted half the content it could have because the runtime
owned the counter and did not expose it.  Hence :func:`counter_name` and the
``ampi tokens`` command.
"""

from __future__ import annotations

import functools
import re
from typing import Any

__all__ = ["count_tokens", "counter_name", "estimator_report"]

_WORD = re.compile(r"[A-Za-z]+|[0-9]+|[^\sA-Za-z0-9]+")


@functools.lru_cache(maxsize=1)
def _tiktoken_encoder() -> Any | None:
    try:  # pragma: no cover - depends on the environment
        import tiktoken
    except Exception:
        return None
    try:  # pragma: no cover
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _structural(text: str) -> int:
    """Estimate tokens without a vocabulary.

    A BPE tokeniser splits long alphabetic runs into sub-word pieces at roughly one
    piece per four to five characters, keeps short words whole, and emits one token
    per punctuation character in a run of punctuation (which is why JSON is dense).
    Modelling those three behaviours separately keeps the median relative error
    against ``cl100k_base`` under 20% on the reference corpus, which is verified by
    ``tests/test_tokens.py`` rather than asserted here.
    """
    total = 0
    for match in _WORD.finditer(text):
        piece = match.group(0)
        first = piece[0]
        if first.isalpha():
            total += 1 if len(piece) <= 5 else 1 + (len(piece) - 5 + 3) // 4
        elif first.isdigit():
            # Digit runs tokenise about three characters at a time.
            total += max(1, (len(piece) + 2) // 3)
        else:
            total += len(piece)
    return total


def count_tokens(payload: Any) -> int:
    """Count the tokens a payload would occupy in an executor's context."""
    if payload is None:
        return 0
    if not isinstance(payload, str):
        import json

        payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    enc = _tiktoken_encoder()
    if enc is not None:  # pragma: no cover - depends on the environment
        return len(enc.encode(payload, disallowed_special=()))
    return _structural(payload)


def counter_name() -> str:
    """The estimator this runtime uses.  Reported by ``ampi info`` (S2.1)."""
    return "cl100k_base" if _tiktoken_encoder() is not None else "structural-v1"


def estimator_report(samples: list[str]) -> dict[str, Any]:
    """Compare the structural estimator against ``cl100k_base`` where available."""
    enc = _tiktoken_encoder()
    out: dict[str, Any] = {"counter": counter_name(), "n": len(samples)}
    if enc is None:  # pragma: no cover - depends on the environment
        out["reference_available"] = False
        return out
    errors = []
    for s in samples:  # pragma: no cover - depends on the environment
        ref = len(enc.encode(s, disallowed_special=()))
        if ref == 0:
            continue
        errors.append(abs(_structural(s) - ref) / ref)
    errors.sort()
    out["reference_available"] = True
    if errors:
        out["median_rel_error"] = errors[len(errors) // 2]
        out["p95_rel_error"] = errors[int(0.95 * (len(errors) - 1))]
    return out
