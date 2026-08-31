"""Tests for tokenbudget.estimate."""

from __future__ import annotations

import pytest

from tokenbudget.estimate import (
    PER_MESSAGE_OVERHEAD,
    count_tokens,
    estimate_messages,
)

SAMPLES = [
    "",
    "a",
    "ab",
    "abc",
    "abcd",
    "abcde",
    "hello world",
    "the quick brown fox jumps over the lazy dog",
    "\n\t  ",
    "unicode: \u00e9\u00e8\u00ea \u4f60\u597d",
    "x" * 999,
]


def test_empty_and_none_cost_nothing() -> None:
    assert count_tokens("") == 0
    assert count_tokens(None) == 0


def test_count_tokens_is_deterministic() -> None:
    for text in SAMPLES:
        first = count_tokens(text)
        assert all(count_tokens(text) == first for _ in range(5))


def test_count_tokens_is_monotone_non_decreasing_in_length() -> None:
    ordered = sorted(SAMPLES, key=len)
    counts = [count_tokens(text) for text in ordered]
    assert counts == sorted(counts)
    # And growing one string a character at a time never drops the count.
    growing = "budgeting tokens across cooperating agents"
    for size in range(1, len(growing) + 1):
        assert count_tokens(growing[:size]) >= count_tokens(growing[: size - 1])


def test_count_tokens_is_never_negative() -> None:
    assert all(count_tokens(text) >= 0 for text in SAMPLES)


def test_equal_length_texts_cost_the_same() -> None:
    assert count_tokens("aaaa aaaa") == count_tokens("zz zz zz1")
    assert count_tokens("x" * 40) == count_tokens("y" * 40)


def test_nonempty_text_costs_at_least_one_token() -> None:
    assert count_tokens("a") >= 1
    assert count_tokens(" ") >= 1


def test_count_tokens_rejects_non_text() -> None:
    with pytest.raises(TypeError):
        count_tokens(17)  # type: ignore[arg-type]


def test_estimate_messages_sums_content_plus_fixed_overhead() -> None:
    messages = [
        {"role": "system", "content": "be concise"},
        {"role": "user", "content": "how many tokens is this sentence?"},
    ]
    expected = sum(PER_MESSAGE_OVERHEAD + count_tokens(m["content"]) for m in messages)
    assert estimate_messages(messages) == expected


def test_estimate_messages_of_empty_list_is_zero() -> None:
    assert estimate_messages([]) == 0


def test_estimate_messages_charges_overhead_for_contentless_messages() -> None:
    assert estimate_messages([{"role": "user"}]) == PER_MESSAGE_OVERHEAD
    assert estimate_messages([{"role": "user", "content": None}]) == PER_MESSAGE_OVERHEAD
    assert estimate_messages([{}, {}]) == 2 * PER_MESSAGE_OVERHEAD


def test_estimate_messages_grows_with_each_message() -> None:
    one = [{"role": "user", "content": "hello"}]
    two = one + [{"role": "assistant", "content": "hi"}]
    assert estimate_messages(two) > estimate_messages(one)


def test_estimate_messages_rejects_non_mapping_entries() -> None:
    with pytest.raises(TypeError):
        estimate_messages(["not a message"])  # type: ignore[list-item]
