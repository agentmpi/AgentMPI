"""Public entry point of the `minidb` SQL query engine.

This module is a thin orchestrator: it validates its two arguments, drives the
parse -> plan -> execute pipeline, and guarantees that `QueryError` is the only
exception type that can leave the public API.
"""

from typing import Any

from .engine import execute
from .errors import QueryError
from .parser import parse
from .planner import plan

__all__ = ["query"]


def _check_arguments(sql: object, tables: object) -> None:
    """Reject argument shapes the downstream modules are not required to handle."""
    if not isinstance(sql, str):
        raise QueryError(
            "sql must be a string, not %s" % type(sql).__name__
        )
    if not isinstance(tables, dict):
        raise QueryError(
            "tables must be a dict of table name to list of rows, not %s"
            % type(tables).__name__
        )
    for name, rows in tables.items():
        if not isinstance(name, str):
            raise QueryError(
                "table name must be a string, not %s" % type(name).__name__
            )
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


def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute `sql` against `tables` and return the result rows."""
    _check_arguments(sql, tables)
    try:
        statement: Any = parse(sql)
        prepared: Any = plan(statement, tables)
        return execute(prepared, tables)
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is too deeply nested to evaluate") from exc
    except Exception as exc:
        raise QueryError(
            "failed to execute query: %s: %s" % (type(exc).__name__, exc)
        ) from exc
