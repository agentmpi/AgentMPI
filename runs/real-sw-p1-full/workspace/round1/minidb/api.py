"""Public entry point of the `minidb` query engine."""

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
        raise QueryError(
            f"internal error while executing query: {type(exc).__name__}: {exc}"
        ) from exc


def _validate_arguments(sql: object, tables: object) -> None:
    """Reject argument shapes that would otherwise surface as TypeError."""
    if not isinstance(sql, str):
        raise QueryError(f"sql must be a string, not {type(sql).__name__}")
    if not isinstance(tables, dict):
        raise QueryError(
            f"tables must be a dict of table name to rows, not {type(tables).__name__}"
        )
    for name, rows in tables.items():
        if not isinstance(name, str):
            raise QueryError(f"table name must be a string, not {type(name).__name__}")
        if not isinstance(rows, list):
            raise QueryError(
                f"table {name!r} must be a list of rows, not {type(rows).__name__}"
            )
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                raise QueryError(
                    f"row {position} of table {name!r} must be a dict, not {type(row).__name__}"
                )
