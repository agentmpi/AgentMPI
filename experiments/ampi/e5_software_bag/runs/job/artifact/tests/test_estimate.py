"""Unit tests for tokenbudget.estimate."""

from tokenbudget.estimate import PER_MESSAGE_OVERHEAD, count_tokens, estimate_messages


def test_empty_and_none_cost_nothing() -> None:
    assert count_tokens("") == 0
    assert count_tokens(None) == 0


def test_count_is_deterministic_and_non_negative() -> None:
    text = "the supervisor counts every single token it spends"
    assert count_tokens(text) == count_tokens(text)
    assert count_tokens(text) >= 0


def test_count_is_monotone_in_length() -> None:
    text = "a" * 200
    counts = [count_tokens(text[:i]) for i in range(len(text) + 1)]
    assert counts == sorted(counts)


def test_estimate_messages_adds_per_message_overhead() -> None:
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    expected = count_tokens("hello") + count_tokens("hi") + 2 * PER_MESSAGE_OVERHEAD
    assert estimate_messages(messages) == expected


def test_estimate_messages_of_nothing_is_zero() -> None:
    assert estimate_messages([]) == 0


def test_message_without_content_still_costs_overhead() -> None:
    assert estimate_messages([{"role": "user"}]) == PER_MESSAGE_OVERHEAD
