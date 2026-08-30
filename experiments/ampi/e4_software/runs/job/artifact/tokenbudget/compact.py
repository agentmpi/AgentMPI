"""Shrink oversized prompt material so that it fits inside a token budget.

Two complementary strategies live here:

``head_tail``
    Compact a single blob of text by keeping a prefix and a suffix and
    replacing everything in between with an elision marker.
``drop_oldest``
    Compact a chat-style message list by discarding the oldest messages,
    which are usually the least relevant ones.

Both are measured with :mod:`tokenbudget.estimate`, so "fits the budget"
always means the same thing here as it does everywhere else in the package.
"""

from __future__ import annotations

from tokenbudget import estimate

__all__ = ["ELISION_MARKER", "head_tail", "drop_oldest"]

#: Separator placed between the retained head and the retained tail.
ELISION_MARKER = "\n...[elided]...\n"


def head_tail(text: str, budget: int, head_frac: float = 0.6) -> str:
    """Return ``text`` shortened to at most ``budget`` tokens.

    Text that already fits is returned unchanged. Otherwise the result is a
    prefix of ``text``, then :data:`ELISION_MARKER`, then a suffix of
    ``text``; ``head_frac`` of the retained characters go to the prefix and
    the rest to the suffix. The amount retained is the largest that still
    fits ``budget`` as measured by :func:`tokenbudget.estimate.count_tokens`.

    If the marker alone is larger than ``budget`` there is nothing that both
    elides and fits, and the empty string is returned.

    Raises:
        ValueError: if ``budget`` is negative or ``head_frac`` is outside
            the closed interval ``[0.0, 1.0]``.
    """
    if budget < 0:
        raise ValueError(f"budget must be non-negative, got {budget!r}")
    if not 0.0 <= head_frac <= 1.0:
        raise ValueError(f"head_frac must be within [0.0, 1.0], got {head_frac!r}")

    if not text:
        return text
    if estimate.count_tokens(text) <= budget:
        return text

    def elide(keep: int) -> str:
        head_len = int(keep * head_frac)
        tail_len = keep - head_len
        tail = text[len(text) - tail_len :] if tail_len else ""
        return text[:head_len] + ELISION_MARKER + tail

    if estimate.count_tokens(ELISION_MARKER) > budget:
        return ""

    # count_tokens is monotone in len(text) and len(elide(keep)) grows with
    # keep, so the set of fitting keeps is a prefix and can be bisected.
    low, high = 0, len(text) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if estimate.count_tokens(elide(middle)) <= budget:
            low = middle
        else:
            high = middle - 1
    return elide(low)


def drop_oldest(messages: list[dict], budget: int) -> list[dict]:
    """Return the newest run of ``messages`` that fits ``budget``.

    Messages are discarded from the front until
    :func:`tokenbudget.estimate.estimate_messages` reports that the remainder
    fits. The most recent message is never dropped, so a single message that
    exceeds ``budget`` on its own is still returned.

    The input list is not modified; the result is a new list holding the same
    message objects.

    Raises:
        ValueError: if ``budget`` is negative.
    """
    if budget < 0:
        raise ValueError(f"budget must be non-negative, got {budget!r}")
    if not messages:
        return []

    for start in range(len(messages)):
        kept = messages[start:]
        if estimate.estimate_messages(kept) <= budget:
            return list(kept)
    return [messages[-1]]
