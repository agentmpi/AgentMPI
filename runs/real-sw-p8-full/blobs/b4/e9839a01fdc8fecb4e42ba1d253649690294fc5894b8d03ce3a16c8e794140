You own `minidb/parser.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

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

### minidb/functions.py (published exports: ["SCALARS", "AGGREGATES", "like_match", "compare"])
```python
'''Scalar functions, aggregate functions, LIKE matching and value comparison.

SQL NULL is Python None throughout. Every failure raised from this module is a
QueryError; no other exception type is allowed to escape.
'''

from __future__ import annotations

import re
from typing import Any, Callable

from .errors import QueryError

_NUMBER = 'number'
_STRING = 'string'
_BOOLEAN = 'boolean'


def _value_class(value: Any) -> str | None:
    '''Return the comparison class of a value, or None if it has none.'''
    if isinstance(value, bool):
        return _BOOLEAN
    if isinstance(value, (int, float)):
        return _NUMBER
    if isinstance(value, str):
        return _STRING
    return None


def _type_name(value: Any) -> str:
    return type(value).__name__


def compare(a: Any, b: Any) -> int | None:
    '''Three-way comparison: -1, 0 or 1, or None when either side is NULL.

    Raises QueryError when the two values are not of the same comparison class,
    which is what makes a mixed-type comparison a query error.
    '''
    if a is None or b is None:
        return None
    class_a = _value_class(a)
    class_b = _value_class(b)
    if class_a is None or class_b is None or class_a != class_b:
        raise QueryError(
            'cannot compare ' + _type_name(a) + ' with ' + _type_name(b)
        )
    try:
        if a < b:
            return -1
        if b < a:
            return 1
    except TypeError as exc:
        raise QueryError(
            'cannot compare ' + _type_name(a) + ' with ' + _type_name(b)
        ) from exc
    return 0


_LIKE_CACHE: dict[str, re.Pattern[str]] = {}


def _like_pattern(pattern: str) -> re.Pattern[str]:
    '''Compile a SQL LIKE pattern, caching the result.'''
    compiled = _LIKE_CACHE.get(pattern)
    if compiled is not None:
        return compiled
    parts: list[str] = []
    for char in pattern:
        if char == '%':
            parts.append('.*')
        elif char == '_':
            parts.append('.')
        else:
            parts.append(re.escape(char))
    try:
        compiled = re.compile(''.join(parts), re.DOTALL)
    except re.error as exc:
        raise QueryError('invalid LIKE pattern: ' + pattern) from exc
    _LIKE_CACHE[pattern] = compiled
    return compiled


def like_match(value: Any, pattern: Any) -> bool | None:
    '''SQL LIKE: %% matches any run of characters, _ matches exactly one.

    Returns None (unknown) when either operand is NULL. Raises QueryError when
    either operand is present but is not a string.
    '''
    if value is None or pattern is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'LIKE requires a string value, got ' + _type_name(value)
        )
    if not isinstance(pattern, str):
        raise QueryError(
            'LIKE requires a string pattern, got ' + _type_name(pattern)
        )
    return _like_pattern(pattern).fullmatch(value) is not None


def _single_argument(name: str, args: tuple[Any, ...]) -> Any:
    if len(args) != 1:
        raise QueryError(
            name + ' takes exactly one argument, got ' + str(len(args))
        )
    return args[0]


def _upper(*args: Any) -> str | None:
    value = _single_argument('UPPER', args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'UPPER requires a string argument, got ' + _type_name(value)
        )
    return value.upper()


def _lower(*args: Any) -> str | None:
    value = _single_argument('LOWER', args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'LOWER requires a string argument, got ' + _type_name(value)
        )
    return value.lower()


def _length(*args: Any) -> int | None:
    value = _single_argument('LENGTH', args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'LENGTH requires a string argument, got ' + _type_name(value)
        )
    return len(value)


def _abs(*args: Any) -> int | float | None:
    value = _single_argument('ABS', args)
    if value is None:
        return None
    if _value_class(value) != _NUMBER:
        raise QueryError(
            'ABS requires a numeric argument, got ' + _type_name(value)
        )
    return abs(value)


def _coalesce(*args: Any) -> Any:
    if not args:
        raise QueryError('COALESCE requires at least one argument')
    for value in args:
        if value is not None:
            return value
    return None


def _numeric_values(name: str, values: list[Any]) -> list[int | float]:
    numbers: list[int | float] = []
    for value in values:
        if value is None:
            continue
        if _value_class(value) != _NUMBER:
            raise QueryError(
                name + ' requires numeric values, got ' + _type_name(value)
            )
        numbers.append(value)
    return numbers


def _count(values: list[Any]) -> int:
    total = 0
    for value in values:
        if value is not None:
            total += 1
    return total


def _sum(values: list[Any]) -> int | float | None:
    numbers = _numeric_values('SUM', values)
    if not numbers:
        return None
    total = numbers[0]
    for value in numbers[1:]:
        total = total + value
    return total


def _avg(values: list[Any]) -> float | None:
    numbers = _numeric_values('AVG', values)
    if not numbers:
        return None
    total = 0.0
    for value in numbers:
        total += value
    return total / len(numbers)


def _extreme(name: str, values: list[Any], wanted: int) -> Any:
    '''Return the non-NULL value whose comparison against the running best is
    `wanted`, or None when there is no non-NULL value.'''
    best: Any = None
    seen = False
    for value in values:
        if value is None:
            continue
        if not seen:
            best = value
            seen = True
            continue
        order = compare(value, best)
        if order == wanted:
            best = value
    if not seen:
        return None
    return best


def _min(values: list[Any]) -> Any:
    return _extreme('MIN', values, -1)


def _max(values: list[Any]) -> Any:
    return _extreme('MAX', values, 1)


SCALARS: dict[str, Callable[..., Any]] = {
    'UPPER': _upper,
    'LOWER': _lower,
    'LENGTH': _length,
    'ABS': _abs,
    'COALESCE': _coalesce,
}

AGGREGATES: dict[str, Callable[[list[Any]], Any]] = {
    'COUNT': _count,
    'SUM': _sum,
    'AVG': _avg,
    'MIN': _min,
    'MAX': _max,
}

__all__ = ['SCALARS', 'AGGREGATES', 'like_match', 'compare']

```