"""Strategies for shrinking text and message history to fit a budget.

Skeleton placeholder: the owning implementer replaces this whole file.
"""


def head_tail(text: str, budget: int, head_frac: float = 0.6) -> str:
    """Return ``text`` elided in the middle so that it fits ``budget``."""
    raise NotImplementedError("compact.head_tail is not implemented yet")


def drop_oldest(messages: list[dict], budget: int) -> list[dict]:
    """Return ``messages`` with leading entries dropped until they fit ``budget``."""
    raise NotImplementedError("compact.drop_oldest is not implemented yet")
