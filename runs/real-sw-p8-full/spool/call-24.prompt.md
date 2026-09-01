You own `minidb/functions.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/tokens.py (published exports: ["Token", "tokenize"])
```python
"""Lexical analysis for minidb.

Turns SQL source text into a flat list of `Token` values. No syntactic
validation happens here: clause order, balanced parentheses and expression
structure are the parser's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QueryError

_KEYWORDS = frozenset(
    {
        "SELECT",
        "DISTINCT",
        "FROM",
        "AS",
        "INNER",
        "JOIN",
        "ON",
        "WHERE",
        "GROUP",
        "BY",
        "HAVING",
        "ORDER",
        "ASC",
        "DESC",
        "LIMIT",
        "OFFSET",
        "AND",
        "OR",
        "NOT",
        "IS",
        "NULL",
        "IN",
        "LIKE",
    }
)

_TWO_CHAR_OPS = ("<>", "!=", "<=", ">=")
_ONE_CHAR_OPS = frozenset("=<>+-*/")
_PUNCT = frozenset("(),.")
_WHITESPACE = frozenset(" \t\r\n\f\v")
_DIGITS = frozenset("0123456789")


@dataclass(frozen=True)
class Token:
    """One lexical token; `pos` indexes its first character in the source."""

    kind: str
    value: str | int | float | None
    pos: int


def tokenize(sql: str) -> list[Token]:
    """Lex a complete SQL string into its tokens, in source order."""
    if not isinstance(sql, str):
        raise QueryError(f"query must be a string, not {type(sql).__name__}")

    tokens: list[Token] = []
    i = 0
    length = len(sql)
    while i < length:
        start = i
        ch = sql[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == "'":
            text, i = _read_string(sql, start)
            tokens.append(Token("STRING", text, start))
            continue
        if ch in _DIGITS:
            number, i = _read_number(sql, start)
            tokens.append(Token("NUMBER", number, start))
            continue
        if ch == "_" or ch.isalpha():
            word, i = _read_word(sql, start)
            upper = word.upper()
            if upper in _KEYWORDS:
                tokens.append(Token("KEYWORD", upper, start))
            else:
                tokens.append(Token("IDENT", word, start))
            continue
        if sql[i : i + 2] in _TWO_CHAR_OPS:
            tokens.append(Token("OP", sql[i : i + 2], start))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(Token("OP", ch, start))
            i += 1
            continue
        if ch in _PUNCT:
            tokens.append(Token("PUNCT", ch, start))
            i += 1
            continue
        raise QueryError(f"unexpected character {ch!r} at position {start}")
    return tokens


def _read_string(sql: str, start: int) -> tuple[str, int]:
    """Read a single-quoted literal, collapsing each doubled quote to one."""
    parts: list[str] = []
    i = start + 1
    length = len(sql)
    while True:
        if i >= length:
            raise QueryError(f"unterminated string literal at position {start}")
        ch = sql[i]
        if ch == "'":
            if i + 1 < length and sql[i + 1] == "'":
                parts.append("'")
                i += 2
                continue
            return "".join(parts), i + 1
        parts.append(ch)
        i += 1


def _read_number(sql: str, start: int) -> tuple[int | float, int]:
    """Read an unsigned integer or float literal."""
    i = start
    length = len(sql)
    is_float = False
    while i < length and sql[i] in _DIGITS:
        i += 1
    if i < length and sql[i] == ".":
        is_float = True
        i += 1
        if i >= length or sql[i] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:i]!r} at position {start}"
            )
        while i < length and sql[i] in _DIGITS:
            i += 1
    if i < length and sql[i] in "eE":
        j = i + 1
        if j < length and sql[j] in "+-":
            j += 1
        if j >= length or sql[j] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:j + 1]!r} at position {start}"
            )
        is_float = True
        i = j
        while i < length and sql[i] in _DIGITS:
            i += 1
    if i < length and (sql[i] == "." or sql[i] == "_" or sql[i].isalnum()):
        raise QueryError(
            f"malformed number literal {sql[start:i + 1]!r} at position {start}"
        )
    text = sql[start:i]
    try:
        return (float(text) if is_float else int(text)), i
    except ValueError as exc:
        raise QueryError(
            f"malformed number literal {text!r} at position {start}"
        ) from exc


def _read_word(sql: str, start: int) -> tuple[str, int]:
    """Read one identifier or keyword word."""
    i = start + 1
    length = len(sql)
    while i < length and (sql[i] == "_" or sql[i].isalnum()):
        i += 1
    return sql[start:i], i

```

### minidb/nodes.py (published exports: ["Expr", "Column", "Literal", "Star", "BinOp", "UnaryOp", "Func", "SelectItem", "SelectItem.output_name", "TableRef", "TableRef.ref_name", "Join", "OrderKey", "Select"])
```python
"""AST dataclasses for the minidb SQL engine.

Every node is a frozen dataclass holding pure data: no validation, no name
resolution and no evaluation happen here, so constructing a node never raises.
All child containers are tuples so that nodes are hashable and can be used as
dictionary keys by the planner.
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
    """One ``INNER JOIN`` clause together with its ``ON`` condition."""

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

```