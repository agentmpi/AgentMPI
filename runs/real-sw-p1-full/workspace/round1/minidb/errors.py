"""Error types for minidb.

This module holds the single public exception of the package. It depends on
nothing else, so every other module may import it without creating a cycle.
"""

__all__ = ["QueryError"]


class QueryError(Exception):
    """Raised for any query that minidb cannot execute.

    This covers malformed SQL, unknown tables or columns, ambiguous column
    references, mixed-type comparisons, negative LIMIT/OFFSET values and type
    errors. It is the only exception the public API is allowed to raise, so
    callers branch on the exception type rather than on the message text.
    """
