You own `minidb/api.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/planner.py (published exports: ["Plan", "PlannedItem", "PlannedOrderKey", "plan", "resolve_column", "SCALAR_ARITY", "AGGREGATE_ARITY"])
```python
"""Name resolution, aggregate classification and validation for minidb.

The planner turns the parsed :class:`~minidb.nodes.Select` into a fully
validated :class:`Plan`: every table and column reference is bound to a
source, ``*`` is expanded, output names are computed and the statement is
classified as aggregate or row-wise.  It evaluates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import QueryError
from .nodes import BinOp, Column, Func, Literal, Select, Star, UnaryOp

SCALAR_ARITY: dict[str, tuple[int, int | None]] = {
    "UPPER": (1, 1),
    "LOWER": (1, 1),
    "LENGTH": (1, 1),
    "ABS": (1, 1),
    "COALESCE": (1, None),
}

AGGREGATE_ARITY: dict[str, tuple[int, int | None]] = {
    "COUNT": (1, 1),
    "SUM": (1, 1),
    "AVG": (1, 1),
    "MIN": (1, 1),
    "MAX": (1, 1),
}


@dataclass
class PlannedItem:
    """One computed output column."""

    name: str
    expr: object


@dataclass
class PlannedOrderKey:
    """One ORDER BY key, possibly pointing at a computed output column."""

    expr: object
    descending: bool = False
    output_index: int | None = None


@dataclass
class Plan:
    """A validated, name-resolved description of one SELECT statement."""

    sources: list[tuple[str, str]] = field(default_factory=list)
    join_conditions: list[object] = field(default_factory=list)
    where: object | None = None
    group_by: list[object] = field(default_factory=list)
    having: object | None = None
    select_items: list[PlannedItem] = field(default_factory=list)
    distinct: bool = False
    order_by: list[PlannedOrderKey] = field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    is_aggregate: bool = False
    aggregate_calls: list[object] = field(default_factory=list)
    column_bindings: dict[tuple[str | None, str], tuple[str, str]] = field(
        default_factory=dict
    )


def _written(node: Column) -> str:
    return f"{node.table}.{node.name}" if node.table is not None else node.name


class _Planner:
    def __init__(self, select: Select, tables: dict[str, list[dict]]) -> None:
        self.select = select
        self.tables = tables
        self.sources: list[tuple[str, str]] = []
        self.columns: dict[str, list[str]] = {}
        self.known: dict[str, set[str]] = {}
        self.open_schema: set[str] = set()
        self.bindings: dict[tuple[str | None, str], tuple[str, str]] = {}
        self.ambiguous: set[str] = set()
        self.is_aggregate = False

    # -- sources and bindings ------------------------------------------------

    def build_sources(self) -> None:
        refs = [self.select.from_table]
        for join in self.select.joins:
            if str(join.kind).upper() != "INNER":
                raise QueryError(f"unsupported join kind: {join.kind}")
            refs.append(join.table)
        for ref in refs:
            name = ref.name
            alias = ref.ref_name()
            if name not in self.tables:
                raise QueryError(f"unknown table: {name}")
            if alias in self.known:
                raise QueryError(f"duplicate table alias: {alias}")
            rows = self.tables[name]
            if not isinstance(rows, list):
                raise QueryError(f"table {name} is not a list of rows")
            ordered: list[str] = []
            known: set[str] = set()
            for position, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise QueryError(f"row {position} of table {name} is not a mapping")
                for column in row:
                    if not isinstance(column, str):
                        raise QueryError(
                            f"column name in table {name} is not a string"
                        )
                    if column not in known:
                        known.add(column)
                        if position == 0:
                            ordered.append(column)
            if not rows:
                self.open_schema.add(alias)
            self.sources.append((name, alias))
            self.columns[alias] = ordered
            self.known[alias] = known

    def build_bindings(self) -> None:
        owners: dict[str, list[str]] = {}
        for _, alias in self.sources:
            for column in self.known[alias]:
                self.bindings[(alias, column)] = (alias, column)
                owners.setdefault(column, []).append(alias)
        for column, holders in owners.items():
            if len(holders) == 1:
                self.bindings[(None, column)] = (holders[0], column)
            else:
                self.ambiguous.add(column)

    def bind_column(self, node: Column) -> tuple[str, str]:
        key = (node.table, node.name)
        bound = self.bindings.get(key)
        if bound is not None:
            return bound
        if node.table is not None:
            if node.table not in self.known:
                raise QueryError(f"unknown table or alias: {node.table}")
            if node.table in self.open_schema:
                self.bindings[key] = (node.table, node.name)
                return self.bindings[key]
            raise QueryError(f"unknown column: {node.table}.{node.name}")
        if node.name in self.ambiguous:
            raise QueryError(
                f"ambiguous column: {node.name}; qualify it with a table name"
            )
        blank = [alias for _, alias in self.sources if alias in self.open_schema]
        if len(blank) == 1:
            self.bindings[key] = (blank[0], node.name)
            return self.bindings[key]
        if len(blank) > 1:
            raise QueryError(
                f"ambiguous column: {node.name}; qualify it with a table name"
            )
        raise QueryError(f"unknown column: {node.name}")

    # -- validation ----------------------------------------------------------

    @staticmethod
    def check_arity(name: str, args: tuple, bounds: tuple[int, int | None]) -> None:
        low, high = bounds
        count = len(args)
        if count < low or (high is not None and count > high):
            expected = f"{low}" if high == low else f"at least {low}"
            raise QueryError(
                f"{name} takes {expected} argument(s), got {count}"
            )

    def validate(
        self,
        expr: object,
        clause: str,
        *,
        allow_aggregate: bool,
        in_aggregate: bool = False,
    ) -> None:
        if isinstance(expr, Column):
            self.bind_column(expr)
            return
        if isinstance(expr, Literal):
            return
        if isinstance(expr, Star):
            raise QueryError(f"* is not allowed in {clause}")
        if isinstance(expr, UnaryOp):
            self.validate(
                expr.operand,
                clause,
                allow_aggregate=allow_aggregate,
                in_aggregate=in_aggregate,
            )
            return
        if isinstance(expr, BinOp):
            self.validate(
                expr.left,
                clause,
                allow_aggregate=allow_aggregate,
                in_aggregate=in_aggregate,
            )
            right = expr.right
            operands = right if isinstance(right, tuple) else (right,)
            for operand in operands:
                self.validate(
                    operand,
                    clause,
                    allow_aggregate=allow_aggregate,
                    in_aggregate=in_aggregate,
                )
            return
        if isinstance(expr, Func):
            name = expr.name
            if name in AGGREGATE_ARITY:
                if not allow_aggregate:
                    raise QueryError(
                        f"aggregate function {name} is not allowed in {clause}"
                    )
                if in_aggregate:
                    raise QueryError(
                        f"aggregate function {name} may not be nested "
                        "inside another aggregate"
                    )
                self.check_arity(name, expr.args, AGGREGATE_ARITY[name])
                for arg in expr.args:
                    if isinstance(arg, Star):
                        if name != "COUNT":
                            raise QueryError(f"* is not a valid argument of {name}")
                        if arg.table is not None:
                            raise QueryError("COUNT(table.*) is not supported")
                        continue
                    self.validate(
                        arg, clause, allow_aggregate=allow_aggregate, in_aggregate=True
                    )
                return
            if name in SCALAR_ARITY:
                self.check_arity(name, expr.args, SCALAR_ARITY[name])
                for arg in expr.args:
                    self.validate(
                        arg,
                        clause,
                        allow_aggregate=allow_aggregate,
                        
```

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