"""Token accounting.

Tokens are AgentMPI's unit of *volume*: they play the role bytes play in MPI's
cost model, and the role memory plays in MPI's buffering rules.  Because the
runtime must be able to price a payload without contacting a model provider,
counting is pluggable with a deterministic offline default.

The default estimator is a character/word blend calibrated against GPT-family
BPE tokenisers on English prose and on source code; it is documented as an
*estimate* and every measurement in the paper that depends on exact token
counts is reported from provider-returned usage instead.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from typing import Any

_WORD_RE = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` without a tokeniser.

    English prose runs at roughly 0.75 tokens per whitespace word and 0.25
    tokens per character; source code is denser in punctuation and runs closer
    to 0.30 tokens per character.  Blending a word-based and a
    character-based estimate and taking the maximum keeps the estimator
    conservative (it never *under*-counts by much), which is the safe
    direction for a budget check.
    """
    if not text:
        return 0
    n_chars = len(text)
    n_atoms = len(_WORD_RE.findall(text))
    by_chars = n_chars / 3.8
    by_atoms = n_atoms * 0.82
    return max(1, int(round(max(by_chars, by_atoms))))


def _canonical(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8", errors="replace")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


class TokenCounter:
    """Pluggable token counter.

    Harnesses that have a real tokeniser available can install it once::

        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        agentmpi.tokens.COUNTER.install(lambda s: len(enc.encode(s)))

    and every subsequent budget check, eager/rendezvous decision and cost
    report uses exact counts.
    """

    def __init__(self) -> None:
        self._fn: Callable[[str], int] = estimate_tokens
        self.exact = False

    def install(self, fn: Callable[[str], int], *, exact: bool = True) -> None:
        self._fn = fn
        self.exact = exact

    def count(self, payload: Any) -> int:
        return self._fn(_canonical(payload))

    def count_text(self, text: str) -> int:
        return self._fn(text)


#: Process-wide counter used by the runtime.
COUNTER = TokenCounter()


def count(payload: Any) -> int:
    """Token volume of ``payload`` under the installed counter."""
    return COUNTER.count(payload)


def try_install_tiktoken(encoding: str = "cl100k_base") -> bool:
    """Install a real BPE tokeniser if ``tiktoken`` is importable.

    Returns ``True`` on success.  Kept as an explicit opt-in call rather than
    an import-time side effect so that the runtime has no network dependency.
    """
    try:
        tiktoken = importlib.import_module("tiktoken")
        enc = tiktoken.get_encoding(encoding)
    except Exception:
        return False
    COUNTER.install(lambda s: len(enc.encode(s, disallowed_special=())), exact=True)
    return True
