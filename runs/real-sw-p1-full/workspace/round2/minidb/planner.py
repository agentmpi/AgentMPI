"""Name resolution, aggregate classification and validation.

:func:`plan` turns the parser's AST into a :class:`Plan`: a resolved,
validated description of the query in which every column reference carries the
index of the source it comes from, every aggregate has been hoisted into
``Plan.aggregates``, and output names are final.  The engine evaluates the
resolved expressions and never needs to look at the AST again.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from . import nodes
from .errors import QueryError

AGGREGATE_NAMES = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
SCALAR_ARITY: dict[str, tuple[int, int | None]] = {
    "UPPER": (1, 1),
    "LOWER": (1, 1),
    "LENGTH": (1, 1),
    "ABS": (1, 1),
    "COALESCE": (1, None),
}
_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})
_ARITHMETIC = frozenset({"+", "-", "*", "/"})


class Expr:
    """Base class of every resolved expression node."""


@dataclass
class Const(Expr):
    value: Any


@dataclass
class ColRef(Expr):
    source: int
    column: str


@dataclass
class Arith(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Negate(Expr):
    operand: Expr


@dataclass
class Compare(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class AndExpr(Expr):
    parts: list


@dataclass
class OrExpr(Expr):
    parts: list


@dataclass
class NotExpr(Expr):
    operand: Expr


@dataclass
class IsNullExpr(Expr):
    operand: Expr
    negated: bool = False


@dataclass
class InExpr(Expr):
    operand: Expr
    options: list


@dataclass
class LikeExpr(Expr):
    operand: Expr
    pattern: Expr


@dataclass
class ScalarCall(Expr):
    name: str
    args: list


@dataclass
class AggRef(Expr):
    """Reference to ``Plan.aggregates[index]``, evaluated per group."""

    index: int


@dataclass
class OutputRef(Expr):
    """Reference to output column ``index``; used by ``ORDER BY`` aliases."""

    index: int


@dataclass
class AggSpec:
    """One aggregate to compute for every group."""

    name: str
    arg: Expr | None
    star: bool


@dataclass
class Source:
    """One table in ``FROM``/``JOIN`` order.

    ``columns`` is the table's column order, taken from its first row; it is
    empty for a table with no rows.
    """

    name: str
    alias: str
    columns: list


@dataclass
class JoinStep:
    """Join source ``source_index`` on condition ``on``."""

    source_index: int
    on: Expr


@dataclass
class OrderTerm:
    expr: Expr
    desc: bool = False


@dataclass
class Plan:
    """A fully resolved query."""

    sources: list = field(default_factory=list)
    joins: list = field(default_factory=list)
    where: Expr | None = None
    group_exprs: list = field(default_factory=list)
    grouped: bool = False
    aggregates: list = field(default_factory=list)
    having: Expr | None = None
    output_names: list = field(default_factory=list)
    output_exprs: list = field(default_factory=list)
    order: list = field(default_factory=list)
    distinct: bool = False
    limit: int | None = None
    offset: int | None = None


class _Scope:
    """Resolves column references against the query's sources."""

    def __init__(self, sources: list) -> None:
        self.sources = sources
        self.aliases: dict[str, int] = {}
        for index, source in enumerate(sources):
            self.aliases[source.alias] = index

    def resolve(self, table: str | None, name: str) -> ColRef:
        if table is not None:
            if table not in self.aliases:
                raise QueryError(f"unknown table {table!r}")
            index = self.aliases[table]
            source = self.sources[index]
            if source.columns and name not in source.columns:
                raise QueryError(f"unknown column {table}.{name}")
            return ColRef(index, name)
        hits = [
            index
            for index, source in enumerate(self.sources)
            if name in source.columns
        ]
        if len(hits) > 1:
            raise QueryError(
                f"column {name!r} is ambiguous across the joined tables; qualify it"
            )
        if hits:
            return ColRef(hits[0], name)
        for index, source in enumerate(self.sources):
            if not source.columns:
                # An empty table has no known columns and contributes no rows,
                # so any name may belong to it.
                return ColRef(index, name)
        raise QueryError(f"unknown column {name!r}")


def plan(select: Any, tables: Any) -> Plan:
    """Resolve and validate ``select`` against ``tables``.

    Raises :class:`~minidb.errors.QueryError` for unknown tables or columns,
    ambiguous names, misplaced or nested aggregates, unknown functions,
    negative ``LIMIT``/``OFFSET``, and select expressions that are neither
    aggregated nor grouped.
    """
    if not isinstance(select, nodes.Select):
        raise QueryError("invalid query")
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to rows")
    if select.from_table is None:
        raise QueryError("the query has no FROM clause")

    sources: list[Source] = []
    seen_aliases: set[str] = set()

    def add_source(ref: Any) -> int:
        if not isinstance(ref, nodes.TableRef):
            raise QueryError("invalid table reference")
        if ref.name not in tables:
            raise QueryError(f"unknown table {ref.name!r}")
        rows = tables[ref.name]
        if not isinstance(rows, list):
            raise QueryError(f"table {ref.name!r} must be a list of rows")
        columns: list = []
        if rows:
            first = rows[0]
            if not isinstance(first, dict):
                raise QueryError(f"table {ref.name!r} must contain row dicts")
            columns = list(first.keys())
        alias = ref.alias or ref.name
        if alias in seen_aliases:
            raise QueryError(f"duplicate table name or alias {alias!r}")
        seen_aliases.add(alias)
        sources.append(Source(ref.name, alias, columns))
        return len(sources) - 1

    add_source(select.from_table)
    join_targets: list[int] = []
    for join in select.joins:
        if not isinstance(join, nodes.Join):
            raise QueryError("invalid join")
        join_targets.append(add_source(join.table))

    scope = _Scope(sources)
    aggregates: list[AggSpec] = []

    joins = [
        JoinStep(index, _plan_expr(join.on, scope, aggregates, False))
        for index, join in zip(join_targets, select.joins)
    ]

    where = (
        None
        if select.where is None
        else _plan_expr(select.where, scope, aggregates, False)
    )

    group_exprs = [
        _plan_expr(expr, scope, aggregates, False) for expr in select.group_by
    ]

    output_names: list[str] = []
    output_exprs: list[Expr] = []
    for item in select.items:
        if not isinstance(item, nodes.SelectItem):
            raise QueryError("invalid select item")
        if isinstance(item.expr, nodes.Star):
            table = item.expr.table
            if table is None:
                targets = list(range(len(sources)))
            else:
                if table not in scope.aliases:
                    raise QueryError(f"unknown table {table!r}")
                targets = [scope.aliases[table]]
            for index in targets:
                for column in sources[index].columns:
                    output_names.append(column)
                    output_exprs.append(ColRef(index, column))
            continue
        output_exprs.append(_plan_expr(item.expr, scope, aggregates, True))
        output_names.append(_output_name(item, len(output_names)))

    having = (
        None
        if select.having is None
        else _plan_expr(select.having, scope, aggregates, True)
    )

    order: list[OrderTerm] = []
    for key in select.order_by:
        if not isinstance(key, nodes.OrderKey):
            raise QueryError("invalid ORDER BY term")
        expr: Expr
        if (
            isinstance(key.expr, nodes.Column)
            and key.expr.table is None
            and key.expr.name in output_names
        ):
            expr = OutputRef(output_names.index(key.expr.name))
        else:
            expr = _plan_expr(key.expr, scope, aggregates, True)
        order.append(OrderTerm(expr, bool(key.desc)))

    grouped = bool(group_exprs) or bool(aggregates)

    if grouped:
        for name, expr in zip(output_names, output_exprs):
            _check_grouped(expr, group_exprs, f"select expression {name!r}")
        if having is not None:
            _check_grouped(having, group_exprs, "HAVING")
        for term in order:
            _check_grouped(term.expr, group_exprs, "ORDER BY")
    elif having is not None:
        # Without grouping or aggregates, HAVING is just another row filter.
        where = having if where is None else _and(where, having)
        having = None

    limit = _check_count(select.limit, "LIMIT")
    offset = _check_count(select.offset, "OFFSET")

    return Plan(
        sources=sources,
        joins=joins,
        where=where,
        group_exprs=group_exprs,
        grouped=grouped,
        aggregates=aggregates,
        having=having,
        output_names=output_names,
        output_exprs=output_exprs,
        order=order,
        distinct=bool(select.distinct),
        limit=limit,
        offset=offset,
    )


def _check_count(value: Any, clause: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{clause} requires an integer")
    if value < 0:
        raise QueryError(f"{clause} must not be negative")
    return value


def _output_name(item: Any, position: int) -> str:
    if item.alias:
        return item.alias
    if isinstance(item.expr, nodes.Column):
        return item.expr.name
    if item.text:
        return item.text
    return f"column{position + 1}"


def _and(left: Expr, right: Expr) -> Expr:
    parts: list[Expr] = []
    for part in (left, right):
        if isinstance(part, AndExpr):
            parts.extend(part.parts)
        else:
            parts.append(part)
    return AndExpr(parts)


def _or(left: Expr, right: Expr) -> Expr:
    parts: list[Expr] = []
    for part in (left, right):
        if isinstance(part, OrExpr):
            parts.extend(part.parts)
        else:
            parts.append(part)
    return OrExpr(parts)


def _plan_expr(
    node: Any, scope: _Scope, aggregates: list, allow_aggregates: bool
) -> Expr:
    if isinstance(node, nodes.Literal):
        return Const(node.value)
    if isinstance(node, nodes.Column):
        return scope.resolve(node.table, node.name)
    if isinstance(node, nodes.Star):
        raise QueryError("'*' is only allowed in a select list or in COUNT(*)")
    if isinstance(node, nodes.UnaryOp):
        op = node.op.upper()
        if op == "NOT":
            return NotExpr(
                _plan_expr(node.operand, scope, aggregates, allow_aggregates)
            )
        if op == "IS NULL":
            return IsNullExpr(
                _plan_expr(node.operand, scope, aggregates, allow_aggregates),
                False,
            )
        if op == "IS NOT NULL":
            return IsNullExpr(
                _plan_expr(node.operand, scope, aggregates, allow_aggregates),
                True,
            )
        if op == "-":
            return Negate(
                _plan_expr(node.operand, scope, aggregates, allow_aggregates)
            )
        if op == "+":
            return _plan_expr(node.operand, scope, aggregates, allow_aggregates)
        raise QueryError(f"unsupported operator {node.op!r}")
    if isinstance(node, nodes.BinOp):
        op = node.op.upper()
        if op == "AND":
            return _and(
                _plan_expr(node.left, scope, aggregates, allow_aggregates),
                _plan_expr(node.right, scope, aggregates, allow_aggregates),
            )
        if op == "OR":
            return _or(
                _plan_expr(node.left, scope, aggregates, allow_aggregates),
                _plan_expr(node.right, scope, aggregates, allow_aggregates),
            )
        if op == "IN":
            options = node.right if isinstance(node.right, list) else [node.right]
            return InExpr(
                _plan_expr(node.left, scope, aggregates, allow_aggregates),
                [
                    _plan_expr(option, scope, aggregates, allow_aggregates)
                    for option in options
                ],
            )
        if op == "LIKE":
            return LikeExpr(
                _plan_expr(node.left, scope, aggregates, allow_aggregates),
                _plan_expr(node.right, scope, aggregates, allow_aggregates),
            )
        if node.op in _COMPARISONS:
            return Compare(
                node.op,
                _plan_expr(node.left, scope, aggregates, allow_aggregates),
                _plan_expr(node.right, scope, aggregates, allow_aggregates),
            )
        if node.op in _ARITHMETIC:
            return Arith(
                node.op,
                _plan_expr(node.left, scope, aggregates, allow_aggregates),
                _plan_expr(node.right, scope, aggregates, allow_aggregates),
            )
        raise QueryError(f"unsupported operator {node.op!r}")
    if isinstance(node, nodes.Func):
        return _plan_call(node, scope, aggregates, allow_aggregates)
    raise QueryError("unsupported expression")


def _plan_call(
    node: Any, scope: _Scope, aggregates: list, allow_aggregates: bool
) -> Expr:
    name = node.name.upper()
    if name in AGGREGATE_NAMES:
        if not allow_aggregates:
            raise QueryError(f"{name} is not allowed here")
        if node.star:
            if name != "COUNT":
                raise QueryError(f"{name}(*) is not allowed; {name} needs a value")
            spec = AggSpec(name, None, True)
        else:
            if len(node.args) != 1:
                raise QueryError(
                    f"{name} takes exactly 1 argument, got {len(node.args)}"
                )
            spec = AggSpec(
                name, _plan_expr(node.args[0], scope, aggregates, False), False
            )
        for index, existing in enumerate(aggregates):
            if existing == spec:
                return AggRef(index)
        aggregates.append(spec)
        return AggRef(len(aggregates) - 1)
    if name in SCALAR_ARITY:
        if node.star:
            raise QueryError(f"{name}(*) is not allowed")
        low, high = SCALAR_ARITY[name]
        count = len(node.args)
        if count < low or (high is not None and count > high):
            raise QueryError(f"{name} was given {count} argument(s)")
        return ScalarCall(
            name,
            [
                _plan_expr(arg, scope, aggregates, allow_aggregates)
                for arg in node.args
            ],
        )
    raise QueryError(f"unknown function {node.name}")


def children(expr: Expr) -> list:
    """Return the immediate sub-expressions of a resolved expression."""
    out: list = []
    for info in fields(expr):
        value = getattr(expr, info.name)
        if isinstance(value, Expr):
            out.append(value)
        elif isinstance(value, list):
            out.extend(item for item in value if isinstance(item, Expr))
    return out


def _check_grouped(expr: Expr, group_exprs: list, where: str) -> None:
    for grouped in group_exprs:
        if expr == grouped:
            return
    if isinstance(expr, (Const, AggRef, OutputRef)):
        return
    if isinstance(expr, ColRef):
        raise QueryError(
            f"{where} uses column {expr.column!r}, which is neither grouped "
            "nor aggregated"
        )
    for child in children(expr):
        _check_grouped(child, group_exprs, where)
