"""Name resolution, aggregate classification and validation for minidb.

The planner turns the parser's AST (``minidb.nodes``) into a fully resolved
:class:`Plan`.  Every name is resolved to a ``(table alias, column)`` pair,
every aggregate is hoisted into ``Plan.aggregates`` and replaced by an
:class:`AggRef`, and everything the specification lets the planner reject is
rejected here as a :class:`~minidb.errors.QueryError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import nodes
from .errors import QueryError

__all__ = [
    "AggRef",
    "AggregateCall",
    "Binary",
    "Column",
    "Expr",
    "Func",
    "InList",
    "IsNull",
    "Like",
    "Literal",
    "OrderItem",
    "Plan",
    "SelectItem",
    "TableRef",
    "Unary",
    "plan",
]


@dataclass(frozen=True)
class Literal:
    """A constant value; SQL ``NULL`` is ``None``."""

    value: object


@dataclass(frozen=True)
class Column:
    """A column reference resolved to one table alias of the query."""

    alias: str
    name: str


@dataclass(frozen=True)
class Unary:
    """``-x`` or ``NOT x``."""

    op: str
    operand: "Expr"


@dataclass(frozen=True)
class Binary:
    """An arithmetic, comparison or logical operator applied to two operands."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Func:
    """A scalar function call with validated name and arity."""

    name: str
    args: tuple["Expr", ...]


@dataclass(frozen=True)
class IsNull:
    """``x IS NULL`` or, with ``negated``, ``x IS NOT NULL``."""

    operand: "Expr"
    negated: bool


@dataclass(frozen=True)
class InList:
    """``x IN (a, b, ...)``."""

    operand: "Expr"
    items: tuple["Expr", ...]


@dataclass(frozen=True)
class Like:
    """``x LIKE 'pat'``."""

    operand: "Expr"
    pattern: "Expr"


@dataclass(frozen=True)
class AggRef:
    """Placeholder for ``Plan.aggregates[index]`` evaluated for the group."""

    index: int


Expr = Literal | Column | Unary | Binary | Func | IsNull | InList | Like | AggRef


@dataclass(frozen=True)
class TableRef:
    """One table of the ``FROM``/``JOIN`` chain and the alias it is known by."""

    name: str
    alias: str


@dataclass(frozen=True)
class SelectItem:
    """One output column: its final name and the expression producing it."""

    name: str
    expr: Expr


@dataclass(frozen=True)
class OrderItem:
    """One ``ORDER BY`` key."""

    expr: Expr
    descending: bool


@dataclass(frozen=True)
class AggregateCall:
    """One aggregate; ``arg`` is ``None`` exactly for ``COUNT(*)``."""

    func: str
    arg: Expr | None


@dataclass(frozen=True)
class Plan:
    """A validated, fully resolved query, ready for the engine."""

    from_tables: tuple[TableRef, ...]
    join_conditions: tuple[Expr, ...]
    select: tuple[SelectItem, ...]
    where: Expr | None
    group_by: tuple[Expr, ...]
    having: Expr | None
    order_by: tuple[OrderItem, ...]
    distinct: bool
    limit: int | None
    offset: int | None
    is_aggregate: bool
    aggregates: tuple[AggregateCall, ...]


_ARITHMETIC_OPS = frozenset({"+", "-", "*", "/"})
_COMPARISON_OPS = frozenset({"=", "<>", "<", "<=", ">", ">="})
_LOGICAL_OPS = frozenset({"AND", "OR"})
_BINARY_OPS = _ARITHMETIC_OPS | _COMPARISON_OPS | _LOGICAL_OPS
_ONE_ARG_SCALARS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS"})
_ONE_ARG_AGGREGATES = frozenset({"SUM", "AVG", "MIN", "MAX"})


def _subexpressions(expr: Expr) -> tuple[Expr, ...]:
    """The direct operands of a resolved expression."""

    if isinstance(expr, Unary):
        return (expr.operand,)
    if isinstance(expr, Binary):
        return (expr.left, expr.right)
    if isinstance(expr, Func):
        return expr.args
    if isinstance(expr, IsNull):
        return (expr.operand,)
    if isinstance(expr, InList):
        return (expr.operand,) + expr.items
    if isinstance(expr, Like):
        return (expr.operand, expr.pattern)
    return ()


class _Planner:
    """Resolution state for a single statement."""

    def __init__(self, statement: nodes.SelectStmt, tables: dict[str, list[dict]]) -> None:
        self.statement = statement
        self.tables = tables
        self.from_tables: list[TableRef] = []
        self.columns_by_alias: dict[str, tuple[str, ...] | None] = {}
        self.alias_of_name: dict[str, list[str]] = {}
        self.aggregates: list[AggregateCall] = []
        self.aggregate_index: dict[AggregateCall, int] = {}

    # -- tables ---------------------------------------------------------

    def collect_tables(self) -> None:
        refs = [self.statement.source]
        for join in self.statement.joins:
            refs.append(join.table)
        for ref in refs:
            name = ref.name
            if not isinstance(name, str) or name not in self.tables:
                raise QueryError(f"unknown table {name!r}")
            rows = self.tables[name]
            if not isinstance(rows, list):
                raise QueryError(f"table {name!r} is not a list of rows")
            alias = ref.alias if ref.alias is not None else name
            if alias in self.columns_by_alias:
                raise QueryError(f"duplicate table alias {alias!r}")
            self.from_tables.append(TableRef(name=name, alias=alias))
            self.columns_by_alias[alias] = self._schema_of(name, rows)
            self.alias_of_name.setdefault(name, []).append(alias)

    @staticmethod
    def _schema_of(name: str, rows: list[dict]) -> tuple[str, ...] | None:
        """Column names of a table, or ``None`` when the table has no rows."""

        if not rows:
            return None
        first = rows[0]
        if not isinstance(first, dict):
            raise QueryError(f"row of table {name!r} is not a dict")
        return tuple(str(key) for key in first)

    def alias_for_qualifier(self, qualifier: str) -> str:
        """Resolve a written ``t`` in ``t.col`` to a table alias in the query."""

        if qualifier in self.columns_by_alias:
            return qualifier
        candidates = self.alias_of_name.get(qualifier, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise QueryError(f"ambiguous table reference {qualifier!r}")
        raise QueryError(f"unknown table {qualifier!r}")

    def resolve_column(self, qualifier: str | None, name: str) -> Column:
        if qualifier is not None:
            alias = self.alias_for_qualifier(qualifier)
            known = self.columns_by_alias[alias]
            if known is not None and name not in known:
                raise QueryError(f"unknown column {qualifier + '.' + name!r}")
            return Column(alias=alias, name=name)
        matches = [
            alias
            for alias, known in self.columns_by_alias.items()
            if known is not None and name in known
        ]
        if len(matches) > 1:
            raise QueryError(f"ambiguous column {name!r}; qualify it with a table name")
        if len(matches) == 1:
            return Column(alias=matches[0], name=name)
        # No table with a known schema has the column.  An empty table has no
        # known schema, so a reference into one is accepted unvalidated.
        unknown = [
            alias for alias, known in self.columns_by_alias.items() if known is None
        ]
        if len(unknown) == 1:
            return Column(alias=unknown[0], name=name)
        if len(unknown) > 1:
            raise QueryError(f"ambiguous column {name!r}; qualify it with a table name")
        raise QueryError(f"unknown column {name!r}")

    # -- expressions ----------------------------------------------------

    def resolve(self, expr: Any, clause: str, allow_aggregates: bool) -> Expr:
        if isinstance(expr, nodes.Literal):
            return Literal(value=expr.value)
        if isinstance(expr, nodes.ColumnRef):
            return self.resolve_column(expr.table, expr.name)
        if isinstance(expr, nodes.Star):
            raise QueryError(f"'*' is not allowed in {clause}")
        if isinstance(expr, nodes.UnaryOp):
            operand = self.resolve(expr.operand, clause, allow_aggregates)
            if expr.op == "+":
                return operand
            if expr.op in ("-", "NOT"):
                return Unary(op=expr.op, operand=operand)
            raise QueryError(f"unsupported unary operator {expr.op!r}")
        if isinstance(expr, nodes.BinaryOp):
            op = "<>" if expr.op == "!=" else expr.op
            if op not in _BINARY_OPS:
                raise QueryError(f"unsupported operator {expr.op!r}")
            return Binary(
                op=op,
                left=self.resolve(expr.left, clause, allow_aggregates),
                right=self.resolve(expr.right, clause, allow_aggregates),
            )
        if isinstance(expr, nodes.FuncCall):
            return self.resolve_call(expr, clause, allow_aggregates)
        if isinstance(expr, nodes.IsNull):
            return IsNull(
                operand=self.resolve(expr.operand, clause, allow_aggregates),
                negated=bool(expr.negated),
            )
        if isinstance(expr, nodes.InList):
            if not expr.items:
                raise QueryError("IN requires at least one value")
            return InList(
                operand=self.resolve(expr.operand, clause, allow_aggregates),
                items=tuple(
                    self.resolve(item, clause, allow_aggregates) for item in expr.items
                ),
            )
        if isinstance(expr, nodes.Like):
            return Like(
                operand=self.resolve(expr.operand, clause, allow_aggregates),
                pattern=self.resolve(expr.pattern, clause, allow_aggregates),
            )
        raise QueryError(f"unsupported expression in {clause}")

    def resolve_call(
        self, call: nodes.FuncCall, clause: str, allow_aggregates: bool
    ) -> Expr:
        name = call.name.upper() if isinstance(call.name, str) else ""
        star = bool(call.star)
        args = tuple(call.args)
        if name in nodes.AGGREGATE_FUNCTIONS:
            if not allow_aggregates:
                raise QueryError(f"aggregate function {name} is not allowed in {clause}")
            return self.resolve_aggregate(name, args, star, clause)
        if star:
            raise QueryError(f"{name}(*) is not a valid function call")
        if name in _ONE_ARG_SCALARS:
            if len(args) != 1:
                raise QueryError(f"{name} takes exactly one argument")
        elif name == "COALESCE":
            if not args:
                raise QueryError("COALESCE takes at least one argument")
        else:
            raise QueryError(f"unknown function {call.name!r}")
        return Func(
            name=name,
            args=tuple(self.resolve(arg, clause, allow_aggregates) for arg in args),
        )

    def resolve_aggregate(
        self, name: str, args: tuple[Any, ...], star: bool, clause: str
    ) -> AggRef:
        if name == "COUNT":
            if star:
                if args:
                    raise QueryError("COUNT(*) takes no arguments")
                argument: Expr | None = None
            elif len(args) == 1:
                argument = self.resolve(args[0], f"{name}()", allow_aggregates=False)
            else:
                raise QueryError("COUNT takes exactly one argument or '*'")
        else:
            if star:
                raise QueryError(f"{name}(*) is not allowed; {name} needs an argument")
            if len(args) != 1:
                raise QueryError(f"{name} takes exactly one argument")
            argument = self.resolve(args[0], f"{name}()", allow_aggregates=False)
        call = AggregateCall(func=name, arg=argument)
        index = self.aggregate_index.get(call)
        if index is None:
            index = len(self.aggregates)
            self.aggregate_index[call] = index
            self.aggregates.append(call)
        return AggRef(index=index)

    # -- select list ----------------------------------------------------

    def expand_star(self, qualifier: str | None) -> list[SelectItem]:
        if qualifier is None:
            aliases = [ref.alias for ref in self.from_tables]
        else:
            aliases = [self.alias_for_qualifier(qualifier)]
        expanded: list[SelectItem] = []
        for alias in aliases:
            known = self.columns_by_alias[alias]
            if known is None:
                continue
            for column in known:
                expanded.append(
                    SelectItem(name=column, expr=Column(alias=alias, name=column))
                )
        return expanded

    def build_select(self) -> list[SelectItem]:
        items: list[SelectItem] = []
        if not self.statement.items:
            raise QueryError("the select list is empty")
        for item in self.statement.items:
            if isinstance(item.expr, nodes.Star):
                if item.alias is not None:
                    raise QueryError("'*' cannot be given an alias")
                items.extend(self.expand_star(item.expr.table))
                continue
            name = self.output_name(item)
            items.append(
                SelectItem(
                    name=name,
                    expr=self.resolve(item.expr, "the select list", allow_aggregates=True),
                )
            )
        return self.dedupe(items)

    @staticmethod
    def output_name(item: nodes.SelectItem) -> str:
        try:
            name = nodes.output_name(item)
        except ValueError as exc:  # pragma: no cover - Star handled by caller
            raise QueryError("cannot name this select item") from exc
        if not isinstance(name, str) or not name:
            raise QueryError("cannot name this select item")
        return name

    @staticmethod
    def dedupe(items: list[SelectItem]) -> list[SelectItem]:
        """Keep the last item of each output name, at its later position."""

        counts: dict[str, int] = {}
        for item in items:
            counts[item.name] = counts.get(item.name, 0) + 1
        result: list[SelectItem] = []
        for item in items:
            if counts[item.name] > 1:
                counts[item.name] -= 1
                continue
            result.append(item)
        return result

    # -- grouping -------------------------------------------------------

    @staticmethod
    def check_grouped(expr: Expr, group_exprs: frozenset, where: str) -> None:
        """Every column outside an aggregate must be a grouping expression."""

        if isinstance(expr, AggRef) or isinstance(expr, Literal) or expr in group_exprs:
            return
        if isinstance(expr, Column):
            raise QueryError(
                f"column {expr.alias + '.' + expr.name!r} in {where} must appear in "
                "GROUP BY or inside an aggregate"
            )
        children = _subexpressions(expr)
        if not children:
            raise QueryError(f"expression in {where} must appear in GROUP BY")
        for child in children:
            _Planner.check_grouped(child, group_exprs, where)

    # -- limits ---------------------------------------------------------

    @staticmethod
    def check_limit(value: Any, keyword: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise QueryError(f"{keyword} requires an integer")
        if value < 0:
            raise QueryError(f"{keyword} must not be negative")
        return value


def plan(statement: Any, tables: dict[str, list[dict]]) -> Plan:
    """Resolve and validate a parsed ``SELECT`` against ``tables``.

    Raises :class:`~minidb.errors.QueryError` for every rejected query and
    never lets a ``KeyError``, ``IndexError``, ``AttributeError``,
    ``TypeError`` or ``ValueError`` escape.
    """

    try:
        return _plan(statement, tables)
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is nested too deeply") from exc
    except (KeyError, IndexError, AttributeError, TypeError, ValueError) as exc:
        raise QueryError(f"malformed query: {exc}") from exc


def _plan(statement: Any, tables: dict[str, list[dict]]) -> Plan:
    if not isinstance(statement, nodes.SelectStmt):
        raise QueryError("the planner needs a parsed SELECT statement")
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to rows")

    planner = _Planner(statement, tables)
    planner.collect_tables()

    join_conditions = tuple(
        planner.resolve(join.condition, "an ON condition", allow_aggregates=False)
        for join in statement.joins
    )
    where = (
        None
        if statement.where is None
        else planner.resolve(statement.where, "WHERE", allow_aggregates=False)
    )
    group_by = tuple(
        planner.resolve(expr, "GROUP BY", allow_aggregates=False)
        for expr in statement.group_by
    )

    select = planner.build_select()

    having = (
        None
        if statement.having is None
        else planner.resolve(statement.having, "HAVING", allow_aggregates=True)
    )

    by_output_name = {item.name: item.expr for item in select}
    order_by: list[OrderItem] = []
    for item in statement.order_by:
        target = item.expr
        if (
            isinstance(target, nodes.ColumnRef)
            and target.table is None
            and target.name in by_output_name
        ):
            resolved = by_output_name[target.name]
        else:
            resolved = planner.resolve(target, "ORDER BY", allow_aggregates=True)
        order_by.append(OrderItem(expr=resolved, descending=bool(item.descending)))

    is_aggregate = bool(planner.aggregates) or bool(group_by) or having is not None
    if is_aggregate:
        group_exprs = frozenset(group_by)
        for item in select:
            planner.check_grouped(item.expr, group_exprs, "the select list")
        if having is not None:
            planner.check_grouped(having, group_exprs, "HAVING")
        for order_item in order_by:
            planner.check_grouped(order_item.expr, group_exprs, "ORDER BY")

    return Plan(
        from_tables=tuple(planner.from_tables),
        join_conditions=join_conditions,
        select=tuple(select),
        where=where,
        group_by=group_by,
        having=having,
        order_by=tuple(order_by),
        distinct=bool(statement.distinct),
        limit=planner.check_limit(statement.limit, "LIMIT"),
        offset=planner.check_limit(statement.offset, "OFFSET"),
        is_aggregate=is_aggregate,
        aggregates=tuple(planner.aggregates),
    )
