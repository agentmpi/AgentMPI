"""Strategies for shrinking text and message history to fit a budget.

Both strategies are lossy but predictable: :func:`head_tail` keeps the start
and the end of a document and elides the middle, and :func:`drop_oldest`
forgets the oldest turns of a conversation. Everything is measured with
:mod:`tokenbudget.estimate`, so a compacted result is guaranteed to fit the
same budget the caller checked against :mod:`tokenbudget.policy`.
"""

from .estimate import count_tokens, estimate_messages

ELISION_MARKER = "\n...[elided]...\n"
"""Inserted in place of the removed middle of a document."""


def _prefix_len(text: str, budget: int) -> int:
    """Return the length of the longest prefix of ``text`` fitting ``budget``."""
    if budget <= 0:
        return 0
    if count_tokens(text) <= budget:
        return len(text)
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return low


def _suffix_len(text: str, budget: int) -> int:
    """Return the length of the longest suffix of ``text`` fitting ``budget``."""
    if budget <= 0:
        return 0
    if count_tokens(text) <= budget:
        return len(text)
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[len(text) - mid:]) <= budget:
            low = mid
        else:
            high = mid - 1
    return low


def head_tail(text: str, budget: int, head_frac: float = 0.6) -> str:
    """Return ``text`` with its middle elided so that it fits ``budget``.

    ``text`` comes back unchanged when it already fits. Otherwise the result
    is a prefix, :data:`ELISION_MARKER`, and a suffix, where ``head_frac`` of
    the available budget is spent on the prefix. The result always fits
    ``budget`` as measured by :func:`tokenbudget.estimate.count_tokens`.
    """
    if not 0.0 <= head_frac <= 1.0:
        raise ValueError(f"head_frac must be between 0 and 1, got {head_frac}")
    if not text or count_tokens(text) <= budget:
        return text

    body_budget = budget - count_tokens(ELISION_MARKER)
    if body_budget <= 0:
        return text[: _prefix_len(text, budget)]

    head_budget = int(body_budget * head_frac)
    head_len = _prefix_len(text, head_budget)
    tail_len = _suffix_len(text, body_budget - head_budget)
    if head_len + tail_len > len(text):
        tail_len = len(text) - head_len

    result = text[:head_len] + ELISION_MARKER + text[len(text) - tail_len:]
    if count_tokens(result) > budget:
        return text[: _prefix_len(text, budget)]
    return result


def drop_oldest(messages: list[dict], budget: int) -> list[dict]:
    """Return ``messages`` with leading entries dropped until they fit ``budget``.

    The most recent message is always kept, even when it alone is over
    budget, because a conversation with nothing in it is useless.
    """
    kept = list(messages)
    while len(kept) > 1 and estimate_messages(kept) > budget:
        kept.pop(0)
    return kept
