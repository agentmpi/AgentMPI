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
    """

```

### minidb/api.py (published exports: ["query"])
```python
"""Public entry point of the `minidb` SQL query engine.

This module is a thin orchestrator: it validates its two arguments, drives the
parse -> plan -> execute pipeline across the sibling modules, and guarantees
that `QueryError` is the only exception type that can leave the public API.

No interface was published for the sibling modules by the time this file was
written, so the pipeline stage of each one is located by name at call time
rather than by hard-coding a signature that a sibling never agreed to.
"""

import inspect
from typing import Any, Callable, Sequence

from . import engine, parser, planner, tokens
from .errors import QueryError

__all__ = ["query"]

_TOKENIZER_NAMES = ("tokenize", "tokenise", "tokenize_sql", "lex", "scan", "tokens")
_PARSER_NAMES = ("parse", "parse_sql", "parse_query", "parse_statement", "parse_select")
_PLANNER_NAMES = (
    "plan",
    "plan_query",
    "build_plan",
    "make_plan",
    "create_plan",
    "analyze",
    "analyse",
    "resolve",
    "validate",
    "prepare",
)
_ENGINE_NAMES = (
    "execute",
    "execute_plan",
    "execute_query",
    "run",
    "run_plan",
    "evaluate",
    "evaluate_plan",
)


def _entry_point(module: Any, names: Sequence[str], hints: Sequence[str]) -> Callable[..., Any] | None:
    """Find the callable a sibling module offers for one pipeline stage."""
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate) and not isinstance(candidate, type):
            return candidate
    for name in sorted(vars(module)):
        if name.startswith("_"):
            continue
        candidate = getattr(module, name)
        if not callable(candidate) or isinstance(candidate, type):
            continue
        if getattr(candidate, "__module__", None) != module.__name__:
            continue
        lowered = name.lower()
        if any(hint in lowered for hint in hints):
            return candidate
    return None


def _arity(func: Callable[..., Any]) -> tuple[int, int | None]:
    """Return how many positional arguments `func` requires and accepts."""
    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return (0, None)
    minimum = 0
    maximum: int | None = 0
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            maximum = None
            continue
        if parameter.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if maximum is not None:
            maximum += 1
        if parameter.default is inspect.Parameter.empty:
            minimum += 1
    return (minimum, maximum)


def _invoke(func: Callable[..., Any], options: Sequence[tuple[Any, ...]]) -> Any:
    """Call `func` with the first argument tuple its signature can accept."""
    minimum, maximum = _arity(func)
    for args in options:
        if len(args) >= minimum and (maximum is None or len(args) <= maximum):
            return func(*args)
    return func(*options[0])


def _check_arguments(sql: object, tables: object) -> None:
    """Reject argument shapes the downstream modules are not required to handle."""
    if not isinstance(sql, str):
        raise QueryError("sql must be a string, not %s" % type(sql).__name__)
    if not isinstance(tables, dict):
        raise QueryError(
            "tables must be a dict of table name to list of rows, not %s"
            % type(tables).__name__
        )
    for name, rows in tables.items():
        if not isinstance(name, str):
            raise QueryError("table name must be a string, not %s" % type(name).__name__)
        if not isinstance(rows, list):
            raise QueryError(
                "table %r must be a list of rows, not %s" % (name, type(rows).__name__)
            )
        for row in rows:
            if not isinstance(row, dict):
                raise QueryError(
                    "every row of table %r must be a dict, not %s"
                    % (name, type(row).__name__)
                )
        # A table's column order is the key order of its first row, so that row
        # is the one whose keys have to be usable as output column names.
        if rows and not all(isinstance(column, str) for column in rows[0]):
            raise QueryError("column names of table %r must be strings" % (name,))


def _tokenize(sql: str) -> Any:
    """Tokenize `sql`, or return None when the token stage cannot be used."""
    entry = _entry_point(tokens, _TOKENIZER_NAMES, ("token", "lex", "scan"))
    if entry is None:
        return None
    try:
        return _invoke(entry, [(sql,)])
    except Exception:
        return None


def _parse(sql: str) -> Any:
    entry = _entry_point(parser, _PARSER_NAMES, ("parse",))
    if entry is None:
        raise QueryError("minidb.parser exposes no parse entry point")
    try:
        return _invoke(entry, [(sql,)])
    except QueryError:
        raise
    except Exception:
        # The parser may take an already tokenized stream instead of raw text.
        stream = _tokenize(sql)
        if stream is None:
            raise
        return _invoke(entry, [(stream,)])


def _plan(statement: Any, tables: dict[str, list[dict]]) -> Any:
    entry = _entry_point(planner, _PLANNER_NAMES, ("plan", "resolve", "valid"))
    if entry is None:
        return statement
    prepared = _invoke(entry, [(statement, tables), (statement,)])
    # A planner that only validates in place returns nothing useful.
    return statement if prepared is None else prepared


def _execute(prepared: Any, tables: dict[str, list[dict]]) -> Any:
    entry = _entry_point(engine, _ENGINE_NAMES, ("exec", "run", "eval"))
    if entry is None:
        raise QueryError("minidb.engine exposes no execute entry point")
    return _invoke(entry, [(prepared, tables), (prepared,)])


def _as_rows(rows: Any) -> list[dict]:
    """Materialize the engine's result without changing its content or order."""
    if isinstance(rows, list):
        result = rows
    elif isinstance(rows, (dict, str, bytes)):
        raise QueryError(
            "query result must be a list of rows, not %s" % type(rows).__name__
        )
    else:
        try:
            result = list(rows)
        except TypeError as exc:
            raise QueryError(
                "query result must be a list of rows, not %s" % type(rows).__name__
            ) from exc
    for row in result:
        if not isinstance(row, dict):
            raise QueryError(
                "every result row must be a dict, not %s" % type(row).__name__
            )
    return result


def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute `sql` against `tables` and return the result rows."""
    _check_arguments(sql, tables)
    try:
        statement = _parse(sql)
        prepared = _plan(statement, tables)
        return _as_rows(_execute(prepared, tables))
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is too deeply nested to evaluate") from exc
    except Exception as exc:
        raise QueryError(
            "failed to execute query: %s: %s" % (type(exc).__name__, exc)
        ) from exc

```