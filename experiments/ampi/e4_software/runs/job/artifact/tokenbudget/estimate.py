"""Estimate how many tokens a piece of text or a chat transcript costs.

This is the leaf module of :mod:`tokenbudget`: everything else measures
"how big is this?" by calling in here, and this module imports nothing from
the rest of the package. The estimate is a cheap character-based
approximation rather than a real tokeniser, which keeps it dependency-free
and gives it the properties the rest of the package relies on:

``count_tokens`` is deterministic, never negative, and never shrinks when
text grows. Because the count is derived from ``len(text)`` alone, callers
such as :mod:`tokenbudget.compact` may bisect over the length of a string to
find the largest slice that fits a budget.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = [
    "CHARS_PER_TOKEN",
    "PER_MESSAGE_OVERHEAD",
    "count_tokens",
    "estimate_messages",
]

#: Average number of characters that make up one token.
CHARS_PER_TOKEN = 4

#: Tokens charged per message for role markers and separators.
PER_MESSAGE_OVERHEAD = 4


def count_tokens(text: str | None) -> int:
    """Return the estimated number of tokens in ``text``.

    Empty text and ``None`` cost nothing. Anything else costs
    ``ceil(len(text) / CHARS_PER_TOKEN)``, so a non-empty string always
    costs at least one token and the count is a non-decreasing function of
    ``len(text)``.

    Raises:
        TypeError: if ``text`` is neither a string nor ``None``.
    """
    if text is None:
        return 0
    if not isinstance(text, str):
        raise TypeError(f"text must be a str or None, got {type(text).__name__}")
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)


def estimate_messages(messages: Sequence[Mapping[str, object]]) -> int:
    """Return the estimated cost of a list of chat messages.

    Each message costs :func:`count_tokens` of its ``"content"`` field plus
    :data:`PER_MESSAGE_OVERHEAD` tokens of framing. A message with no
    ``"content"`` key, or one whose content is ``None`` or empty, costs the
    overhead alone. An empty message list costs nothing.

    Raises:
        TypeError: if a message is not a mapping, or if its content is
            neither a string nor ``None``.
    """
    total = 0
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise TypeError(f"message {index} must be a mapping, got {type(message).__name__}")
        content = message.get("content")
        if not isinstance(content, str) and content is not None:
            raise TypeError(
                f"content of message {index} must be a str or None, "
                f"got {type(content).__name__}"
            )
        total += count_tokens(content) + PER_MESSAGE_OVERHEAD
    return total
