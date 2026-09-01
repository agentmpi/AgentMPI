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

from functools import cmp_to_key

from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match
from .planner import Plan, resolve_column

_ARITHMETIC_OPS = frozenset({'+', '-', '*', '/'})
_COMPARISON_OPS = frozenset({'=', '==', '<>', '!=', '<', '<=', '>', '>='})
_EXPRESSION_NODES = frozenset({'Column', 'Literal', 'BinOp', 'UnaryOp', 'Func', 'Star'})

_MISSING = object()


def execute(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute a planned SELECT against `tables` and return the result rows."""
    try:
        return _run(plan, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f'query execution failed: {exc}') from exc


def _run(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    rows = _from_clause(plan, tables)
    if plan.where is not None:
        rows = [env for env in rows if _is_true(_eval(plan.where, env, plan, None))]
    if plan.is_aggregate:
        records = _aggregate_records(plan, rows)
    else:
        records = [(_output_row(plan, env, None), env, None) for env in rows]
    if plan.distinct:
        records = _distinct(records)
    if plan.order_by:
        records = _order(plan, records)
    return _slice(plan, [record[0] for record in records])


def _source_rows(tables: dict[str, list[dict]], table_name: str) -> list[dict]:
    if not isinstance(tables, dict) or table_name not in tables:
        raise QueryError(f'unknown table: {table_name}')
    rows = tables[table_name]
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise QueryError(f'table {table_name} is not a list of rows')
    return list(rows)


def _from_clause(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    if not plan.sources:
        return [{}]
    table_name, alias = plan.sources[0]
    envs = [{alias: row} for row in _source_rows(tables, table_name)]
    for index in range(1, len(plan.sources)):
        table_name, alias = plan.sources[index]
        right_rows = _source_rows(tables, table_name)
        condition = None
        if index - 1 < len(plan.join_conditions):
            condition = plan.join_conditions[index - 1]
        joined = []
        for env in envs:
            for row in right_rows:
                candidate = dict(env)
                candidate[alias] = row
                if condition is None or _is_true(_eval(condition, candidate, plan, None)):
                    joined.append(candidate)
        envs = joined
    return envs


def _aggregate_records(plan: Plan, rows: list[dict]) -> list[tuple]:
    groups: list[list[dict]] = []
    position_of_key: dict[tuple, int] = {}
    if plan.group_by:
        for env in rows:
            key = tuple(_hashable(_eval(expr, env, plan, None)) for expr in plan.group_by)
            position = position_of_key.get(key)
            if position is None:
                position_of_key[key] = len(groups)
                groups.append([env])
            else:
                groups[position].append(env)
    else:
        groups.append(list(rows))

    records = []
    for members in groups:
        aggregates = _group_aggregates(plan, members)
        env = members[0] if members else {}
        if plan.having is not None and not _is_true(_eval(plan.having, env, plan, aggregates)):
            continue
        records.append((_output_row(plan, env, aggregates), env, aggregates))
    return records


def _group_aggregates(plan: Plan, members: list[dict]) -> dict[int, object]:
    aggregates: dict[int, object] = {}
    for call in plan.aggregate_calls:
        name = _function_name(call)
        if name not in AGGREGATES:
            raise QueryError(f'unknown aggregate function: {name}')
        arguments = _function_args(call)
        if name == 'COUNT' and (not arguments or _is_star(arguments[0])):
            values = [1] * len(members)
        elif len(arguments) != 1:
            raise QueryError(f'{name} takes exactly one argument')
        else:
            values = [_eval(arguments[0], env, plan, None) for env in members]
        aggregates[id(call)] = AGGREGATES[name](values)
    return aggregates


def _output_row(plan: Plan, env: dict, aggregates: dict | None) -> dict:
    row: dict = {}
    for item in plan.select_items:
        row[item.name] = _eval(item.expr, env, plan, aggregates)
    return row


def _distinct(records: list[tuple]) -> list[tuple]:
    seen = set()
    unique = []
    for record in records:
        key = tuple(_hashable(value) for value in record[0].values())
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _order(plan: Plan, records: list[tuple]) -> list[tuple]:
    decorated = []
    for record in records:
        out_row, env, aggregates = record
        keys = []
        for order_key in plan.order_by:
            index = order_key.output_index
            if index is not None:
                if not 0 <= index < len(plan.select_items):
                    raise QueryError('ORDER BY refers to an output column that does not exist')
                keys.append(out_row.get(plan.select_items[index].name))
            else:
                keys.append(_eval(order_key.expr, env, plan, aggregates))
        decorated.append((keys, record))
    directions = [bool(order_key.descending) for order_key in plan.order_by]
    decorated.sort(key=cmp_to_key(lambda left, right: _compare_keys(left[0], right[0], directions)))
    return [record for _keys, record in decorated]


def _compare_keys(left: list, right: list, directions: list[bool]) -> int:
    for a, b, descending in zip(left, right, directions):
        if a is None and b is None:
            continue
        if a is None:
            result = -1
        elif b is None:
            result = 1
        else:
            result = compare(a, b)
            if result is None:
                continue
        if descending:
            result = -result
        if result:
            return result
    return 0


def _slice(plan: Plan, rows: list[dict]) -> list[dict]:
    offset = plan.offset or 0
    if offset < 0:
        raise QueryError('OFFSET must not be negative')
    limit = plan.limit
    if limit is not None and limit < 0:
        raise QueryError('LIMIT must not be negative')
    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _eval(node, env: dict, plan: Plan, aggregates: dict | None):
    kind = type(node).__name__
    if kind == 'Literal':
        return _attr(node, ('value', 'val', 'literal'))
    if kind == 'Column':
        return _column_value(node, env, plan)
    if kind == 'Func':
        return _eval_func(node, env, plan, aggregates)
    if kind == 'UnaryOp':
        return _eval_unary(node, env, plan, aggregates)
    if kind == 'BinOp':
        return _eval_binary(node, env, plan, aggregates)
    if kind == 'Star':
        raise QueryError('* is not valid in this position')
    if node is None or isinstance(node, (bool, int, float, str)):
        return node
    raise QueryError(f'cannot evaluate expression node: {kind}')


def _attr(node, names: tuple, default=_MISSING):
    for name in names:
        value = getattr(node, name, _MISSING)
        if value is not _MISSING:
            return value
    if default is not _MISSING:
        return default
    raise QueryError(f'unsupported {type(node).__name__} node in query')


def _column_value(node, env: dict, plan: Plan):
    qualifier = _attr(node, ('qualifier', 'table', 'table_name', 'prefix', 'alias'), None)
    name = _attr(node, ('name', 'column', 'column_name', 'col'))
    source_alias, column_name = resolve_column(plan, qualifier, name)
    row = env.get(source_alias)
    if not isinstance(row, dict):
        return None
    return row.get(column_name)


def _function_name(node) -> str:
    name = _attr(node, ('name', 'func', 'function', 'func_name', 'fname'))
    if not isinstance(name, str):
        raise QueryError('function name must be a string')
    return name.upper()


def _function_args(node) -> list:
    arguments = _attr(node, ('args', 'arguments', 'params', 'operands'), ())
    if arguments is None:
        return []
    if isinstance(arguments, (list, tuple)):
        return list(arguments)
    return [arguments]


def _is_star(node) -> bool:
    if type(node).__name__ == 'Star':
        return True
    return node is None or node == '*'


def _eval_func(node, env: dict, plan: Plan, aggregates: dict | None):
    name = _function_name(node)
    if name in AGGREGATES:
        if aggregates is None:
            raise QueryError(f'aggregate function {name} is not allowed here')
        if id(node) not in aggregates:
            raise QueryError(f'aggregate function {name} was not planned for this group')
        return aggregates[id(node)]
    if name 
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