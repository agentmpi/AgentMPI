"""Abstract syntax tree for minidb.

Every node is an immutable, hashable dataclass; the helpers here only inspect
or render nodes.  This module validates nothing and therefore never raises
``QueryError``: malformed SQL is the business of the tokeniser, parser and
planner.  The ``ValueError`` raised by the helpers signals a caller bug (an
object that is not an AST node), not bad user SQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

__all__ = [
    "AGGREGATE_FUNCTIONS",
    "SCALAR_FUNCTIONS",
    "BinaryOp",
    "ColumnRef",
    "Expr",
    "FuncCall",
    "InList",
    "IsNull",
    "JoinClause",
    "Like",
    "Literal",
    "Node",
    "OrderItem",
    "SelectItem",
    "SelectStmt",
    "Star",
    "TableRef",
    "UnaryOp",
    "children",
    "contains_aggregate",
    "expr_source",
    "is_aggregate_call",
    "output_name",
    "walk",
]


@dataclass(frozen=True, slots=True)
class Literal:
    """A number, a string with ``''`` escapes already resolved, or NULL."""

    value: int | float | str | None


@dataclass(frozen=True, slots=True)
class ColumnRef:
    """``col`` is ``ColumnRef(None, "col")``, ``t.col`` is ``ColumnRef("t", "col")``."""

    table: str | None
    name: str


@dataclass(frozen=True, slots=True)
class Star:
    """``*`` is ``Star(None)``, ``t.*`` is ``Star("t")``."""

    table: str | None = None


@dataclass(frozen=True, slots=True)
class UnaryOp:
    """``op`` is one of ``NOT``, ``-``, ``+``."""

    op: str
    operand: Expr


@dataclass(frozen=True, slots=True)
class BinaryOp:
    """``op`` is arithmetic, a comparison (``!=`` normalised to ``<>``), AND or OR."""

    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class FuncCall:
    """A scalar or aggregate call; ``COUNT(*)`` is ``FuncCall("COUNT", (), True)``."""

    name: str
    args: tuple[Expr, ...] = ()
    star: bool = False


@dataclass(frozen=True, slots=True)
class IsNull:
    """``x IS NULL`` when ``negated`` is false, ``x IS NOT NULL`` when it is true."""

    operand: Expr
    negated: bool = False


@dataclass(frozen=True, slots=True)
class InList:
    """``x IN (a, b, ...)``."""

    operand: Expr
    items: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Like:
    """``x LIKE 'pat'``; ``pattern`` is normally a string ``Literal``."""

    operand: Expr
    pattern: Expr


@dataclass(frozen=True, slots=True)
class SelectItem:
    """One select_list entry.

    ``source_text`` is the entry's expression source with all whitespace
    removed, which the Output naming rule needs for unaliased non-column
    expressions.
    """

    expr: Expr
    alias: str | None = None
    source_text: str = ""


@dataclass(frozen=True, slots=True)
class TableRef:
    """A FROM or JOIN table with an optional alias."""

    name: str
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class JoinClause:
    """One INNER JOIN and its ON condition."""

    table: TableRef
    condition: Expr


@dataclass(frozen=True, slots=True)
class OrderItem:
    """One ORDER BY key; ASC (or absent) is ``descending=False``."""

    expr: Expr
    descending: bool = False


@dataclass(frozen=True, slots=True)
class SelectStmt:
    """A whole parsed query."""

    items: tuple[SelectItem, ...]
    source: TableRef
    distinct: bool = False
    joins: tuple[JoinClause, ...] = ()
    where: Expr | None = None
    group_by: tuple[Expr, ...] = ()
    having: Expr | None = None
    order_by: tuple[OrderItem, ...] = ()
    limit: int | None = None
    offset: int | None = None


Expr = Literal | ColumnRef | Star | UnaryOp | BinaryOp | FuncCall | IsNull | InList | Like
Node = Expr | SelectItem | TableRef | JoinClause | OrderItem | SelectStmt

AGGREGATE_FUNCTIONS: frozenset[str] = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
SCALAR_FUNCTIONS: frozenset[str] = frozenset(
    {"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"}
)

_EXPR_TYPES: tuple[type, ...] = (
    Literal,
    ColumnRef,
    Star,
    UnaryOp,
    BinaryOp,
    FuncCall,
    IsNull,
    InList,
    Like,
)
_NODE_TYPES: tuple[type, ...] = _EXPR_TYPES + (
    SelectItem,
    TableRef,
    JoinClause,
    OrderItem,
    SelectStmt,
)


def is_aggregate_call(expr: Expr) -> bool:
    """True iff ``expr`` is a call to one of the aggregate functions."""
    return isinstance(expr, FuncCall) and expr.name.upper() in AGGREGATE_FUNCTIONS


def contains_aggregate(expr: Expr) -> bool:
    """True iff ``expr`` is, or contains anywhere beneath it, an aggregate call."""
    return any(is_aggregate_call(node) for node in walk(expr))


def children(node: Node) -> tuple[Node, ...]:
    """The node's direct child nodes, in source order."""
    if isinstance(node, (Literal, ColumnRef, Star, TableRef)):
        return ()
    if isinstance(node, (UnaryOp, IsNull)):
        return (node.operand,)
    if isinstance(node, BinaryOp):
        return (node.left, node.right)
    if isinstance(node, FuncCall):
        return tuple(node.args)
    if isinstance(node, InList):
        return (node.operand, *node.items)
    if isinstance(node, Like):
        return (node.operand, node.pattern)
    if isinstance(node, (SelectItem, OrderItem)):
        return (node.expr,)
    if isinstance(node, JoinClause):
        return (node.table, node.condition)
    if isinstance(node, SelectStmt):
        found: list[Node] = [*node.items, node.source, *node.joins]
        if node.where is not None:
            found.append(node.where)
        found.extend(node.group_by)
        if node.having is not None:
            found.append(node.having)
        found.extend(node.order_by)
        return tuple(found)
    raise ValueError(f"not a minidb AST node: {node!r}")


def walk(node: Node) -> Iterator[Node]:
    """Pre-order iterator over ``node`` and every node beneath it."""
    if not isinstance(node, _NODE_TYPES):
        raise ValueError(f"not a minidb AST node: {node!r}")
    return _walk(node)


def _walk(node: Node) -> Iterator[Node]:
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(children(current)))


def expr_source(expr: Expr) -> str:
    """Canonical source rendering of ``expr`` with all whitespace removed."""
    if isinstance(expr, Literal):
        return _literal_source(expr.value)
    if isinstance(expr, ColumnRef):
        return expr.name if expr.table is None else f"{expr.table}.{expr.name}"
    if isinstance(expr, Star):
        return "*" if expr.table is None else f"{expr.table}.*"
    if isinstance(expr, UnaryOp):
        return f"{expr.op}{expr_source(expr.operand)}"
    if isinstance(expr, BinaryOp):
        return f"{expr_source(expr.left)}{expr.op}{expr_source(expr.right)}"
    if isinstance(expr, FuncCall):
        inner = "*" if expr.star else ",".join(expr_source(arg) for arg in expr.args)
        return f"{expr.name}({inner})"
    if isinstance(expr, IsNull):
        keyword = "ISNOTNULL" if expr.negated else "ISNULL"
        return f"{expr_source(expr.operand)}{keyword}"
    if isinstance(expr, InList):
        inner = ",".join(expr_source(item) for item in expr.items)
        return f"{expr_source(expr.operand)}IN({inner})"
    if isinstance(expr, Like):
        return f"{expr_source(expr.operand)}LIKE{expr_source(expr.pattern)}"
    raise ValueError(f"not a minidb expression node: {expr!r}")


def _literal_source(value: int | float | str | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def output_name(item: SelectItem) -> str:
    """The Output naming result for one select item."""
    if not isinstance(item, SelectItem):
        raise ValueError(f"not a select item: {item!r}")
    if isinstance(item.expr, Star):
        raise ValueError(
            "a star select item has no single output name; expand it first"
        )
    if item.alias is not None:
        return item.alias
    if isinstance(item.expr, ColumnRef):
        return item.expr.name
    if item.source_text:
        return item.source_text
    return expr_source(item.expr)
