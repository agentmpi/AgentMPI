"""Name resolution, aggregate classification and validation for minidb.

The planner turns the parser's syntax tree into a fully resolved :class:`Plan`:
every column is bound to one table alias, every aggregate is hoisted into
``Plan.aggregates`` and replaced by an :class:`AggRef`, output names are
computed, and every error the specification lets the planner detect is raised
as a :class:`~minidb.errors.QueryError`.

The syntax tree owned by ``minidb.nodes`` published no interface, so this
module reads it structurally: a node is recognised by the fields it carries
(``op``/``left``/``right``, ``name``/``args``, ``operand``/``pattern`` and so
on), accepting both attribute-style nodes and mapping-style nodes, and never
depending on a particular class name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import QueryError

__all__ = [
    "AGGREGATE_FUNCTIONS",
    "SCALAR_FUNCTIONS",
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
    """An arithmetic, comparison or logical operator over two operands."""

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


AGGREGATE_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
SCALAR_FUNCTIONS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

_ARITHMETIC_OPS = frozenset({"+", "-", "*", "/"})
_COMPARISON_OPS = frozenset({"=", "<>", "<", "<=", ">", ">="})
_LOGICAL_OPS = frozenset({"AND", "OR"})
_BINARY_OPS = _ARITHMETIC_OPS | _COMPARISON_OPS | _LOGICAL_OPS
_ONE_ARG_SCALARS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS"})

_MISSING = object()
_ABSENT = object()


def _field(node: Any, *names: str, default: Any = _MISSING) -> Any:
    """First of ``names`` present on ``node``, as attribute or mapping key."""

    if isinstance(node, Mapping):
        for name in names:
            if name in node:
                return node[name]
    else:
        for name in names:
            value = getattr(node, name, _MISSING)
            if value is not _MISSING:
                return value
    if default is _MISSING:
        raise QueryError("the planner cannot understand this parse tree")
    return default


def _has(node: Any, *names: str) -> bool:
    return _field(node, *names, default=_ABSENT) is not _ABSENT


def _sequence(value: Any) -> tuple[Any, ...]:
    """Normalise an optional node sequence to a tuple."""

    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _normalise_op(op: Any) -> str:
    if not isinstance(op, str):
        raise QueryError("unsupported operator in the query")
    text = op.strip()
    upper = text.upper()
    if upper in {"AND", "OR", "NOT"}:
        return upper
    if text == "!=":
        return "<>"
    if text == "==":
        return "="
    return text


def _descending_of(item: Any) -> bool:
    """Whether an ORDER BY item is DESC, whichever polarity it records."""

    descending = _field(item, "descending", "desc", default=None)
    if descending is not None:
        return bool(descending)
    ascending = _field(item, "ascending", "asc", default=None)
    if ascending is not None:
        return not ascending
    direction = _field(item, "direction", "order", "sort", default=None)
    if isinstance(direction, str):
        return direction.strip().upper() in {"DESC", "DESCENDING"}
    return False


def _is_star(node: Any) -> bool:
    """True when ``node`` denotes ``*`` or ``t.*``."""

    if isinstance(node, str):
        text = node.strip()
        return text == "*" or text.endswith(".*")
    if "star" in type(node).__name__.lower():
        return True
    kind = _field(node, "type", "kind", default=None)
    if isinstance(kind, str) and kind.strip().lower() in {"star", "wildcard", "asterisk"}:
        return True
    if _has(node, "args", "arguments"):
        return False
    name = _field(node, "name", "column", default=None)
    return name == "*"


def _star_qualifier(node: Any) -> str | None:
    """The table qualifier of a star node, or ``None`` for a bare ``*``."""

    if isinstance(node, str):
        text = node.strip()
        return None if text == "*" else text[:-2] or None
    qualifier = _field(node, "table", "qualifier", "prefix", "table_name", default=None)
    if isinstance(qualifier, str) and qualifier:
        return qualifier
    return None


class _Planner:
    """Resolution state for a single statement."""

    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        self.from_tables: list[TableRef] = []
        self.columns_by_alias: dict[str, tuple[str, ...] | None] = {}
        self.aliases_of_name: dict[str, list[str]] = {}
        self.aggregates: list[AggregateCall] = []
        self.aggregate_index: dict[AggregateCall, int] = {}

    # -- tables ---------------------------------------------------------

    def add_table(self, ref: Any) -> None:
        if isinstance(ref, str):
            name: Any = ref
            written_alias: Any = None
        else:
            name = _field(ref, "name", "table", "table_name")
            written_alias = _field(ref, "alias", "as_name", default=None)
        if not isinstance(name, str):
            raise QueryError("a FROM entry has no table name")
        if name not in self.tables:
            raise QueryError(f"unknown table {name!r}")
        rows = self.tables[name]
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise QueryError(f"table {name!r} is not a list of rows")
        alias = written_alias if isinstance(written_alias, str) and written_alias else name
        if alias in self.columns_by_alias:
            raise QueryError(f"duplicate table alias {alias!r}")
        self.from_tables.append(TableRef(name=name, alias=alias))
        self.columns_by_alias[alias] = self._schema_of(name, rows)
        self.aliases_of_name.setdefault(name, []).append(alias)

    @staticmethod
    def _schema_of(name: str, rows: Sequence[Any]) -> tuple[str, ...] | None:
        """Column names of a table, or ``None`` when the table has no rows."""

        if len(rows) == 0:
            return None
        first = rows[0]
        if not isinstance(first, Mapping):
            raise QueryError(f"a row of table {name!r} is not a dict")
        columns: list[str] = []
        for key in first:
            if not isinstance(key, str):
                raise QueryError(
                    f"table {name!r} has a column name that is not a string"
                )
            columns.append(key)
        return tuple(columns)

    def alias_for_qualifier(self, qualifier: str) -> str:
        if qualifier in self.columns_by_alias:
            return qualifier
        candidates = self.aliases_of_name.get(qualifier, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise QueryError(f"ambiguous table reference {qualifier!r}")
        raise QueryError(f"unknown table {qualifier!r}")

    def resolve_column(self, qualifier: Any, name: Any) -> Column:
        if not isinstance(name, str) or not name:
            raise QueryError("a column reference has no name")
        if isinstance(qualifier, str) and qualifier:
            alias = self.alias_for_qualifier(qualifier)
            known = self.columns_by_alias[alias]
            if known is not None and name not in known:
                raise QueryError(f"unknown column '{qualifier}.{name}'")
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
        # A table with no rows has no discoverable schema, so a reference into
        # one is accepted without validation.
        unknown = [
            alias for alias, known in self.columns_by_alias.items() if known is None
        ]
        if len(unknown) == 1:
            return Column(alias=unknown[0], name=name)
        if len(unknown) > 1:
            raise QueryError(f"ambiguous column {name!r}; qualify it with a table name")
        raise QueryError(f"unknown column {name!r}")

    # -- expressions ----------------------------------------------------

    def resolve(self, node: Any, clause: str, allow_aggregates: bool) -> Expr:
        if node is None or isinstance(node, (bool, int, float)):
            return Literal(value=node)
        if _is_star(node):
            raise QueryError(f"'*' is not allowed in {clause}")
        if isinstance(node, str):
            return Literal(value=node)
        if _has(node, "left") and _has(node, "right"):
            op = _normalise_op(_field(node, "op", "operator"))
            if op not in _BINARY_OPS:
                raise QueryError(f"unsupported operator {op!r}")
            return Binary(
                op=op,
                left=self.resolve(_field(node, "left"), clause, allow_aggregates),
                right=self.resolve(_field(node, "right"), clause, allow_aggregates),
            )
        if _has(node, "args", "arguments") or _has(node, "func", "function"):
            return self.resolve_call(node, clause, allow_aggregates)
        if _has(node, "pattern"):
            return Like(
                operand=self.resolve(
                    _field(node, "operand", "expr", "value", "left"),
                    clause,
                    allow_aggregates,
                ),
                pattern=self.resolve(_field(node, "pattern"), clause, allow_aggregates),
            )
        if _has(node, "items", "values", "candidates") and _has(
            node, "operand", "expr", "value", "left"
        ):
            items = _sequence(_field(node, "items", "values", "candidates"))
            if not items:
                raise QueryError("IN requires at least one value")
            return InList(
                operand=self.resolve(
                    _field(node, "operand", "expr", "value", "left"),
                    clause,
                    allow_aggregates,
                ),
                items=tuple(
                    self.resolve(item, clause, allow_aggregates) for item in items
                ),
            )
        if _has(node, "negated", "is_not", "not_"):
            return IsNull(
                operand=self.resolve(
                    _field(node, "operand", "expr", "value"), clause, allow_aggregates
                ),
                negated=bool(_field(node, "negated", "is_not", "not_")),
            )
        if _has(node, "op", "operator"):
            op = _normalise_op(_field(node, "op", "operator"))
            operand = self.resolve(
                _field(node, "operand", "expr", "value", "right"),
                clause,
                allow_aggregates,
            )
            if op == "+":
                return operand
            if op in {"-", "NOT"}:
                return Unary(op=op, operand=operand)
            raise QueryError(f"unsupported unary operator {op!r}")
        if _has(node, "name", "column"):
            return self.resolve_column(
                _field(node, "table", "qualifier", "prefix", "table_name", default=None),
                _field(node, "name", "column"),
            )
        if _has(node, "value", "literal"):
            return Literal(value=_field(node, "value", "literal"))
        if "null" in type(node).__name__.lower():
            return IsNull(
                operand=self.resolve(
                    _field(node, "operand", "expr", "value"), clause, allow_aggregates
                ),
                negated=False,
            )
        raise QueryError(f"unsupported expression in {clause}")

    def resolve_call(self, node: Any, clause: str, allow_aggregates: bool) -> Expr:
        raw_name = _field(node, "name", "func", "function", "func_name")
        if not isinstance(raw_name, str) or not raw_name:
            raise QueryError("a function call has no name")
        name = raw_name.upper()
        args = _sequence(_field(node, "args", "arguments", default=()))
        star = bool(_field(node, "star", "is_star", default=False))
        if args and all(_is_star(arg) for arg in args):
            star = True
            args = ()
        if name == "COUNT" and not args:
            # A parser may encode COUNT(*) either with a star flag or with an
            # empty argument list; both mean "count rows".
            star = True
        if name in AGGREGATE_FUNCTIONS:
            if not allow_aggregates:
                raise QueryError(f"aggregate function {name} is not allowed in {clause}")
            return self.resolve_aggregate(name, args, star)
        if star:
            raise QueryError(f"{name}(*) is not a valid function call")
        if name in _ONE_ARG_SCALARS:
            if len(args) != 1:
                raise QueryError(f"{name} takes exactly one argument")
        elif name == "COALESCE":
            if not args:
                raise QueryError("COALESCE takes at least one argument")
        else:
            raise QueryError(f"unknown function {raw_name!r}")
        return Func(
            name=name,
            args=tuple(self.resolve(arg, clause, allow_aggregates) for arg in args),
        )

    def resolve_aggregate(
        self, name: str, args: tuple[Any, ...], star: bool
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

    # -- output naming --------------------------------------------------

    def render(self, node: Any) -> str:
        """Best-effort source rendering of a node, whitespace removed."""

        if node is None:
            return "NULL"
        if isinstance(node, bool):
            return "TRUE" if node else "FALSE"
        if isinstance(node, (int, float)):
            return repr(node)
        if isinstance(node, str):
            return node.replace(" ", "")
        if _is_star(node):
            qualifier = _star_qualifier(node)
            return "*" if qualifier is None else f"{qualifier}.*"
        if _has(node, "left") and _has(node, "right"):
            op = _normalise_op(_field(node, "op", "operator"))
            return (
                f"{self.render(_field(node, 'left'))}{op}"
                f"{self.render(_field(node, 'right'))}"
            )
        if _has(node, "args", "arguments") or _has(node, "func", "function"):
            raw_name = _field(node, "name", "func", "function", "func_name", default="")
            args = _sequence(_field(node, "args", "arguments", default=()))
            star = bool(_field(node, "star", "is_star", default=False))
            rendered = "*" if star and not args else ",".join(self.render(a) for a in args)
            return f"{str(raw_name).upper()}({rendered})"
        if _has(node, "pattern"):
            operand = _field(node, "operand", "expr", "value", "left", default=None)
            return f"{self.render(operand)}LIKE{self.render(_field(node, 'pattern'))}"
        if _has(node, "items", "values", "candidates") and _has(
            node, "operand", "expr", "value", "left"
        ):
            operand = _field(node, "operand", "expr", "value", "left")
            items = _sequence(_field(node, "items", "values", "candidates"))
            return f"{self.render(operand)}IN({','.join(self.render(i) for i in items)})"
        if _has(node, "negated", "is_not", "not_"):
            operand = _field(node, "operand", "expr", "value", default=None)
            negated = bool(_field(node, "negated", "is_not", "not_"))
            return f"{self.render(operand)}IS{'NOT' if negated else ''}NULL"
        if _has(node, "op", "operator"):
            op = _normalise_op(_field(node, "op", "operator"))
            operand = _field(node, "operand", "expr", "value", "right", default=None)
            return f"{op}{self.render(operand)}"
        if _has(node, "name", "column"):
            qualifier = _field(
                node, "table", "qualifier", "prefix", "table_name", default=None
            )
            name = _field(node, "name", "column")
            return f"{qualifier}.{name}" if qualifier else str(name)
        if _has(node, "value", "literal"):
            value = _field(node, "value", "literal")
            if isinstance(value, str):
                escaped = value.replace("'", "''")
                return f"'{escaped}'"
            return self.render(value)
        return "?"

    def item_name(self, item: Any, node: Any) -> str:
        published = _field(item, "output_name", default=None)
        if isinstance(published, str) and published:
            return published
        alias = _field(item, "alias", "as_name", "label", default=None)
        if isinstance(alias, str) and alias:
            return alias
        if _has(node, "name", "column") and not _has(node, "args", "arguments"):
            name = _field(node, "name", "column")
            if isinstance(name, str) and name:
                return name
        source_text = _field(item, "source_text", "source", "text", "sql", default=None)
        if isinstance(source_text, str) and source_text.strip():
            return "".join(source_text.split())
        return "".join(self.render(node).split())

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

    def build_select(self, items: tuple[Any, ...]) -> list[SelectItem]:
        if not items:
            raise QueryError("the select list is empty")
        built: list[SelectItem] = []
        for item in items:
            node = item if _is_star(item) else _field(item, "expr", "expression", "value", default=item)
            if _is_star(node):
                built.extend(self.expand_star(_star_qualifier(node)))
                continue
            built.append(
                SelectItem(
                    name=self.item_name(item, node),
                    expr=self.resolve(node, "the select list", allow_aggregates=True),
                )
            )
        return self.dedupe(built)

    @staticmethod
    def dedupe(items: list[SelectItem]) -> list[SelectItem]:
        """Keep the last item of each output name, at its later position."""

        remaining: dict[str, int] = {}
        for item in items:
            remaining[item.name] = remaining.get(item.name, 0) + 1
        result: list[SelectItem] = []
        for item in items:
            if remaining[item.name] > 1:
                remaining[item.name] -= 1
                continue
            result.append(item)
        return result

    # -- grouping -------------------------------------------------------

    @staticmethod
    def check_grouped(expr: Expr, group_exprs: frozenset, where: str) -> None:
        """Outside an aggregate, only grouping expressions may be referenced."""

        if isinstance(expr, (AggRef, Literal)) or expr in group_exprs:
            return
        if isinstance(expr, Column):
            raise QueryError(
                f"column '{expr.alias}.{expr.name}' in {where} must appear in "
                "GROUP BY or inside an aggregate"
            )
        children = _subexpressions(expr)
        if not children:
            raise QueryError(f"an expression in {where} must appear in GROUP BY")
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
        raise QueryError("the query is nested too deeply") from exc
    except (KeyError, IndexError, AttributeError, TypeError, ValueError) as exc:
        raise QueryError(f"malformed query: {exc}") from exc


def _plan(statement: Any, tables: Any) -> Plan:
    if statement is None or isinstance(statement, (str, bytes, int, float, bool)):
        raise QueryError("the planner needs a parsed SELECT statement")
    if not isinstance(tables, Mapping):
        raise QueryError("tables must be a mapping of table name to rows")

    planner = _Planner(dict(tables))
    planner.add_table(_field(statement, "source", "from_table", "from_", "table", "source_table"))
    joins = _sequence(_field(statement, "joins", "join_clauses", default=()))
    join_nodes: list[Any] = []
    for join in joins:
        planner.add_table(_field(join, "table", "table_ref", "source", "right"))
        join_nodes.append(_field(join, "condition", "on", "on_condition", "expr"))

    join_conditions = tuple(
        planner.resolve(condition, "an ON condition", allow_aggregates=False)
        for condition in join_nodes
    )

    where_node = _field(statement, "where", "where_clause", default=None)
    where = (
        None
        if where_node is None
        else planner.resolve(where_node, "WHERE", allow_aggregates=False)
    )

    group_by = tuple(
        planner.resolve(node, "GROUP BY", allow_aggregates=False)
        for node in _sequence(_field(statement, "group_by", "groupby", "group", default=()))
    )

    select = planner.build_select(
        _sequence(
            _field(
                statement,
                "items",
                "select_items",
                "select_list",
                "select",
                "columns",
                "projections",
                "targets",
            )
        )
    )

    having_node = _field(statement, "having", "having_clause", default=None)
    having = (
        None
        if having_node is None
        else planner.resolve(having_node, "HAVING", allow_aggregates=True)
    )

    by_output_name = {item.name: item.expr for item in select}
    order_by: list[OrderItem] = []
    for item in _sequence(
        _field(statement, "order_by", "orderby", "order", "order_items", default=())
    ):
        node = _field(item, "expr", "expression", "value", default=item)
        descending = _descending_of(item)
        if (
            not _has(node, "args", "arguments")
            and _has(node, "name", "column")
            and not _field(node, "table", "qualifier", "prefix", "table_name", default=None)
            and _field(node, "name", "column") in by_output_name
        ):
            resolved = by_output_name[_field(node, "name", "column")]
        else:
            resolved = planner.resolve(node, "ORDER BY", allow_aggregates=True)
        order_by.append(OrderItem(expr=resolved, descending=bool(descending)))

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
        distinct=bool(_field(statement, "distinct", "is_distinct", default=False)),
        limit=planner.check_limit(_field(statement, "limit", default=None), "LIMIT"),
        offset=planner.check_limit(_field(statement, "offset", default=None), "OFFSET"),
        is_aggregate=is_aggregate,
        aggregates=tuple(planner.aggregates),
    )
