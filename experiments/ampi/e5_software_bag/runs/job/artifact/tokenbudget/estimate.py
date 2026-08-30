"""Token counting for raw text and for chat-style message lists.

This is the leaf module of the package: it imports nothing from
``tokenbudget``, so every other module is free to depend on it.

The estimator is a character-length heuristic rather than a real BPE
tokenizer. That keeps the package dependency-free and, more importantly,
makes the count monotone non-decreasing in ``len(text)``, which the
compaction code relies on when it binary-searches for the longest prefix
that still fits a budget.
"""

import math

CHARS_PER_TOKEN = 4
"""Average characters per token assumed by :func:`count_tokens`."""

PER_MESSAGE_OVERHEAD = 4
"""Tokens charged per chat message for its role and framing."""


def count_tokens(text: str) -> int:
    """Return the estimated number of tokens in ``text``.

    Returns 0 for an empty string or ``None``. The result is deterministic,
    never negative, and never decreases as ``len(text)`` grows.
    """
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_messages(messages: list[dict]) -> int:
    """Return the estimated token cost of a list of chat messages.

    Each message costs :func:`count_tokens` of its ``content`` plus a flat
    :data:`PER_MESSAGE_OVERHEAD`.
    """
    if not messages:
        return 0
    return sum(
        count_tokens(message.get("content")) + PER_MESSAGE_OVERHEAD
        for message in messages
    )
