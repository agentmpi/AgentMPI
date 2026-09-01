"""AST dataclasses for the minidb SQL engine.

Every node is a frozen dataclass holding pure data: no validation, no name
resolution and no evaluation happen here, so constructing a node never raises.
All child containers are tuples so that nodes are hashable and can be used as
dictionary keys by the planner.

Field names are the contract other modules read (`Column.name`/`Column.table`,
`Literal.value`, `BinOp.op`/`left`/`right`, `UnaryOp.op`/`operand`,
`Func.name`/`args`, `Star.table`, `SelectItem.expr`/`alias`/`source_text`,
`TableRef.name`/`alias`, `Join.table`/`condition`/`kind`,
`OrderKey.expr`/`descending` and the `Select` clause fields); read them
directly rather than probing for alternative spellings.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Expr",
    "Column",
    "Literal",
    "Star",
    "BinOp",
    "UnaryOp",
    "Func",
    "SelectItem",
    "TableRef",
    "Join",
    "OrderKey",
    "Select",
]


@dataclass(frozen=True)
class Column:
    """A column reference: ``col`` (table is None) or ``table.col``/``alias.col``.

    ``table`` holds the qualifier exactly as written; identifiers are
    case-sensitive.
    """

    name: str
    table: str | None = None


@dataclass(frozen=True)
class Literal:
    """A literal value, already decoded by the parser.

    SQL ``NULL`` is Python ``None`` and a doubled quote inside a string literal
    has already been unescaped to a single quote.
    """

    value: int | float | str | None


@dataclass(frozen=True)
class Star:
    """``*`` (table is None) or ``table.*`` (table is the written name or alias)."""

    table: str | None = None


@dataclass(frozen=True)
class BinOp:
    """A binary operation.

    ``op`` is one of ``+ - * /``, ``= <> != < <= > >=``, ``AND``, ``OR``,
    ``IN`` or ``LIKE``. ``right`` is a tuple of expressions only when ``op`` is
    ``IN``; for every other operator it is a single expression.
    """

    op: str
    left: Expr
    right: Expr | tuple[Expr, ...]


@dataclass(frozen=True)
class UnaryOp:
    """A unary operation: ``NOT``, ``-``, ``+``, ``IS NULL`` or ``IS NOT NULL``."""

    op: str
    operand: Expr


@dataclass(frozen=True)
class Func:
    """A function call with an upper-cased name; ``COUNT(*)`` is ``Func('COUNT', (Star(),))``.

    Arity and whether the name is known are not checked here.
    """

    name: str
    args: tuple[Expr | Star, ...]


Expr = Column | Literal | BinOp | UnaryOp | Func | Star


@dataclass(frozen=True)
class SelectItem:
    """One entry of the select list.

    ``source_text`` is the exact source slice of ``expr``; the parser must set it
    so that unaliased expressions can be named per the specification.
    """

    expr: Expr | Star
    alias: str | None = None
    source_text: str = ""

    def output_name(self) -> str:
        """The output column name: alias, else a bare column name, else the
        source text with all whitespace removed.

        A star item has no single name and yields ``""``; callers expand it.
        """
        if self.alias is not None:
            return self.alias
        if isinstance(self.expr, Column):
            return self.expr.name
        if isinstance(self.expr, Star):
            return ""
        return "".join(self.source_text.split())


@dataclass(frozen=True)
class TableRef:
    """A table named in ``FROM`` or ``JOIN``, with an optional alias."""

    name: str
    alias: str | None = None

    def ref_name(self) -> str:
        """The name by which expressions refer to this table."""
        if self.alias is not None:
            return self.alias
        return self.name


@dataclass(frozen=True)
class Join:
    """One ``INNER JOIN`` clause together with its ``ON`` condition.

    ``condition`` is never None: the grammar requires ``ON`` for every join.
    """

    table: TableRef
    condition: Expr
    kind: str = "INNER"


@dataclass(frozen=True)
class OrderKey:
    """One ``ORDER BY`` key; ``descending`` is True only for ``DESC``."""

    expr: Expr
    descending: bool = False


@dataclass(frozen=True)
class Select:
    """A whole parsed statement; unwritten clauses are None or empty tuples.

    ``limit`` and ``offset`` are stored as parsed, without sign checking.
    """

    items: tuple[SelectItem, ...]
    from_table: TableRef
    distinct: bool = False
    joins: tuple[Join, ...] = ()
    where: Expr | None = None
    group_by: tuple[Expr, ...] = ()
    having: Expr | None = None
    order_by: tuple[OrderKey, ...] = ()
    limit: int | None = None
    offset: int | None = None
