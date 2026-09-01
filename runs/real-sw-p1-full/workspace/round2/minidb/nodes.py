"""AST dataclasses produced by :func:`minidb.parser.parse`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Literal:
    """A constant: integer, float, string, ``None`` for ``NULL``, or bool."""

    value: Any


@dataclass
class Column:
    """A column reference; ``table`` is the qualifier, or ``None`` if bare."""

    table: str | None
    name: str


@dataclass
class Star:
    """``*`` or ``table.*`` in a select list."""

    table: str | None = None


@dataclass
class BinOp:
    """A binary operator.

    ``op`` is one of ``+ - * /``, a comparison (``= <> != < <= > >=``),
    ``AND``, ``OR``, ``IN`` (with ``right`` a list of expressions) or ``LIKE``.
    """

    op: str
    left: Any
    right: Any


@dataclass
class UnaryOp:
    """A prefix/postfix operator: ``-``, ``+``, ``NOT``, ``IS NULL``, ``IS NOT NULL``."""

    op: str
    operand: Any


@dataclass
class Func:
    """A function call; ``star`` is true only for ``COUNT(*)``."""

    name: str
    args: list = field(default_factory=list)
    star: bool = False


@dataclass
class TableRef:
    """A table in ``FROM``/``JOIN`` with an optional alias."""

    name: str
    alias: str | None = None


@dataclass
class Join:
    """An ``INNER JOIN`` of ``table`` on condition ``on``."""

    table: TableRef
    on: Any


@dataclass
class SelectItem:
    """One select-list entry.

    ``text`` is the source text of the expression with whitespace removed; it
    is used for output naming when there is no alias.
    """

    expr: Any
    alias: str | None = None
    text: str = ""


@dataclass
class OrderKey:
    """One ``ORDER BY`` term."""

    expr: Any
    desc: bool = False


@dataclass
class Select:
    """A whole ``SELECT`` statement."""

    items: list
    from_table: TableRef | None = None
    joins: list = field(default_factory=list)
    distinct: bool = False
    where: Any = None
    group_by: list = field(default_factory=list)
    having: Any = None
    order_by: list = field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
