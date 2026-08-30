"""Tests for :mod:`tokenbudget.compact`.

The expectations are phrased in terms of what ``tokenbudget.estimate``
actually reports rather than hard-coded token counts, so they hold for any
counter that satisfies the estimate contract.
"""

from __future__ import annotations

import pytest

from tokenbudget import estimate
from tokenbudget.compact import ELISION_MARKER, drop_oldest, head_tail

LONG_TEXT = " ".join(f"word{i:03d}" for i in range(400))


def split_result(result: str) -> tuple[str, str]:
    """Return the retained head and tail of an elided ``result``."""
    head, marker, tail = result.partition(ELISION_MARKER)
    assert marker == ELISION_MARKER, "an elided result must contain the marker"
    return head, tail


def test_head_tail_returns_short_text_unchanged() -> None:
    text = "a handful of words that easily fits"
    budget = estimate.count_tokens(text)
    assert head_tail(text, budget) == text
    assert head_tail(text, budget + 1000) == text
    assert ELISION_MARKER not in head_tail(text, budget)


def test_head_tail_on_empty_text_is_empty_for_any_budget() -> None:
    assert head_tail("", 0) == ""
    assert head_tail("", 50) == ""


def test_head_tail_result_fits_the_budget() -> None:
    for budget in (5, 10, 25, 50, 100, 200):
        result = head_tail(LONG_TEXT, budget)
        assert estimate.count_tokens(result) <= budget, budget
        assert len(result) < len(LONG_TEXT)


def test_head_tail_keeps_a_real_prefix_and_a_real_suffix() -> None:
    result = head_tail(LONG_TEXT, 60)
    head, tail = split_result(result)
    assert head, "the default head_frac must retain some head"
    assert tail, "the default head_frac must retain some tail"
    assert LONG_TEXT.startswith(head)
    assert LONG_TEXT.endswith(tail)


def test_head_tail_splits_retained_text_by_head_frac() -> None:
    for head_frac in (0.25, 0.5, 0.6, 0.9):
        head, tail = split_result(head_tail(LONG_TEXT, 80, head_frac=head_frac))
        kept = len(head) + len(tail)
        assert len(head) == int(kept * head_frac), head_frac


def test_head_tail_head_frac_extremes_keep_only_one_side() -> None:
    head, tail = split_result(head_tail(LONG_TEXT, 80, head_frac=1.0))
    assert tail == "" and LONG_TEXT.startswith(head) and head

    head, tail = split_result(head_tail(LONG_TEXT, 80, head_frac=0.0))
    assert head == "" and LONG_TEXT.endswith(tail) and tail


def test_head_tail_keeps_more_text_as_the_budget_grows() -> None:
    kept = [len(head_tail(LONG_TEXT, budget)) for budget in (10, 20, 40, 80, 160)]
    assert kept == sorted(kept)
    assert kept[0] < kept[-1]


def test_head_tail_returns_empty_when_even_the_marker_does_not_fit() -> None:
    assert head_tail(LONG_TEXT, 0) == ""


def test_head_tail_is_deterministic() -> None:
    assert head_tail(LONG_TEXT, 42) == head_tail(LONG_TEXT, 42)


def test_head_tail_rejects_a_negative_budget() -> None:
    with pytest.raises(ValueError):
        head_tail(LONG_TEXT, -1)


@pytest.mark.parametrize("head_frac", [-0.1, 1.1])
def test_head_tail_rejects_head_frac_outside_the_unit_interval(head_frac: float) -> None:
    with pytest.raises(ValueError):
        head_tail(LONG_TEXT, 50, head_frac=head_frac)


def messages(*contents: str) -> list[dict]:
    return [{"role": "user", "content": content} for content in contents]


def test_drop_oldest_on_an_empty_list_returns_an_empty_list() -> None:
    assert drop_oldest([], 0) == []
    assert drop_oldest([], 100) == []


def test_drop_oldest_keeps_everything_that_fits() -> None:
    chat = messages("first", "second", "third")
    result = drop_oldest(chat, estimate.estimate_messages(chat))
    assert result == chat
    assert result is not chat


def test_drop_oldest_removes_messages_from_the_front() -> None:
    chat = messages("oldest", "older", "newer", "newest")
    budget = estimate.estimate_messages(chat[-2:])
    result = drop_oldest(chat, budget)
    assert result == chat[-2:]
    assert estimate.estimate_messages(result) <= budget


def test_drop_oldest_always_keeps_the_last_message() -> None:
    chat = messages("small", LONG_TEXT)
    for budget in (0, 1, 5):
        assert drop_oldest(chat, budget) == chat[-1:]


def test_drop_oldest_keeps_the_last_message_even_alone_over_budget() -> None:
    chat = messages(LONG_TEXT)
    result = drop_oldest(chat, 0)
    assert result == chat
    assert estimate.estimate_messages(result) > 0


def test_drop_oldest_does_not_mutate_its_input() -> None:
    chat = messages("a", "b", "c")
    before = [dict(message) for message in chat]
    drop_oldest(chat, 0)
    assert chat == before
    assert len(chat) == 3


def test_drop_oldest_rejects_a_negative_budget() -> None:
    with pytest.raises(ValueError):
        drop_oldest(messages("a"), -5)
