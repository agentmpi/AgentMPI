You own `minidb/errors.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/engine.py (published exports: ["execute"])
```python
"""Execution of a planned minidb SELECT statement."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cmp_to_key

from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match

_MISSING = object()

_ARITHMETIC_OPS = frozenset({'+', '-', '*', '/'})
_COMPARISON_OPS = frozenset({'=', '==', '<>', '!=', '<', '<=', '>', '>='})
_NODE_CLASSES = frozenset({'Column', 'Literal', 'BinOp', 'UnaryOp', 'Func', 'Star'})


@dataclass(frozen=True)
class _ColumnRef:
    """A column already bound to a source alias, produced by `*` expansion."""

    alias: str
    column: str


@dataclass
class _Compiled:
    """The plan reduced to the shapes execution needs."""

    sources: list = field(default_factory=list)
    join_conditions: list = field(default_factory=list)
    where: object = None
    group_by: list = field(default_factory=list)
    having: object = None
    items: list = field(default_factory=list)
    distinct: bool = False
    order_by: list = field(default_factory=list)
    limit: object = None
    offset: object = None
    is_aggregate: bool = False
    aggregate_calls: list = field(default_factory=list)
    schema: dict = field(default_factory=dict)
    schemaless: set = field(default_factory=set)
    bindings: dict = field(default_factory=dict)


def execute(plan, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute a planned SELECT against `tables` and return the result rows."""
    try:
        compiled = _compile(plan, tables)
        return _run(compiled, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f'query execution failed: {exc}') from exc


def _field(obj, names: tuple, default=_MISSING):
    for name in names:
        value = getattr(obj, name, _MISSING)
        if value is not _MISSING:
            return value
    if default is not _MISSING:
        return default
    raise QueryError(f'unsupported {type(obj).__name__} object in plan')


def _compile(plan, tables: dict[str, list[dict]]) -> _Compiled:
    if not isinstance(tables, dict):
        raise QueryError('tables must be a mapping of table name to rows')
    compiled = _Compiled()
    compiled.sources = _sources(plan)
    for table_name, alias in compiled.sources:
        columns = _column_order(_source_rows(tables, table_name))
        compiled.schema[alias] = columns
        if not columns:
            compiled.schemaless.add(alias)
    compiled.join_conditions = _join_conditions(plan, len(compiled.sources))
    compiled.where = _field(plan, ('where', 'where_clause', 'filter'), None)
    compiled.group_by = list(_field(plan, ('group_by', 'groupby', 'group_keys'), ()) or ())
    compiled.having = _field(plan, ('having', 'having_clause'), None)
    compiled.distinct = bool(_field(plan, ('distinct', 'is_distinct'), False))
    compiled.limit = _field(plan, ('limit',), None)
    compiled.offset = _field(plan, ('offset',), None)
    compiled.items = _select_items(plan, compiled)
    compiled.order_by = _order_keys(plan, compiled)
    compiled.aggregate_calls = _aggregate_calls(compiled)
    declared = bool(_field(plan, ('is_aggregate', 'aggregate', 'has_aggregates'), False))
    compiled.is_aggregate = declared or bool(compiled.group_by) or bool(compiled.aggregate_calls)
    return compiled


def _sources(plan) -> list[tuple[str, str]]:
    raw = _field(plan, ('sources', 'from_sources', 'source_list', 'from_tables'), None)
    if not raw:
        raise QueryError('the plan describes no FROM source')
    sources = []
    seen = set()
    for entry in raw:
        table_name, alias = _source_entry(entry)
        if alias in seen:
            raise QueryError(f'duplicate table alias: {alias}')
        seen.add(alias)
        sources.append((table_name, alias))
    return sources


def _source_entry(entry) -> tuple[str, str]:
    if isinstance(entry, str):
        return (entry, entry)
    if isinstance(entry, (tuple, list)):
        if len(entry) == 2:
            return (str(entry[0]), str(entry[1]))
        if len(entry) == 1:
            return (str(entry[0]), str(entry[0]))
        raise QueryError('malformed FROM source in plan')
    name = _field(entry, ('table', 'name', 'table_name'))
    alias = _field(entry, ('alias', 'as_name'), None)
    if hasattr(name, 'name'):
        name = _field(name, ('name', 'table'))
    return (str(name), str(alias) if alias else str(name))


def _source_rows(tables: dict[str, list[dict]], table_name: str) -> list[dict]:
    if table_name not in tables:
        raise QueryError(f'unknown table: {table_name}')
    rows = tables[table_name]
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise QueryError(f'table {table_name} is not a list of rows')
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError(f'table {table_name} contains a row that is not a mapping')
    return list(rows)


def _column_order(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    return [column for column in rows[0] if isinstance(column, str)]


def _join_conditions(plan, source_count: int) -> list:
    raw = _field(plan, ('join_conditions', 'on_conditions', 'joins', 'join_on'), None)
    conditions = []
    for entry in raw or ():
        if entry is None or type(entry).__name__ in _NODE_CLASSES:
            conditions.append(entry)
        else:
            conditions.append(_field(entry, ('condition', 'on', 'expr'), None))
    while len(conditions) < max(source_count - 1, 0):
        conditions.append(None)
    return conditions


def _select_items(plan, compiled: _Compiled) -> list[tuple[str, object]]:
    raw = _field(plan, ('select_items', 'items', 'select_list', 'projections'), None)
    if raw is None:
        raise QueryError('the plan describes no select list')
    pairs = []
    for entry in raw:
        pairs.extend(_select_item(entry, compiled))
    if not pairs:
        raise QueryError('the select list is empty')
    ordered: list[tuple[str, object]] = []
    for name, expr in pairs:
        ordered = [pair for pair in ordered if pair[0] != name]
        ordered.append((name, expr))
    return ordered


def _select_item(entry, compiled: _Compiled) -> list[tuple[str, object]]:
    kind = type(entry).__name__
    if kind == 'Star':
        return _expand_star(_field(entry, ('table', 'qualifier'), None), compiled)
    if kind in _NODE_CLASSES:
        return [(_output_name(entry, None, None), entry)]
    expr = _field(entry, ('expr', 'expression', 'value'), None)
    name = _field(entry, ('name', 'output_name', 'out_name'), None)
    alias = _field(entry, ('alias', 'as_name', 'label'), None)
    source_text = _field(entry, ('source_text', 'source', 'text'), None)
    if expr is None and not isinstance(name, str):
        raise QueryError('malformed select item in plan')
    if expr is not None and type(expr).__name__ == 'Star':
        return _expand_star(_field(expr, ('table', 'qualifier'), None), compiled)
    if isinstance(name, str) and name:
        return [(name, expr)]
    return [(_output_name(expr, alias, source_text), expr)]


def _expand_star(qualifier, compiled: _Compiled) -> list[tuple[str, object]]:
    if qualifier is not None:
        qualifier = str(qualifier)
        if qualifier not in compiled.schema:
            raise QueryError(f'unknown table or alias: {qualifier}')
    expanded = []
    for _table_name, alias in compiled.sources:
        if qualifier is not None and alias != qualifier:
            continue
        for column in compiled.schema[alias]:
            expanded.append((column, _ColumnRef(alias, column)))
    return expanded


def _output_name(expr, alias, source_text) -> str:
    if isinstance(alias, str) and alias:
        return alias
    if isinstance(expr, _ColumnRef):
        return expr.column
    if type(expr).__name__ == 'Column':
        return str(_field(expr, ('name', 'column', 'column_name')))
    if isinstance(source_text, str) and source_text.strip():
        return ''.join(source_text.split())
    return _render(expr)


def _order_keys(plan, compiled: _Compiled) -> list[tuple[object, object, bool]]:
    raw = _field(plan, ('order_by', 'order_keys', 'orderby'), ()) or ()
    keys = []
    for entry in raw:
        if type(entry).__name__ in _NODE_CLASSES:
            keys.append((entry, _alias_index(entry, compiled), False))
            continue
        expr = _field(entry, ('expr', 'expression', 'key'), None)
        index = _field(entry, ('output_index', 'index', 'position'), None)
        if isinstance(index, bool) or not isinstance(index, int):
            index = _alias_index(expr, compiled)
        keys.append((expr, index, _descending(entry)))
    return keys


def _descending(entry) -> bool:
    value = _field(entry, ('descending', 'desc', 'is_descending'), None)
    if isinstance(value, bool):
        return value
    direction = _field(entry, ('direction', 
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