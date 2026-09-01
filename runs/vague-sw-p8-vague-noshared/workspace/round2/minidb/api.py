"""Public entry point of the `minidb` SQL query engine.

This module is a thin orchestrator: it validates its two arguments, drives the
tokenize -> parse -> plan -> execute pipeline across the sibling modules, and
guarantees that `QueryError` is the only exception type that can leave the
public API. All query behaviour belongs to those siblings, not here.
"""

import inspect
from typing import Any, Callable

from . import engine, parser, planner, tokens
from .errors import QueryError

__all__ = ["query"]


def _entry(module: Any, name: str) -> Callable[..., Any]:
    """The published entry point `name` of a sibling module."""
    entry = getattr(module, name, None)
    if not callable(entry):
        raise QueryError("%s does not provide %s()" % (module.__name__, name))
    return entry


def _parser_wants_tokens(parse: Callable[..., Any]) -> bool:
    """True when the parser's signature asks for a token stream, not raw SQL."""
    try:
        parameters = list(inspect.signature(parse).parameters.values())
    except (TypeError, ValueError):
        return True
    if not parameters:
        return True
    first = parameters[0]
    described = first.name
    if first.annotation is not inspect.Parameter.empty:
        described = "%s %s" % (described, first.annotation)
    described = described.lower()
    if "token" in described or "stream" in described:
        return True
    if "sql" in described or "str" in described or "text" in described or "source" in described:
        return False
    return True


def _check_arguments(sql: object, tables: object) -> None:
    """Reject the two argument shapes no sibling module can work with."""
    if not isinstance(sql, str):
        raise QueryError("sql must be a string, not %s" % type(sql).__name__)
    if not isinstance(tables, dict):
        raise QueryError(
            "tables must be a dict of table name to list of rows, not %s"
            % type(tables).__name__
        )


def _parse(sql: str) -> Any:
    """Tokenize and parse `sql` into the syntax tree the planner consumes."""
    tokenize = _entry(tokens, "tokenize")
    parse = _entry(parser, "parse")
    stream = tokenize(sql)
    if _parser_wants_tokens(parse):
        preferred, alternative = stream, sql
    else:
        preferred, alternative = sql, stream
    try:
        return parse(preferred)
    except TypeError:
        # The parser takes the other end of the token boundary than its
        # signature suggested; a mismatch there is not the query's fault.
        return parse(alternative)


def _plan(statement: Any, tables: dict[str, list[dict]]) -> Any:
    """Resolve and validate the parsed statement against `tables`."""
    prepared = _entry(planner, "plan")(statement, tables)
    if prepared is None:
        raise QueryError("the planner produced no plan for this query")
    return prepared


def _execute(prepared: Any, tables: dict[str, list[dict]]) -> Any:
    return _entry(engine, "execute")(prepared, tables)


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
    try:
        _check_arguments(sql, tables)
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
