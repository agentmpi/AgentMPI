You own `minidb/parser.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/nodes.py (published exports: ["Literal", "ColumnRef", "Star", "UnaryOp", "BinaryOp", "FuncCall", "IsNull", "InList", "Like", "SelectItem", "TableRef", "JoinClause", "OrderItem", "SelectStmt", "Expr", "Node", "AGGREGATE_FUNCTIONS", "SCALAR_FUNCTIONS", "is_aggregate_call", "contains_aggregate", "children", "walk", "expr_source", "output_name"])
```python
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

```

### minidb/functions.py (published exports: ["SCALARS", "AGGREGATES", "like_match", "compare", "order_cmp"])
```python
"""Scalar and aggregate function behaviour, LIKE matching and value comparison.

Every failure is reported as :class:`QueryError`; no other exception escapes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from functools import lru_cache

from .errors import QueryError

__all__ = ["SCALARS", "AGGREGATES", "like_match", "compare", "order_cmp"]

_BOOLEAN = "boolean"
_NUMBER = "number"
_STRING = "string"


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return _BOOLEAN
    if isinstance(value, (int, float)):
        return _NUMBER
    if isinstance(value, str):
        return _STRING
    raise QueryError("cannot compare value of type " + type(value).__name__)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _single(name: str, args: tuple[object, ...]) -> object:
    if len(args) != 1:
        raise QueryError(
            name + " takes exactly 1 argument, got " + str(len(args))
        )
    return args[0]


def _sequence(name: str, values: object) -> tuple[object, ...]:
    try:
        return tuple(values)  # type: ignore[call-overload]
    except TypeError as exc:
        raise QueryError(name + " requires a sequence of values") from exc


def _upper(*args: object) -> str | None:
    value = _single("UPPER", args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("UPPER requires a string argument")
    return value.upper()


def _lower(*args: object) -> str | None:
    value = _single("LOWER", args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LOWER requires a string argument")
    return value.lower()


def _length(*args: object) -> int | None:
    value = _single("LENGTH", args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LENGTH requires a string argument")
    return len(value)


def _absolute(*args: object) -> int | float | None:
    value = _single("ABS", args)
    if value is None:
        return None
    if not _is_number(value):
        raise QueryError("ABS requires a numeric argument")
    return abs(value)


def _coalesce(*args: object) -> object:
    if not args:
        raise QueryError("COALESCE requires at least 1 argument")
    for value in args:
        if value is not None:
            return value
    return None


SCALARS: dict[str, Callable[..., object]] = {
    "UPPER": _upper,
    "LOWER": _lower,
    "LENGTH": _length,
    "ABS": _absolute,
    "COALESCE": _coalesce,
}


def _numeric_inputs(name: str, values: object) -> list[int | float]:
    numbers: list[int | float] = []
    for value in _sequence(name, values):
        if value is None:
            continue
        if not _is_number(value):
            raise QueryError(
                name + " requires numeric input, got " + type(value).__name__
            )
        numbers.append(value)
    return numbers


def _count(values: Sequence[object]) -> int:
    counted = 0
    for value in _sequence("COUNT", values):
        if value is not None:
            counted += 1
    return counted


def _sum(values: Sequence[object]) -> int | float | None:
    numbers = _numeric_inputs("SUM", values)
    if not numbers:
        return None
    total: int | float = 0
    for number in numbers:
        total = total + number
    return total


def _average(values: Sequence[object]) -> float | None:
    numbers = _numeric_inputs("AVG", values)
    if not numbers:
        return None
    total: int | float = 0
    for number in numbers:
        total = total + number
    return float(total) / len(numbers)


def _extreme(name: str, values: Sequence[object], want_greater: bool) -> object:
    best: object = None
    have_best = False
    for value in _sequence(name, values):
        if value is None:
            continue
        if not have_best:
            best = value
            have_best = True
            continue
        ordering = order_cmp(value, best)
        if ordering > 0 if want_greater else ordering < 0:
            best = value
    return best


def _minimum(values: Sequence[object]) -> object:
    return _extreme("MIN", values, False)


def _maximum(values: Sequence[object]) -> object:
    return _extreme("MAX", values, True)


AGGREGATES: dict[str, Callable[[Sequence[object]], object]] = {
    "COUNT": _count,
    "SUM": _sum,
    "AVG": _average,
    "MIN": _minimum,
    "MAX": _maximum,
}


@lru_cache(maxsize=512)
def _compiled_like(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    for character in pattern:
        if character == "%":
            parts.append(".*")
        elif character == "_":
            parts.append(".")
        else:
            parts.append(re.escape(character))
    return re.compile("".join(parts), re.DOTALL)


def like_match(value: object, pattern: object) -> bool | None:
    if value is None or pattern is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            "LIKE requires a string value, got " + type(value).__name__
        )
    if not isinstance(pattern, str):
        raise QueryError(
            "LIKE requires a string pattern, got " + type(pattern).__name__
        )
    return _compiled_like(pattern).fullmatch(value) is not None


_ORDER_TESTS: dict[str, Callable[[int], bool]] = {
    "=": lambda ordering: ordering == 0,
    "<>": lambda ordering: ordering != 0,
    "!=": lambda ordering: ordering != 0,
    "<": lambda ordering: ordering < 0,
    "<=": lambda ordering: ordering <= 0,
    ">": lambda ordering: ordering > 0,
    ">=": lambda ordering: ordering >= 0,
}


def _ordering(left: object, right: object) -> int:
    left_kind = _kind(left)
    right_kind = _kind(right)
    if left_kind != right_kind:
        raise QueryError(
            "cannot compare " + left_kind + " with " + right_kind
        )
    if left < right:  # type: ignore[operator]
        return -1
    if right < left:  # type: ignore[operator]
        return 1
    return 0


def compare(op: str, left: object, right: object) -> bool | None:
    if not isinstance(op, str):
        raise QueryError("unknown comparison operator " + repr(op))
    test = _ORDER_TESTS.get(op)
    if test is None:
        raise QueryError("unknown comparison operator " + repr(op))
    if left is None or right is None:
        return None
    return test(_ordering(left, right))


def order_cmp(left: object, right: object) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    return _ordering(left, right)

```