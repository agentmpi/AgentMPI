"""Token counting for raw text and for chat-style message lists.

Skeleton placeholder: the owning implementer replaces this whole file.
"""


def count_tokens(text: str) -> int:
    """Return the number of tokens in ``text``."""
    raise NotImplementedError("estimate.count_tokens is not implemented yet")


def estimate_messages(messages: list[dict]) -> int:
    """Return the token cost of ``messages``, including per-message overhead."""
    raise NotImplementedError("estimate.estimate_messages is not implemented yet")
