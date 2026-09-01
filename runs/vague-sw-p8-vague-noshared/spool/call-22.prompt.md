You own `minidb/functions.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/tokens.py (published exports: ["KEYWORDS", "Token", "Token.is_keyword", "tokenize"])
```python
"""Lexical analysis for minidb: turning SQL text into a sequence of tokens."""

from __future__ import annotations

import dataclasses

from .errors import QueryError

__all__ = ["KEYWORDS", "Token", "tokenize"]


KEYWORDS: frozenset[str] = frozenset(
    {
        "SELECT",
        "DISTINCT",
        "FROM",
        "INNER",
        "JOIN",
        "ON",
        "WHERE",
        "GROUP",
        "HAVING",
        "ORDER",
        "BY",
        "ASC",
        "DESC",
        "LIMIT",
        "OFFSET",
        "AS",
        "AND",
        "OR",
        "NOT",
        "IS",
        "IN",
        "LIKE",
    }
)

_WHITESPACE: frozenset[str] = frozenset(" \t\n\r\f")

_DIGITS: frozenset[str] = frozenset("0123456789")

# Longest match wins, so these are tried before the single-character operators.
# '!=' is normalised to the canonical value '<>'.
_TWO_CHAR_OPS: dict[str, str] = {"<>": "<>", "!=": "<>", "<=": "<=", ">=": ">="}

_ONE_CHAR_OPS: dict[str, str] = {
    "=": "=",
    "<": "<",
    ">": ">",
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
}

_PUNCT: frozenset[str] = frozenset("(),.")


@dataclasses.dataclass(frozen=True, slots=True)
class Token:
    """One lexical token of a SQL query.

    `kind` is one of 'KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'NULL', 'OP',
    'PUNCT', 'EOF'; `value` is the semantic value of the token; `text` is the
    exact source text ('' for 'EOF'); `pos` is the 0-based index in the query
    text where the token starts (len(sql) for 'EOF').
    """

    kind: str
    value: object
    text: str
    pos: int

    def is_keyword(self, *names: str) -> bool:
        """True for a KEYWORD token matching any of `names`, case-insensitively.

        With no names given, true for any KEYWORD token. Never raises.
        """
        if self.kind != "KEYWORD":
            return False
        if not names:
            return True
        value = self.value
        if not isinstance(value, str):
            return False
        upper = value.upper()
        for name in names:
            if isinstance(name, str) and upper == name.upper():
                return True
        return False


def _is_word_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_word_char(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


def _scan_word(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start
    while i < n and _is_word_char(sql[i]):
        i += 1
    text = sql[start:i]
    upper = text.upper()
    if upper == "NULL":
        return Token("NULL", None, text, start), i
    if upper in KEYWORDS:
        return Token("KEYWORD", upper, text, start), i
    return Token("IDENT", text, text, start), i


def _scan_string(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start + 1
    chunks: list[str] = []
    while i < n:
        ch = sql[i]
        if ch == "'":
            if i + 1 < n and sql[i + 1] == "'":
                chunks.append("'")
                i += 2
                continue
            i += 1
            return Token("STRING", "".join(chunks), sql[start:i], start), i
        chunks.append(ch)
        i += 1
    raise QueryError(
        f"unterminated string literal starting with {sql[start]!r} "
        f"at position {start}"
    )


def _scan_number(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start
    while i < n and sql[i] in _DIGITS:
        i += 1
    is_float = False
    if i < n and sql[i] == ".":
        if i + 1 >= n or sql[i + 1] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:i + 1]!r}: "
                f"expected a digit after {'.'!r} at position {i + 1}"
            )
        is_float = True
        i += 1
        while i < n and sql[i] in _DIGITS:
            i += 1
    if i < n and sql[i] in "eE":
        j = i + 1
        if j < n and sql[j] in "+-":
            j += 1
        if j >= n or sql[j] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:j + 1]!r}: "
                f"expected a digit in the exponent at position {j}"
            )
        is_float = True
        i = j
        while i < n and sql[i] in _DIGITS:
            i += 1
    if i < n and (sql[i] == "." or sql[i] in _DIGITS or _is_word_start(sql[i])):
        raise QueryError(
            f"malformed number literal {sql[start:i + 1]!r}: unexpected "
            f"{sql[i]!r} at position {i}"
        )
    text = sql[start:i]
    value: object = float(text) if is_float else int(text)
    return Token("NUMBER", value, text, start), i


def tokenize(sql: str) -> list[Token]:
    """Lex `sql` into tokens, terminated by exactly one 'EOF' token.

    Whitespace separates tokens and is discarded. Raises QueryError for an
    unterminated string literal, a malformed number, a character that cannot
    begin a token, or a non-string argument.
    """
    if not isinstance(sql, str):
        raise QueryError(
            f"query text must be a string, not {type(sql).__name__}"
        )
    tokens: list[Token] = []
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == "'":
            token, i = _scan_string(sql, i)
            tokens.append(token)
            continue
        if ch in _DIGITS:
            token, i = _scan_number(sql, i)
            tokens.append(token)
            continue
        if _is_word_start(ch):
            token, i = _scan_word(sql, i)
            tokens.append(token)
            continue
        pair = sql[i : i + 2]
        if len(pair) == 2 and pair in _TWO_CHAR_OPS:
            tokens.append(Token("OP", _TWO_CHAR_OPS[pair], pair, i))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(Token("OP", _ONE_CHAR_OPS[ch], ch, i))
            i += 1
            continue
        if ch in _PUNCT:
            tokens.append(Token("PUNCT", ch, ch, i))
            i += 1
            continue
        raise QueryError(f"unexpected character {ch!r} at position {i}")
    tokens.append(Token("EOF", None, "", n))
    return tokens

```

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