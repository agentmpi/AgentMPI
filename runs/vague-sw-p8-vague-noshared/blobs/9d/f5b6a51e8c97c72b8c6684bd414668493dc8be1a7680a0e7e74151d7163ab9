You own `minidb/errors.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/engine.py (published exports: ["Row", "Tables", "execute"])
```python
"""Evaluation of a validated minidb query plan against in-memory tables.

No interface was published for `planner` or `functions`, so this module invents no
signature for either of them: it imports only `QueryError`, reads the plan through
structural access (attribute or mapping lookup of the names the specification's own
vocabulary implies), and implements value semantics -- comparison, ordering, LIKE,
the scalar and aggregate functions -- for itself.
"""

from __future__ import annotations

import re
from functools import cmp_to_key
from typing import Any, TypeAlias

from .errors import QueryError

__all__ = ["Row", "Tables", "execute"]

Row: TypeAlias = dict[str, Any]
Tables: TypeAlias = dict[str, list[Row]]

_JoinedRow: TypeAlias = dict[str, Row]

_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})
_AGGREGATE_NAMES = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
_SCALAR_NAMES = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

_KIND_BY_CLASS = {
    "literal": "literal",
    "const": "literal",
    "constant": "literal",
    "number": "literal",
    "string": "literal",
    "null": "literal",
    "column": "column",
    "columnref": "column",
    "col": "column",
    "field": "column",
    "fieldref": "column",
    "identifier": "column",
    "unary": "unary",
    "unaryop": "unary",
    "unaryexpr": "unary",
    "binary": "binary",
    "binaryop": "binary",
    "binop": "binary",
    "binaryexpr": "binary",
    "comparison": "binary",
    "func": "func",
    "function": "func",
    "funccall": "func",
    "functioncall": "func",
    "call": "func",
    "scalar": "func",
    "scalarcall": "func",
    "scalarfunc": "func",
    "isnull": "isnull",
    "nulltest": "isnull",
    "inlist": "inlist",
    "in": "inlist",
    "intest": "inlist",
    "inexpr": "inlist",
    "like": "like",
    "likeexpr": "like",
    "aggref": "aggref",
    "aggregateref": "aggref",
    "aggslot": "aggref",
    "aggregateslot": "aggref",
    "agg": "aggregate",
    "aggcall": "aggregate",
    "aggregate": "aggregate",
    "aggregatecall": "aggregate",
    "aggregatefunc": "aggregate",
    "aggregation": "aggregate",
}


class _Scope:
    """The evaluation context for one output row."""

    __slots__ = ("row", "members", "agg_values")

    def __init__(
        self,
        row: _JoinedRow,
        members: list[_JoinedRow] | None = None,
        agg_values: list[Any] | None = None,
    ) -> None:
        self.row = row
        self.members = members
        self.agg_values = agg_values


def execute(plan: Any, tables: Tables) -> list[Row]:
    """Evaluate `plan` against `tables` and return the result rows."""
    try:
        return _execute(plan, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"query evaluation failed: {exc}") from exc


def _execute(plan: Any, tables: Tables) -> list[Row]:
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to list of rows")

    select = tuple(_pick(plan, ("select", "select_items", "projections", "outputs"), ()) or ())
    if not select:
        raise QueryError("query has no output columns")

    rows = _scan(_sources(plan), tables)

    where = _pick(plan, ("where", "where_clause", "filter"))
    if where is not None:
        rows = [row for row in rows if _truth(_eval(where, _Scope(row)))]

    group_by = tuple(_pick(plan, ("group_by", "group", "groups", "grouping"), ()) or ())
    having = _pick(plan, ("having", "having_clause"))
    order_by = tuple(_pick(plan, ("order_by", "order", "ordering"), ()) or ())
    aggregates = tuple(_pick(plan, ("aggregates", "agg_calls", "aggregate_calls"), ()) or ())

    grouped = bool(group_by) or bool(aggregates) or _uses_aggregate(select, having, order_by)
    if grouped:
        entries = _grouped_entries(plan, rows, select, group_by, having, order_by, aggregates)
    else:
        entries = [
            (_project(select, _Scope(row)), _sort_keys(order_by, _Scope(row)))
            for row in rows
        ]

    if _pick(plan, ("distinct", "is_distinct"), False):
        entries = _distinct(entries)
    if order_by:
        entries = _sort(order_by, entries)

    return _slice(plan, [row for row, _keys in entries])


def _pick(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    """Read the first of `names` that `obj` provides, as attribute or mapping key."""
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _sources(plan: Any) -> list[tuple[str, str, Any]]:
    """Return the FROM/JOIN tables as (table name, alias, ON condition) in order."""
    refs = list(_pick(plan, ("from_tables", "tables", "sources", "from_", "from"), ()) or ())
    if not refs:
        base = _pick(plan, ("from_table", "base_table", "table"))
        if base is not None:
            refs = [base]
    if not refs:
        raise QueryError("query has no FROM table")

    conditions = list(_pick(plan, ("join_conditions", "on_conditions", "join_on"), ()) or ())
    sources: list[tuple[str, str, Any]] = []
    for position, ref in enumerate(refs):
        name, alias = _table_name_alias(ref)
        condition = None
        if position and position - 1 < len(conditions):
            condition = conditions[position - 1]
        sources.append((name, alias, condition))

    for entry in list(_pick(plan, ("joins", "join_list"), ()) or ()):
        table = _pick(entry, ("table", "ref", "target", "right"))
        if table is None:
            continue
        name, alias = _table_name_alias(table)
        if any(alias == known for _name, known, _cond in sources):
            continue
        condition = _pick(entry, ("condition", "on", "on_condition", "predicate"))
        sources.append((name, alias, condition))

    return sources


def _table_name_alias(ref: Any) -> tuple[str, str]:
    if isinstance(ref, str):
        return ref, ref
    if isinstance(ref, (tuple, list)) and ref:
        name = ref[0]
        alias = ref[1] if len(ref) > 1 and ref[1] else name
        return _as_name(name), _as_name(alias)
    name = _pick(ref, ("name", "table", "table_name"))
    alias = _pick(ref, ("alias", "as_name"))
    if name is None:
        raise QueryError("FROM entry has no table name")
    return _as_name(name), _as_name(alias if alias else name)


def _as_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise QueryError("table name and alias must be non-empty strings")
    return value


def _scan(sources: list[tuple[str, str, Any]], tables: Tables) -> list[_JoinedRow]:
    first_name, first_alias, _condition = sources[0]
    joined: list[_JoinedRow] = [{first_alias: source} for source in _table_rows(first_name, tables)]

    for name, alias, condition in sources[1:]:
        right = _table_rows(name, tables)
        combined: list[_JoinedRow] = []
        for left in joined:
            for source in right:
                candidate = dict(left)
                candidate[alias] = source
                if condition is None or _truth(_eval(condition, _Scope(candidate))):
                    combined.append(candidate)
        joined = combined

    return joined


def _table_rows(name: str, tables: Tables) -> list[Row]:
    if name not in tables:
        raise QueryError(f"unknown table '{name}'")
    rows = tables[name]
    if isinstance(rows, (str, bytes, dict)) or not isinstance(rows, (list, tuple)):
        raise QueryError(f"table '{name}' must be a list of rows")
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError(f"table '{name}' contains a row that is not a mapping")
    return list(rows)


def _grouped_entries(
    plan: Any,
    rows: list[_JoinedRow],
    select: tuple[Any, ...],
    group_by: tuple[Any, ...],
    having: Any,
    order_by: tuple[Any, ...],
    aggregates: tuple[Any, ...],
) -> list[tuple[Row, tuple[Any, ...]]]:
    entries: list[tuple[Row, tuple[Any, ...]]] = []
    for representative, members in _group(group_by, rows):
        scope = _Scope(representative, members, _aggregate_values(aggregates, members))
        if having is not None and not _truth(_eval(having, scope)):
            continue
        entries.append((_project(select, scope), _sort_keys(order_by, scope)))
    return entries


def _group(
    group_by: tuple[Any, ...], rows: list[_JoinedRow]
) -> list[tuple[_JoinedRow, list[_JoinedRow]]]:
    if not group_by:
        return [(rows[0] if rows else {}, rows)]

    buckets: dict[tuple[Any, ...], tuple[_JoinedRow, list[_JoinedRow]]] = {}
    for row in rows:
# ... 1 further lines of this file were not included in this review excerpt ...

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