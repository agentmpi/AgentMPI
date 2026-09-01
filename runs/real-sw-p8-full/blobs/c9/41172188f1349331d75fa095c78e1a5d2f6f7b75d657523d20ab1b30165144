You own `minidb/tokens.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/errors.py (published exports: ["QueryError"])
```python
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

```

### minidb/api.py (published exports: ["query"])
```python
"""Public entry point of the `minidb` query engine.

`query` is the only public name here; `minidb/__init__.py` re-exports it together
with `QueryError` from `minidb.errors`.
"""

from __future__ import annotations

from .engine import execute
from .errors import QueryError
from .parser import parse
from .planner import plan

__all__ = ["query"]


def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute `sql` against `tables` and return the result rows."""
    _validate_arguments(sql, tables)
    try:
        return execute(plan(parse(sql), tables), tables)
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is nested too deeply to evaluate") from exc
    except Exception as exc:
        raise QueryError(f"internal error while executing query: {type(exc).__name__}: {exc}") from exc


def _validate_arguments(sql: object, tables: object) -> None:
    """Reject argument shapes that would otherwise surface as TypeError."""
    if not isinstance(sql, str):
        raise QueryError(f"sql must be a string, not {type(sql).__name__}")
    if not isinstance(tables, dict):
        raise QueryError(f"tables must be a dict of table name to rows, not {type(tables).__name__}")
    for name, rows in tables.items():
        if not isinstance(name, str):
            raise QueryError(f"table name must be a string, not {type(name).__name__}")
        if not isinstance(rows, list):
            raise QueryError(f"table {name!r} must be a list of rows, not {type(rows).__name__}")
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                raise QueryError(
                    f"row {position} of table {name!r} must be a dict, not {type(row).__name__}"
                )

```