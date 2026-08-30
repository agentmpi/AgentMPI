"""Calibration of the fallback token estimator against a real BPE.

The eager/rendezvous threshold, the context budget and the view budgets are all
denominated in tokens, so the estimator is a protocol-visible component: two
implementations that disagree about a payload's size will disagree about whether
it is delivered inline. AgentMPI therefore specifies a *reference estimator* and
requires implementations to report which one they used.

These tests pin the fallback estimator's accuracy against ``cl100k_base`` when
``tiktoken`` is installed, and skip otherwise. The bound we hold ourselves to is
a median relative error under 20% and a worst case under 60% across prose, code,
JSON and CJK -- loose enough to be achievable without a tokeniser, tight enough
that a threshold set at 700 tokens is not accidentally a threshold at 2000.
"""

from __future__ import annotations

import json
import statistics

import pytest

from ampi import tokens as tok

CORPUS = {
    "prose_en": (
        "The Message Passing Interface was standardised because the early 1990s had produced a "
        "dozen mutually incompatible message passing libraries, each tied to one vendor's "
        "machine, and application authors were paying the cost of that fragmentation twice: "
        "once when they wrote portability layers, and again when they discovered that the "
        "layers hid the performance characteristics they most needed to see."
    ),
    "prose_long": "Parallel programs are hard to reason about. " * 40,
    "code_py": (
        "def binomial_children(rank: int, size: int, root: int) -> list[int]:\n"
        "    vr = (rank - root) % size\n"
        "    kids: list[int] = []\n"
        "    mask = 1\n"
        "    while mask < size:\n"
        "        if vr & mask:\n"
        "            break\n"
        "        mask <<= 1\n"
        "    mask >>= 1\n"
        "    while mask >= 1:\n"
        "        child = vr + mask\n"
        "        if child < size:\n"
        "            kids.append((child + root) % size)\n"
        "        mask >>= 1\n"
        "    return kids\n"
    ),
    "json_small": json.dumps({"rank": 3, "tag": 7, "tokens": 1024, "mode": "rendezvous"}),
    "json_nested": json.dumps(
        {"glossary": [{"term": f"t{i}", "translation": f"x{i}", "notes": "keep consistent"}
                      for i in range(30)]},
        ensure_ascii=False,
    ),
    "markdown": (
        "# Phase 2\n\n"
        "## Assignment\n\n"
        "- translate chapters 12-14\n"
        "- reuse the glossary from `ampi win get --win shared --key glossary`\n\n"
        "> Do not invent terminology.\n"
    ),
    "cjk": "这是一个用于测试分词器行为的中文段落。" * 8,
    "mixed": "Rank 7 reported AMPI_ERR_PROC_FAILED after 干活 for 300 seconds; retry with --timeout 600.",
}


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_fallback_estimator_is_within_tolerance(name: str) -> None:
    tok._try_tiktoken()
    if tok._ENCODER is None:
        pytest.skip("tiktoken not installed; the fallback estimator is the reference")
    text = CORPUS[name]
    exact = len(tok._ENCODER.encode(text, disallowed_special=()))
    est = tok._structural_estimate(text)
    rel = abs(est - exact) / max(1, exact)
    assert rel < 0.60, f"{name}: estimate {est} vs exact {exact} (rel {rel:.2f})"


def test_fallback_median_error() -> None:
    tok._try_tiktoken()
    if tok._ENCODER is None:
        pytest.skip("tiktoken not installed")
    errs = []
    for text in CORPUS.values():
        exact = len(tok._ENCODER.encode(text, disallowed_special=()))
        est = tok._structural_estimate(text)
        errs.append(abs(est - exact) / max(1, exact))
    assert statistics.median(errs) < 0.20, f"median relative error {statistics.median(errs):.3f}"


def test_truncate_respects_budget() -> None:
    text = CORPUS["prose_long"]
    for budget in (10, 50, 200, 1000):
        out = tok.truncate_to_tokens(text, budget)
        assert tok.count(out) <= budget, f"budget {budget} exceeded"


def test_count_is_monotone_in_prefix_length() -> None:
    text = CORPUS["prose_en"]
    prev = 0
    for i in range(0, len(text), 37):
        cur = tok.count(text[:i])
        assert cur >= prev
        prev = cur
