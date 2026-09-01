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
                        in_aggregate=in_aggregate,
                    )
                return
            raise QueryError(f"unknown function: {name}")
        raise QueryError(f"unsupported expression in {clause}")

    # -- aggregates and grouping --------------------------------------------

    def has_aggregate(self, expr: object) -> bool:
        found: list[object] = []
        self.collect_aggregates(expr, found, set())
        return bool(found)

    def collect_aggregates(
        self, expr: object, out: list[object], seen: set[int]
    ) -> None:
        if isinstance(expr, Func):
            if expr.name in AGGREGATE_ARITY:
                if id(expr) not in seen:
                    seen.add(id(expr))
                    out.append(expr)
                return
            for arg in expr.args:
                self.collect_aggregates(arg, out, seen)
            return
        if isinstance(expr, UnaryOp):
            self.collect_aggregates(expr.operand, out, seen)
            return
        if isinstance(expr, BinOp):
            self.collect_aggregates(expr.left, out, seen)
            right = expr.right
            operands = right if isinstance(right, tuple) else (right,)
            for operand in operands:
                self.collect_aggregates(operand, out, seen)

    def canon(self, expr: object) -> tuple:
        if isinstance(expr, Column):
            alias, column = self.bind_column(expr)
            return ("col", alias, column)
        if isinstance(expr, Literal):
            return ("lit", type(expr.value).__name__, expr.value)
        if isinstance(expr, Star):
            return ("star", expr.table)
        if isinstance(expr, UnaryOp):
            return ("unary", expr.op, self.canon(expr.operand))
        if isinstance(expr, BinOp):
            right = expr.right
            if isinstance(right, tuple):
                shaped: tuple = ("list",) + tuple(self.canon(i) for i in right)
            else:
                shaped = self.canon(right)
            return ("binary", expr.op, self.canon(expr.left), shaped)
        if isinstance(expr, Func):
            return ("func", expr.name, tuple(self.canon(a) for a in expr.args))
        return ("opaque", repr(expr))

    def check_grouped(self, expr: object, clause: str, keys: set[tuple]) -> None:
        if self.canon(expr) in keys:
            return
        if isinstance(expr, Func) and expr.name in AGGREGATE_ARITY:
            return
        if isinstance(expr, (Literal, Star)):
            return
        if isinstance(expr, Column):
            raise QueryError(
                f"column {_written(expr)} in {clause} must appear in GROUP BY "
                "or be used inside an aggregate function"
            )
        if isinstance(expr, UnaryOp):
            self.check_grouped(expr.operand, clause, keys)
            return
        if isinstance(expr, BinOp):
            self.check_grouped(expr.left, clause, keys)
            right = expr.right
            operands = right if isinstance(right, tuple) else (right,)
            for operand in operands:
                self.check_grouped(operand, clause, keys)
            return
        if isinstance(expr, Func):
            for arg in expr.args:
                self.check_grouped(arg, clause, keys)

    # -- select list ---------------------------------------------------------

    def expand_items(self) -> list[PlannedItem]:
        items: list[PlannedItem] = []
        for item in self.select.items:
            expr = item.expr
            if isinstance(expr, Star):
                if item.alias is not None:
                    raise QueryError("* cannot be given an alias")
                if expr.table is None:
                    targets = [alias for _, alias in self.sources]
                else:
                    if expr.table not in self.columns:
                        raise QueryError(f"unknown table or alias: {expr.table}")
                    targets = [expr.table]
                for alias in targets:
                    for column in self.columns[alias]:
                        items = [i for i in items if i.name != column]
                        items.append(
                            PlannedItem(
                                name=column, expr=Column(name=column, table=alias)
                            )
                        )
                        self.bindings[(alias, column)] = (alias, column)
                continue
            self.validate(expr, "the select list", allow_aggregate=True)
            items.append(PlannedItem(name=self.output_name(item), expr=expr))
        return items

    @staticmethod
    def output_name(item: object) -> str:
        alias = getattr(item, "alias", None)
        if alias:
            return alias
        try:
            name = item.output_name()
        except Exception:
            name = ""
        if name:
            return name
        expr = getattr(item, "expr", None)
        if isinstance(expr, Column):
            return expr.name
        name = "".join(str(getattr(item, "source_text", "")).split())
        if not name:
            raise QueryError(
                "select expression has no name: give it an alias with AS"
            )
        return name

    # -- order by, limit, offset --------------------------------------------

    def plan_order(self, items: list[PlannedItem], keys: set[tuple]) -> list[PlannedOrderKey]:
        names = [item.name for item in items]
        planned: list[PlannedOrderKey] = []
        for key in self.select.order_by:
            expr = key.expr
            index: int | None = None
            if isinstance(expr, Column) and expr.table is None and expr.name in names:
                index = len(names) - 1 - names[::-1].index(expr.name)
            if index is None:
                self.validate(expr, "ORDER BY", allow_aggregate=True)
                if self.is_aggregate:
                    self.check_grouped(expr, "ORDER BY", keys)
            planned.append(
                PlannedOrderKey(
                    expr=expr,
                    descending=bool(key.descending),
                    output_index=index,
                )
            )
        return planned

    @staticmethod
    def check_count(value: object, label: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise QueryError(f"{label} must be an integer")
        if value < 0:
            raise QueryError(f"{label} must not be negative")
        return value

    # -- driver --------------------------------------------------------------

    def run(self) -> Plan:
        self.build_sources()
        self.build_bindings()

        where = self.select.where
        if where is not None:
            self.validate(where, "WHERE", allow_aggregate=False)

        join_conditions: list[object] = []
        for join in self.select.joins:
            self.validate(join.condition, "an ON condition", allow_aggregate=False)
            join_conditions.append(join.condition)

        group_by: list[object] = []
        for expr in self.select.group_by:
            self.validate(expr, "GROUP BY", allow_aggregate=False)
            group_by.append(expr)
        group_keys = {self.canon(expr) for expr in group_by}

        items = self.expand_items()

        having = self.select.having
        if having is not None:
            self.validate(having, "HAVING", allow_aggregate=True)

        aggregated = any(self.has_aggregate(item.expr) for item in items)
        if having is not None and self.has_aggregate(having):
            aggregated = True
        for key in self.select.order_by:
            if self.has_aggregate(key.expr):
                aggregated = True
        self.is_aggregate = bool(group_by) or having is not None or aggregated

        if self.is_aggregate:
            for item in items:
                self.check_grouped(item.expr, "the select list", group_keys)
            if having is not None:
                self.check_grouped(having, "HAVING", group_keys)

        order_by = self.plan_order(items, group_keys)

        calls: list[object] = []
        seen: set[int] = set()
        for item in items:
            self.collect_aggregates(item.expr, calls, seen)
        if having is not None:
            self.collect_aggregates(having, calls, seen)
        for key in order_by:
            if key.output_index is None:
                self.collect_aggregates(key.expr, calls, seen)

        return Plan(
            sources=list(self.sources),
            join_conditions=join_conditions,
            where=where,
            group_by=group_by,
            having=having,
            select_items=items,
            distinct=bool(self.select.distinct),
            order_by=order_by,
            limit=self.check_count(self.select.limit, "LIMIT"),
            offset=self.check_count(self.select.offset, "OFFSET"),
            is_aggregate=self.is_aggregate,
            aggregate_calls=calls,
            column_bindings=dict(self.bindings),
        )


def plan(select: Select, tables: dict[str, list[dict]]) -> Plan:
    """Resolve, classify and validate `select` against `tables`.

    Raises :class:`~minidb.errors.QueryError` for any invalid statement and
    never lets another exception type escape.
    """
    try:
        if not isinstance(tables, dict):
            raise QueryError("tables must be a mapping of name to list of rows")
        return _Planner(select, tables).run()
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"cannot plan query: {exc}") from exc


def resolve_column(plan: Plan, qualifier: str | None, name: str) -> tuple[str, str]:
    """Map a written column reference to the ``(source_alias, column)`` it binds to.

    Raises :class:`~minidb.errors.QueryError` when the reference is unknown or
    was ambiguous at plan time.
    """
    try:
        bound = plan.column_bindings.get((qualifier, name))
    except Exception as exc:
        raise QueryError(f"cannot resolve column: {name}") from exc
    if bound is None:
        written = f"{qualifier}.{name}" if qualifier is not None else name
        raise QueryError(f"unknown or ambiguous column: {written}")
    return bound
