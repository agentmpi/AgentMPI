"""Error type shared by every ``minidb`` module."""


class QueryError(Exception):
    """Raised for any malformed query, unknown name, or type error.

    This is the only exception type that escapes the public ``minidb`` API.
    """
