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
