You own `minidb/planner.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

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