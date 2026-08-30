"""Cheap, dependency-free token estimation.

This is the leaf module of :mod:`tokenbudget`: it imports nothing from the rest
of the package, so every other module may depend on it. It answers the only
question the budgeting machinery needs, "roughly how many tokens is this?",
without pulling in a real tokenizer.

The estimate is a function of the text *length* alone. That is a deliberate
restriction rather than a shortcut: callers rely on the count being monotone
non-decreasing in ``len(text)``, and any estimator that also looked at the
characters themselves could rank a longer string below a shorter one.
"""

from __future__ import annotations

__all__ = ["CHARS_PER_TOKEN", "PER_MESSAGE_OVERHEAD", "count_tokens", "estimate_messages"]

CHARS_PER_TOKEN = 4
"""Average characters per token; the usual rule of thumb for English prose."""

PER_MESSAGE_OVERHEAD = 4
"""Tokens charged per chat message for its role and framing."""


def count_tokens(text: str | None) -> int:
    """Estimate the number of tokens in ``text``.

    Empty strings and ``None`` cost nothing. Otherwise the cost is
    ``ceil(len(text) / CHARS_PER_TOKEN)``, which is deterministic, never
    negative, and never decreases as the text gets longer.
    """
    if text is None:
        return 0
    if not isinstance(text, str):
        raise TypeError(f"count_tokens() expected str or None, got {type(text).__name__}")
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)


def estimate_messages(messages: list[dict]) -> int:
    """Estimate the number of tokens in a list of chat messages.

    Each message costs :data:`PER_MESSAGE_OVERHEAD` tokens plus the cost of its
    ``"content"`` field. A missing or ``None`` content counts as empty, and a
    content that is not a string is stringified so that callers holding
    numbers or ``None``-ish placeholders still get an estimate.
    """
    total = 0
    for index, message in enumerate(messages):
        if not hasattr(message, "get"):
            raise TypeError(f"messages[{index}] is not a mapping: {type(message).__name__}")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)
        total += PER_MESSAGE_OVERHEAD + count_tokens(content)
    return total
