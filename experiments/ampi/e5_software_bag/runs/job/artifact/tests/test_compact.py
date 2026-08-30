"""Unit tests for tokenbudget.compact."""

import pytest

from tokenbudget.compact import ELISION_MARKER, drop_oldest, head_tail
from tokenbudget.estimate import count_tokens, estimate_messages

LONG = "the agent plans its next move and the supervisor counts tokens. " * 30


def test_text_that_already_fits_comes_back_unchanged() -> None:
    assert head_tail("short enough", 1000) == "short enough"


def test_compacted_text_fits_the_budget() -> None:
    for budget in (0, 1, 5, 40, 120, 400):
        assert count_tokens(head_tail(LONG, budget)) <= budget


def test_compacted_text_keeps_a_head_and_a_tail() -> None:
    trimmed = head_tail(LONG, 120)
    assert ELISION_MARKER in trimmed
    head, tail = trimmed.split(ELISION_MARKER)
    assert LONG.startswith(head)
    assert LONG.endswith(tail)


def test_head_frac_shifts_the_split() -> None:
    mostly_head = head_tail(LONG, 200, head_frac=0.9).split(ELISION_MARKER)[0]
    mostly_tail = head_tail(LONG, 200, head_frac=0.1).split(ELISION_MARKER)[0]
    assert len(mostly_head) > len(mostly_tail)


def test_invalid_head_frac_raises() -> None:
    with pytest.raises(ValueError):
        head_tail(LONG, 100, head_frac=1.5)


def test_drop_oldest_keeps_a_suffix_of_the_conversation() -> None:
    messages = [{"role": "user", "content": "turn %d " % i * 20} for i in range(6)]
    kept = drop_oldest(messages, 60)
    assert kept == messages[len(messages) - len(kept):]
    assert estimate_messages(kept) <= 60


def test_drop_oldest_always_keeps_the_last_message() -> None:
    messages = [{"role": "user", "content": "x" * 4000}, {"role": "user", "content": "y" * 4000}]
    kept = drop_oldest(messages, 1)
    assert kept == messages[-1:]


def test_drop_oldest_leaves_a_fitting_conversation_alone() -> None:
    messages = [{"role": "user", "content": "hi"}]
    assert drop_oldest(messages, 1000) == messages


def test_drop_oldest_does_not_mutate_its_input() -> None:
    messages = [{"role": "user", "content": "a" * 400}, {"role": "user", "content": "b"}]
    drop_oldest(messages, 1)
    assert len(messages) == 2
