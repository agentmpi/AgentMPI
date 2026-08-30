"""Compaction strategies that shrink text and message histories to a token budget.

Both strategies measure size with :mod:`tokenbudget.estimate` rather than with
character counts, so a caller always gets back something the estimator agrees
fits the budget it was given.
"""

from __future__ import annotations

from tokenbudget.estimate import count_tokens, estimate_messages

ELISION_MARKER = "\n...[elided]...\n"


def head_tail(text: str, budget: int, head_frac: float = 0.6) -> str:
    """Keep the start and end of ``text``, eliding the middle, to fit ``budget``.

    ``text`` is returned unchanged when it already fits. Otherwise ``head_frac``
    of the retained characters come from the front and the rest from the back,
    joined by :data:`ELISION_MARKER`. The result always fits ``budget`` as
    measured by :func:`tokenbudget.estimate.count_tokens`; when the budget is so
    small that not even the marker fits, the longest fitting prefix is returned
    instead, which may be the empty string.
    """
    if budget < 0:
        raise ValueError("budget must not be negative")
    if not 0.0 <= head_frac <= 1.0:
        raise ValueError("head_frac must be between 0.0 and 1.0")
    if not text:
        return text
    if count_tokens(text) <= budget:
        return text
    if count_tokens(ELISION_MARKER) > budget:
        return _longest_fitting_prefix(text, budget)

    # count_tokens is monotone non-decreasing in length and each extra retained
    # character lengthens the candidate, so the fitting keep counts form a
    # prefix of [0, len(text) - 1] and can be binary searched.
    best = _splice(text, 0, head_frac)
    low, high = 1, len(text) - 1
    while low <= high:
        middle = (low + high) // 2
        candidate = _splice(text, middle, head_frac)
        if count_tokens(candidate) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def drop_oldest(messages: list[dict], budget: int) -> list[dict]:
    """Drop messages from the front of ``messages`` until they fit ``budget``.

    The most recent message is always kept, even when it alone exceeds the
    budget. The input list is never mutated.
    """
    if budget < 0:
        raise ValueError("budget must not be negative")
    kept = list(messages)
    while len(kept) > 1 and estimate_messages(kept) > budget:
        kept.pop(0)
    return kept


def _splice(text: str, keep: int, head_frac: float) -> str:
    """Join a prefix and a suffix of ``text`` totalling ``keep`` characters."""
    head_len = min(keep, int(round(keep * head_frac)))
    tail_len = keep - head_len
    head = text[:head_len]
    tail = text[len(text) - tail_len:] if tail_len else ""
    return head + ELISION_MARKER + tail


def _longest_fitting_prefix(text: str, budget: int) -> str:
    """Return the longest prefix of ``text`` that fits ``budget``."""
    low, high, best = 0, len(text), 0
    while low <= high:
        middle = (low + high) // 2
        if count_tokens(text[:middle]) <= budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return text[:best]
