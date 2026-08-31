"""Tests for tokenbudget.compact."""

from __future__ import annotations

import pytest

from tokenbudget.compact import ELISION_MARKER, drop_oldest, head_tail
from tokenbudget.estimate import count_tokens, estimate_messages

LONG_TEXT = " ".join(f"sentence number {i} about token budgets" for i in range(200))


def message(content: str, role: str = "user") -> dict:
    return {"role": role, "content": content}


def test_head_tail_returns_text_unchanged_when_it_fits():
    assert head_tail(LONG_TEXT, count_tokens(LONG_TEXT)) == LONG_TEXT
    assert head_tail("short", count_tokens("short") + 100) == "short"


def test_head_tail_returns_empty_text_unchanged():
    assert head_tail("", 0) == ""
    assert head_tail("", 50) == ""


def test_head_tail_fits_budget_and_marks_the_elision():
    budget = count_tokens(LONG_TEXT) // 2
    result = head_tail(LONG_TEXT, budget)

    assert result != LONG_TEXT
    assert ELISION_MARKER in result
    assert count_tokens(result) <= budget


def test_head_tail_keeps_a_prefix_and_a_suffix_of_the_original():
    result = head_tail(LONG_TEXT, count_tokens(LONG_TEXT) // 2)
    head, tail = result.split(ELISION_MARKER)

    assert head and tail
    assert LONG_TEXT.startswith(head)
    assert LONG_TEXT.endswith(tail)
    assert len(head) > len(tail)


def test_head_tail_head_frac_extremes_drop_one_side():
    budget = count_tokens(LONG_TEXT) // 2

    all_head, no_tail = head_tail(LONG_TEXT, budget, head_frac=1.0).split(ELISION_MARKER)
    assert no_tail == ""
    assert LONG_TEXT.startswith(all_head)

    no_head, all_tail = head_tail(LONG_TEXT, budget, head_frac=0.0).split(ELISION_MARKER)
    assert no_head == ""
    assert LONG_TEXT.endswith(all_tail)


def test_head_tail_fits_a_budget_too_small_for_the_marker():
    for budget in (0, 1, 2):
        assert count_tokens(head_tail(LONG_TEXT, budget)) <= budget


def test_head_tail_is_deterministic():
    budget = count_tokens(LONG_TEXT) // 3
    assert head_tail(LONG_TEXT, budget) == head_tail(LONG_TEXT, budget)


def test_head_tail_rejects_bad_arguments():
    with pytest.raises(ValueError):
        head_tail(LONG_TEXT, -1)
    with pytest.raises(ValueError):
        head_tail(LONG_TEXT, 100, head_frac=1.5)
    with pytest.raises(ValueError):
        head_tail(LONG_TEXT, 100, head_frac=-0.1)


def test_drop_oldest_keeps_everything_when_it_fits():
    messages = [message("alpha"), message("beta"), message("gamma")]
    assert drop_oldest(messages, estimate_messages(messages)) == messages


def test_drop_oldest_removes_messages_from_the_front():
    messages = [message("alpha" * 20), message("beta" * 20), message("gamma" * 20)]
    result = drop_oldest(messages, estimate_messages(messages[1:]))

    assert result == messages[1:]
    assert estimate_messages(result) <= estimate_messages(messages[1:])


def test_drop_oldest_keeps_the_last_message_even_when_it_alone_is_too_large():
    messages = [message("alpha" * 50), message("omega" * 50)]
    result = drop_oldest(messages, 1)

    assert result == [messages[-1]]
    assert estimate_messages(result) > 1


def test_drop_oldest_does_not_mutate_its_input():
    messages = [message("alpha" * 20), message("beta" * 20), message("gamma" * 20)]
    before = list(messages)
    drop_oldest(messages, 1)

    assert messages == before
    assert drop_oldest(messages, 10_000) is not messages


def test_drop_oldest_handles_an_empty_history():
    assert drop_oldest([], 100) == []


def test_drop_oldest_rejects_a_negative_budget():
    with pytest.raises(ValueError):
        drop_oldest([message("alpha")], -1)
