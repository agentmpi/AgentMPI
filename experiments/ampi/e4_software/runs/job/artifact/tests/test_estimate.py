"""Tests for :mod:`tokenbudget.estimate`, the leaf estimator.

Covers the properties the contract states explicitly --- zero for empty and
``None`` input, determinism, non-negativity, monotonicity in ``len(text)``,
and the four-token-per-message overhead of ``estimate_messages`` --- plus the
edge cases the rest of the package depends on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tokenbudget import estimate

SOURCE = Path(__file__).resolve().parents[1] / "tokenbudget" / "estimate.py"


@pytest.mark.parametrize("empty", ["", None])
def test_count_tokens_is_zero_for_empty_or_none_input(empty):
    assert estimate.count_tokens(empty) == 0


def test_count_tokens_is_deterministic_and_never_negative():
    texts = ["a", "hello world", "\n\t ", "unicode: \u00e9\u00e8\u00ea \u4e2d\u6587", "x" * 5000]
    for text in texts:
        first = estimate.count_tokens(text)
        assert first == estimate.count_tokens(text)
        assert first >= 0


def test_count_tokens_is_monotone_non_decreasing_in_length():
    text = "the quick brown fox jumps over the lazy dog. " * 30
    counts = [estimate.count_tokens(text[:length]) for length in range(len(text) + 1)]
    assert counts == sorted(counts)
    assert counts[-1] == estimate.count_tokens(text)


def test_count_tokens_charges_a_token_per_partial_chunk():
    assert estimate.count_tokens("a") == 1
    assert estimate.count_tokens("abcd") == 1
    assert estimate.count_tokens("abcde") == 2
    assert estimate.count_tokens("x" * 400) == 400 // estimate.CHARS_PER_TOKEN


def test_count_tokens_depends_only_on_length():
    assert estimate.count_tokens("abcdefgh") == estimate.count_tokens("!@#$%^&*")


def test_count_tokens_rejects_non_text_input():
    with pytest.raises(TypeError):
        estimate.count_tokens(42)


def test_estimate_messages_of_no_messages_is_zero():
    assert estimate.estimate_messages([]) == 0


def test_estimate_messages_is_content_plus_fixed_overhead_per_message():
    contents = ["hello there", "a considerably longer reply than the question", ""]
    messages = [{"role": "user", "content": text} for text in contents]

    expected = sum(estimate.count_tokens(text) for text in contents)
    expected += estimate.PER_MESSAGE_OVERHEAD * len(messages)
    assert estimate.estimate_messages(messages) == expected

    for text in contents:
        single = [{"role": "assistant", "content": text}]
        assert estimate.estimate_messages(single) == (
            estimate.count_tokens(text) + estimate.PER_MESSAGE_OVERHEAD
        )


@pytest.mark.parametrize("message", [{}, {"role": "user"}, {"content": None}, {"content": ""}])
def test_messages_without_usable_content_cost_only_the_overhead(message):
    assert estimate.estimate_messages([message]) == estimate.PER_MESSAGE_OVERHEAD


def test_estimate_messages_grows_with_the_transcript_and_leaves_it_untouched():
    messages = [{"role": "user", "content": "question one"}]
    before = estimate.estimate_messages(messages)
    snapshot = [dict(message) for message in messages]

    messages.append({"role": "assistant", "content": "an answer"})
    assert estimate.estimate_messages(messages) > before
    assert snapshot == [{"role": "user", "content": "question one"}]
    assert messages[0] == {"role": "user", "content": "question one"}


def test_estimate_messages_rejects_malformed_input():
    with pytest.raises(TypeError):
        estimate.estimate_messages(["not a mapping"])
    with pytest.raises(TypeError):
        estimate.estimate_messages([{"role": "user", "content": 3}])


def test_estimate_stays_a_leaf_with_a_docstring():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    assert ast.get_docstring(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "tokenbudget"
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "estimate must not use relative imports"
            assert (node.module or "").split(".")[0] != "tokenbudget"
