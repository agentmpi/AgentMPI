"""The single error type of the `minidb` system.

Every other module reports a malformed query, an unknown table or column, an
ambiguous column, a misused aggregate, a negative ``LIMIT``/``OFFSET`` or any
value or type error met while planning or evaluating a query by raising
:class:`QueryError`.  It is the only exception type that escapes the public
API, so callers can rely on ``except QueryError`` alone.

This module deliberately imports nothing, not even from the standard library,
so it can never take part in an import cycle.
"""

__all__ = ["QueryError"]


class QueryError(Exception):
    """Raised for any query that minidb cannot parse, plan or evaluate.

    Construction follows the inherited ``Exception`` behaviour: pass exactly
    one positional argument, a short human-readable message, so that
    ``str(exc)`` is that message and ``exc.args == (message,)``.  The class
    adds no attributes and no methods of its own, and has no subclasses: code
    that needs to distinguish failure kinds must do so through its own control
    flow rather than through the exception type.  Message wording is not part
    of the contract; match on the type, never on the text.

    It derives directly from ``Exception`` rather than from ``ValueError``,
    ``LookupError`` or ``TypeError``, so catching it cannot swallow a genuine
    bug, and a ``KeyError``/``IndexError``/``AttributeError``/``TypeError``
    handler elsewhere will never catch it by accident.

    The one-argument convention is a convention, not a check: the constructor
    is ``Exception``'s, unchanged, so ``QueryError()`` and
    ``QueryError(msg, pos)`` are also legal.  Read the message with
    ``str(exc)``, which is safe for any argument count, never with
    ``exc.args[0]``, which would raise ``IndexError`` for an argument-less
    instance and so let a non-``QueryError`` escape the public API.
    """
