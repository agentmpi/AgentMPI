"""The single public entry point of minidb."""

from __future__ import annotations

from .engine import execute
from .errors import QueryError
from .parser import parse
from .planner import plan


def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute ``sql`` against ``tables`` and return the result rows.

    Every failure is reported as :class:`~minidb.errors.QueryError`; no other
    exception type escapes this function.
    """
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to rows")
    try:
        return execute(plan(parse(sql), tables), tables)
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("the query is nested too deeply") from exc
    except Exception as exc:  # never leak a non-QueryError to the caller
        raise QueryError(f"{type(exc).__name__}: {exc}") from exc
