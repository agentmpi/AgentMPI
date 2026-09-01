"""Name resolution, aggregate classification and validation for minidb."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import QueryError
from .nodes import BinOp, Column, Func, Literal, Select, Star, UnaryOp

__all__ = ["Plan", "PlannedItem", "PlannedOrderKey", "plan"]

_AGGREGATES = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
_SCALARS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})
_SCALAR_ARITY = {
    "UPPER": (1, 1),
    "LOWER": (1, 1),
    "LENGTH": (1, 1),
    "ABS": (1, 1),
    "COALESCE": (1, None),
}


@dataclass(frozen=True)
class PlannedItem:
    """One output column: its name and the expression that produces it."""

    name: str
    expr: object


@dataclass(frozen=True)
class PlannedOrderKey:
    """One ORDER BY key; `output_index` is set when it names an output column."""

    expr: object
    descending: bool
    output_index: int | None


@dataclass
class Plan:
    """Everything the engine needs to run one SELECT."""

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
    bindings: dict = field(default_factory=dict)


def plan(select: Select, tables: dict[str, list[dict]]) -> Plan:
    """Resolve and validate `select` against `tables` and return a Plan."""
    if not isinstance(select, Select):
        raise QueryError("the parser did not produce a SELECT statement")
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to rows")
    return _Planner(select, tables).build()


class _Planner:
    def __init__(self, select: Select, tables: dict) -> None:
        self.select = select
        self.tables = tables
        self.sources: list[tuple[str, str]] = []
        self.schema: dict[str, list[str]] = {}
        self.open_schema: set[str] = set()
        self.bindings: dict[tuple, tuple] = {}
        self.aggregate_calls: list = []
        self.group_by: list = []

    def build(self) -> Plan:
        self._build_sources()
        join_conditions = []
        for join in self.select.joins:
            if join.on is None:
                raise QueryError("JOIN requires an ON condition")
            self._prepare(join.on, allow_aggregates=False)
            join_conditions.append(join.on)
        if self.select.where is not None:
            self._prepare(self.select.where, allow_aggregates=False)
        for expr in self.select.group_by:
            self._prepare(expr, allow_aggregates=False)
            self.group_by.append(expr)
        items = self._build_items()
        if self.select.having is not None:
            self._prepare(self.select.having, allow_aggregates=True)
        order_by = self._build_order_keys(items)
        is_aggregate = bool(self.group_by) or bool(self.aggregate_calls) or self.select.having is not None
        if is_aggregate:
            for item in items:
                self._validate_grouped(item.expr)
            if self.select.having is not None:
                self._validate_grouped(self.select.having)
            for key in order_by:
                if key.output_index is None:
                    self._validate_grouped(key.expr)
        limit = self._count("LIMIT", self.select.limit)
        offset = self._count("OFFSET", self.select.offset)
        return Plan(
            sources=self.sources,
            join_conditions=join_conditions,
            where=self.select.where,
            group_by=self.group_by,
            having=self.select.having,
            items=items,
            distinct=bool(self.select.distinct),
            order_by=order_by,
            limit=limit,
            offset=offset,
            is_aggregate=is_aggregate,
            aggregate_calls=self.aggregate_calls,
            schema=self.schema,
            bindings=self.bindings,
        )

    def _build_sources(self) -> None:
        refs = [self.select.from_] + [join.table for join in self.select.joins]
        for ref in refs:
            if ref is None:
                raise QueryError("the query has no FROM clause")
            alias = ref.alias or ref.name
            if ref.name not in self.tables:
                raise QueryError(f"unknown table: {ref.name}")
            if alias in self.schema:
                raise QueryError(f"duplicate table alias: {alias}")
            rows = self.tables[ref.name]
            self.schema[alias] = self._columns_of(ref.name, rows)
            if not self.schema[alias]:
                self.open_schema.add(alias)
            self.sources.append((ref.name, alias))

    def _columns_of(self, table_name: str, rows: object) -> list[str]:
        if rows is None:
            return []
        if not isinstance(rows, (list, tuple)):
            raise QueryError(f"table {table_name} is not a list of rows")
        if not rows:
            return []
        first = rows[0]
        if not isinstance(first, dict):
            raise QueryError(f"table {table_name} contains a row that is not a mapping")
        return [column for column in first if isinstance(column, str)]

    def _count(self, word: str, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise QueryError(f"{word} requires an integer")
        if value < 0:
            raise QueryError(f"{word} must not be negative")
        return value

    def _resolve(self, node: Column) -> tuple[str, str]:
        key = (node.table, node.name)
        cached = self.bindings.get(key)
        if cached is not None:
            return cached
        if node.table is not None:
            if node.table not in self.schema:
                raise QueryError(f"unknown table or alias: {node.table}")
            if node.name not in self.schema[node.table] and node.table not in self.open_schema:
                raise QueryError(f"unknown column: {node.table}.{node.name}")
            binding = (node.table, node.name)
        else:
            matches = [alias for _name, alias in self.sources if node.name in self.schema[alias]]
            if len(matches) > 1:
                raise QueryError(f"ambiguous column: {node.name}")
            if matches:
                binding = (matches[0], node.name)
            else:
                open_aliases = [alias for _name, alias in self.sources if alias in self.open_schema]
                if not open_aliases:
                    raise QueryError(f"unknown column: {node.name}")
                binding = (open_aliases[0], node.name)
        self.bindings[key] = binding
        return binding

    def _prepare(self, node: object, allow_aggregates: bool, inside_aggregate: bool = False) -> None:
        if node is None or isinstance(node, Literal):
            return
        if isinstance(node, Column):
            self._resolve(node)
            return
        if isinstance(node, Star):
            raise QueryError("* is not valid in this position")
        if isinstance(node, Func):
            self._prepare_func(node, allow_aggregates, inside_aggregate)
            return
        if isinstance(node, UnaryOp):
            self._prepare(node.operand, allow_aggregates, inside_aggregate)
            return
        if isinstance(node, BinOp):
            self._prepare(node.left, allow_aggregates, inside_aggregate)
            if isinstance(node.right, tuple):
                for item in node.right:
                    self._prepare(item, allow_aggregates, inside_aggregate)
            else:
                self._prepare(node.right, allow_aggregates, inside_aggregate)
            return
        raise QueryError(f"unsupported expression node: {type(node).__name__}")

    def _prepare_func(self, node: Func, allow_aggregates: bool, inside_aggregate: bool) -> None:
        name = node.name.upper()
        if name in _AGGREGATES:
            if not allow_aggregates:
                raise QueryError(f"aggregate function {name} is not allowed here")
            if inside_aggregate:
                raise QueryError("aggregate functions may not be nested")
            if len(node.args) != 1:
                raise QueryError(f"{name} takes exactly one argument")
            argument = node.args[0]
            if isinstance(argument, Star):
                if name != "COUNT" or argument.table is not None:
                    raise QueryError(f"{name}(*) is not allowed")
            else:
                self._prepare(argument, allow_aggregates=False, inside_aggregate=True)
            self.aggregate_calls.append(node)
            return
        if name not in _SCALARS:
            raise QueryError(f"unknown function: {name}")
        low, high = _SCALAR_ARITY[name]
        if len(node.args) < low or (high is not None and len(node.args) > high):
            raise QueryError(f"{name} called with {len(node.args)} argument(s)")
        for argument in node.args:
            self._prepare(argument, allow_aggregates, inside_aggregate)

    def _build_items(self) -> list[PlannedItem]:
        items: list[PlannedItem] = []
        for item in self.select.items:
            if isinstance(item.expr, Star):
                items.extend(self._expand_star(item.expr))
                continue
            self._prepare(item.expr, allow_aggregates=True)
            items.append(PlannedItem(self._item_name(item), item.expr))
        if not items:
            raise QueryError("the select list is empty")
        return items

    def _expand_star(self, star: Star) -> list[PlannedItem]:
        if star.table is not None and star.table not in self.schema:
            raise QueryError(f"unknown table or alias: {star.table}")
        expanded: list[PlannedItem] = []
        for _name, alias in self.sources:
            if star.table is not None and alias != star.table:
                continue
            for column in self.schema[alias]:
                self.bindings[(alias, column)] = (alias, column)
                expanded.append(PlannedItem(column, Column(alias, column)))
        return expanded

    def _item_name(self, item: object) -> str:
        alias = getattr(item, "alias", None)
        if isinstance(alias, str) and alias:
            return alias
        expr = item.expr
        if isinstance(expr, Column):
            return expr.name
        source_text = getattr(item, "source_text", "")
        if isinstance(source_text, str) and source_text:
            return source_text
        raise QueryError("cannot name an output column")

    def _build_order_keys(self, items: list[PlannedItem]) -> list[PlannedOrderKey]:
        keys: list[PlannedOrderKey] = []
        names = [item.name for item in items]
        for key in self.select.order_by:
            index = None
            expr = key.expr
            if isinstance(expr, Column) and expr.table is None and expr.name in names:
                index = names.index(expr.name)
            elif getattr(key, "source_text", "") in names:
                index = names.index(key.source_text)
            if index is None:
                self._prepare(expr, allow_aggregates=True)
                keys.append(PlannedOrderKey(expr, bool(key.descending), None))
            else:
                keys.append(PlannedOrderKey(items[index].expr, bool(key.descending), index))
        return keys

    def _validate_grouped(self, expr: object) -> None:
        if expr is None or isinstance(expr, Literal):
            return
        if self._matches_group(expr):
            return
        if isinstance(expr, Func):
            if expr.name.upper() in _AGGREGATES:
                return
            for argument in expr.args:
                self._validate_grouped(argument)
            return
        if isinstance(expr, UnaryOp):
            self._validate_grouped(expr.operand)
            return
        if isinstance(expr, BinOp):
            self._validate_grouped(expr.left)
            if isinstance(expr.right, tuple):
                for item in expr.right:
                    self._validate_grouped(item)
            else:
                self._validate_grouped(expr.right)
            return
        if isinstance(expr, Column):
            raise QueryError(
                f"column {expr.name} must appear in GROUP BY or be used in an aggregate function"
            )
        raise QueryError("unsupported expression in an aggregate query")

    def _matches_group(self, expr: object) -> bool:
        if not self.group_by:
            return False
        target = self._normalise(expr)
        return any(self._normalise(key) == target for key in self.group_by)

    def _normalise(self, expr: object) -> tuple:
        if isinstance(expr, Column):
            alias, column = self.bindings.get((expr.table, expr.name), (expr.table, expr.name))
            return ("column", alias, column)
        if isinstance(expr, Literal):
            return ("literal", type(expr.value).__name__, repr(expr.value))
        if isinstance(expr, Func):
            return ("func", expr.name.upper(), tuple(self._normalise(a) for a in expr.args))
        if isinstance(expr, UnaryOp):
            return ("unary", expr.op, self._normalise(expr.operand))
        if isinstance(expr, BinOp):
            if isinstance(expr.right, tuple):
                right: object = tuple(self._normalise(item) for item in expr.right)
            else:
                right = self._normalise(expr.right)
            return ("binop", expr.op, self._normalise(expr.left), right)
        if isinstance(expr, Star):
            return ("star", expr.table)
        return ("other", repr(expr))
