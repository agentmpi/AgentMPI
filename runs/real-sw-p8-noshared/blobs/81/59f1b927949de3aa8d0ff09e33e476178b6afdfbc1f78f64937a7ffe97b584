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

__all__ = [
    "AGGREGATE_ARITY",
    "SCALAR_ARITY",
    "Plan",
    "PlannedItem",
    "PlannedOrderKey",
    "plan",
    "resolve_column",
]


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

    @property
    def joins(self) -> tuple:
        return tuple(getattr(self.select, "joins", ()) or ())

    @staticmethod
    def alias_of(ref: object) -> str:
        namer = getattr(ref, "ref_name", None)
        if callable(namer):
            alias = namer()
            if alias:
                return str(alias)
        return str(getattr(ref, "alias", None) or ref.name)

    def build_sources(self) -> None:
        refs = [self.select.from_table]
        for join in self.joins:
            if str(getattr(join, "kind", "INNER")).upper() != "INNER":
                raise QueryError(f"unsupported join kind: {join.kind}")
            refs.append(join.table)
        for ref in refs:
            name = ref.name
            alias = self.alias_of(ref)
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
                            raise QueryError("COUNT(tabl
```

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