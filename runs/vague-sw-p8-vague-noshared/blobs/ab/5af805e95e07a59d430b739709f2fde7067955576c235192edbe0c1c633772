You own `minidb/engine.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/parser.py (published exports: ["parse", "Select", "SelectItem", "TableRef", "JoinClause", "OrderItem", "Literal", "ColumnRef", "Star", "UnaryOp", "BinaryOp", "FuncCall", "IsNull", "InList", "Like", "Expr", "KEYWORDS", "AGGREGATE_FUNCTIONS", "SCALAR_FUNCTIONS", "__all__"])
```python
"""SQL text -> syntax tree.

No interface was published for `minidb.tokens` or `minidb.nodes`, so this module
scans its own tokens and defines its own node types rather than guessing at the
names those modules export. Only `minidb.errors.QueryError`, which the
specification itself names, is imported.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QueryError

__all__ = [
    "parse",
    "Select",
    "SelectItem",
    "TableRef",
    "JoinClause",
    "OrderItem",
    "Literal",
    "ColumnRef",
    "Star",
    "UnaryOp",
    "BinaryOp",
    "FuncCall",
    "IsNull",
    "InList",
    "Like",
    "AGGREGATE_FUNCTIONS",
    "SCALAR_FUNCTIONS",
]

AGGREGATE_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
SCALAR_FUNCTIONS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

KEYWORDS = frozenset(
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

_WHITESPACE = " \t\n\r\f\v"
_TWO_CHAR_OPS = {"<=": "<=", ">=": ">=", "<>": "<>", "!=": "<>"}
_ONE_CHAR_OPS = frozenset({"=", "<", ">", "+", "-", "*", "/"})
_PUNCT = frozenset({"(", ")", ",", "."})
_COMPARISON_OPS = frozenset({"=", "<>", "<", "<=", ">", ">="})
_ADDITIVE_OPS = ("+", "-")
_MULTIPLICATIVE_OPS = ("*", "/")

# COUNT (one argument or '*') and COALESCE (one or more) are handled apart.
_FIXED_ARITY = {
    "UPPER": 1,
    "LOWER": 1,
    "LENGTH": 1,
    "ABS": 1,
    "SUM": 1,
    "AVG": 1,
    "MIN": 1,
    "MAX": 1,
}


# --- syntax tree ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Literal:
    """A literal value; `NULL` is Literal(None), strings are already unescaped."""

    value: int | float | str | None


@dataclass(frozen=True, slots=True)
class ColumnRef:
    """`col` is ColumnRef(None, 'col'); `t.col` is ColumnRef('t', 'col')."""

    table: str | None
    name: str


@dataclass(frozen=True, slots=True)
class Star:
    """`*` is Star(None); `t.*` is Star('t'). Only ever a SelectItem.expr."""

    table: str | None = None


@dataclass(frozen=True, slots=True)
class UnaryOp:
    """op is one of 'NOT', '-', '+'."""

    op: str
    operand: "Expr"


@dataclass(frozen=True, slots=True)
class BinaryOp:
    """op is one of '+', '-', '*', '/', '=', '<>', '<', '<=', '>', '>=', 'AND', 'OR'."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True, slots=True)
class FuncCall:
    """name is upper-cased; COUNT(*) is FuncCall('COUNT', (), True)."""

    name: str
    args: tuple["Expr", ...] = ()
    star: bool = False


@dataclass(frozen=True, slots=True)
class IsNull:
    """`x IS NULL` is negated=False, `x IS NOT NULL` is negated=True."""

    operand: "Expr"
    negated: bool = False


@dataclass(frozen=True, slots=True)
class InList:
    """`x IN (a, b, ...)`; items is never empty."""

    operand: "Expr"
    items: tuple["Expr", ...] = ()


@dataclass(frozen=True, slots=True)
class Like:
    """`x LIKE 'pat'`; pattern is normally Literal(str)."""

    operand: "Expr"
    pattern: "Expr"


Expr = (
    Literal
    | ColumnRef
    | Star
    | UnaryOp
    | BinaryOp
    | FuncCall
    | IsNull
    | InList
    | Like
)


@dataclass(frozen=True, slots=True)
class SelectItem:
    """One select_list entry.

    `source_text` is the entry's expression source with whitespace removed.
    `output_name` is the Output naming result, and is None only for a star item,
    which the engine must expand itself.
    """

    expr: Expr
    alias: str | None = None
    source_text: str = ""
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class TableRef:
    """A FROM/JOIN table with its optional alias."""

    name: str
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class JoinClause:
    """One INNER JOIN; condition is never None."""

    table: TableRef
    condition: Expr


@dataclass(frozen=True, slots=True)
class OrderItem:
    """One ORDER BY key; ASC or absent is ascending=True."""

    expr: Expr
    ascending: bool = True


@dataclass(frozen=True, slots=True)
class Select:
    """A whole parsed query: every clause slot is always present."""

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


# --- tokens --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # KEYWORD, IDENT, NUMBER, STRING, NULL, OP, PUNCT, EOF
    value: object
    text: str
    pos: int

    def is_keyword(self, *names: str) -> bool:
        if self.kind != "KEYWORD":
            return False
        if not names:
            return True
        return any(self.value == name.upper() for name in names)


def _scan_string(sql: str, start: int) -> tuple[str, int]:
    chunks: list[str] = []
    i = start + 1
    length = len(sql)
    while True:
        if i >= length:
            raise QueryError(
                f"unterminated string literal starting at position {start}"
            )
        char = sql[i]
        if char == "'":
            if i + 1 < length and sql[i + 1] == "'":
                chunks.append("'")
                i += 2
                continue
            return "".join(chunks), i + 1
        chunks.append(char)
        i += 1


def _scan_number(sql: str, start: int) -> tuple[int | float, int]:
    length = len(sql)
    i = start
    is_float = False
    while i < length and sql[i].isdigit():
        i += 1
    if i < length and sql[i] == ".":
        is_float = True
        i += 1
        if i >= length or not sql[i].isdigit():
            raise QueryError(f"malformed number at position {start}")
        while i < length and sql[i].isdigit():
            i += 1
    if i < length and sql[i] in "eE":
        is_float = True
        i += 1
        if i < length and sql[i] in "+-":
            i += 1
        if i >= length or not sql[i].isdigit():
            raise QueryError(f"malformed number at position {start}")
        while i < length and sql[i].isdigit():
            i += 1
    text = sql[start:i]
    return (float(text) if is_float else int(text)), i


def _tokenize(sql: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    length = len(sql)
    while i < length:
        char = sql[i]
        if char in _WHITESPACE:
            i += 1
            continue
        if char == "'":
            value, end = _scan_string(sql, i)
            tokens.append(_Token("STRING", value, sql[i:end], i))
            i = end
            continue
        if char.isdigit():
            number, end = _scan_number(sql, i)
            tokens.append(_Token("NUMBER", number, sql[i:end], i))
            i = end
            continue
        if char.isalpha() or char == "_":
            end = i
            while end < length and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            text = sql[i:end]
            upper = text.upper()
            if upper == "NULL":
                tokens.append(_Token("NULL", None, text, i))
            elif upper in KEYWORDS:
                tokens.append(_Token("KEYWORD", upper, text, i))
            else:
                tokens.append(_Token("IDENT", text, text, i))
            i = end
            continue
        pair = sql[i : i + 2]
        if pair in _TWO_CHAR_OPS:
            tokens.append(_Token("OP", _TWO_CHAR_OPS[pair], pair, i))
            i += 2
            continue
        if char in _ONE_CHAR_OPS:
            tokens.append(_Token("OP", char, char, i))
            i += 1
            continue
        if char in _PUNCT:
            tokens.append(_Token("PUNCT", char, char, i))
            i += 1
            continue
        raise QueryError(f"unexpected character {char!r} at position {i}")
    tokens.append(_Token("EOF", None, "", length))
    return tokens


# --- parser --------------------------------------------------------------


class _Parser:
    """Recursive-descent parser over one token list."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self, ahead: int = 0) -> _Token:
        index = self._pos + ahead
        if index >= len(self._tokens):
            return self._tokens[-1]
        return self._tokens[index]

    def _advance(self) -> _Token:
        token = self._peek()
        if token.kind != "EOF":
            self._pos += 1
        return token

    def _accept_keyword(self, *names: str) -> _Token | None:
# ... 1 further lines of this file were not included in this review excerpt ...

```

### minidb/planner.py (published exports: ["plan", "Plan", "TableRef", "SelectItem", "OrderItem", "AggregateCall", "Expr", "Literal", "Column", "Unary", "Binary", "Func", "InList", "IsNull", "Like", "AggRef", "AGGREGATE_FUNCTIONS", "SCALAR_FUNCTIONS"])
```python
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
        return tuple(str(key) for key in first)

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
# ... 1 further lines of this file were not included in this review excerpt ...

```