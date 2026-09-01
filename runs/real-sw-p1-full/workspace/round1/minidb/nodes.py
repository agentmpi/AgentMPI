"""AST node types for the minidb SQL subset."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Select",
    "TableRef",
    "Join",
    "Column",
    "Literal",
    "BinOp",
    "UnaryOp",
    "Func",
    "Star",
    "OrderKey",
    "SelectItem",
]


@dataclass(frozen=True)
class Star:
    """`*` or `table.*` in a select list, or the argument of `COUNT(*)`."""

    table: str | None = None


@dataclass(frozen=True)
class Column:
    """A column reference, optionally qualified by a table name or alias."""

    table: str | None
    name: str


@dataclass(frozen=True)
class Literal:
    """A number, a string, or NULL."""

    value: object


@dataclass(frozen=True)
class BinOp:
    """A binary operation.

    `right` is a tuple of expressions when `op` is `IN`, and a single
    expression for every other operator.
    """

    op: str
    left: object
    right: object


@dataclass(frozen=True)
class UnaryOp:
    """`NOT x`, `-x`, `x IS NULL` or `x IS NOT NULL`."""

    op: str
    operand: object


@dataclass(frozen=True)
class Func:
    """A scalar or aggregate call; `name` is upper-cased by the parser."""

    name: str
    args: tuple = ()


@dataclass(frozen=True)
class TableRef:
    """A table in FROM or JOIN, with its optional alias."""

    name: str
    alias: str | None = None


@dataclass(frozen=True)
class Join:
    """An INNER JOIN and its ON condition."""

    table: TableRef
    on: object
    kind: str = "INNER"


@dataclass(frozen=True)
class SelectItem:
    """One entry of the select list.

    `source_text` is the source of the expression with all whitespace removed,
    which is the output name of an unaliased non-column expression.
    """

    expr: object
    alias: str | None = None
    source_text: str = ""


@dataclass(frozen=True)
class OrderKey:
    """One ORDER BY key and its direction."""

    expr: object
    descending: bool = False
    source_text: str = ""


@dataclass(frozen=True)
class Select:
    """A complete SELECT statement."""

    distinct: bool = False
    items: tuple = ()
    from_: TableRef | None = None
    joins: tuple = ()
    where: object = None
    group_by: tuple = ()
    having: object = None
    order_by: tuple = ()
    limit: object = None
    offset: object = None
