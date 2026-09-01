"""Scalar and aggregate function behaviour, LIKE matching and value comparison.

Every failure is reported as :class:`QueryError`; no other exception escapes.
The published tables have two different call shapes: entries of ``SCALARS`` take
the already-evaluated arguments positionally (``SCALARS["UPPER"](value)``),
while entries of ``AGGREGATES`` take one sequence of per-row values
(``AGGREGATES["SUM"](values)``, never ``*values``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from functools import lru_cache

from .errors import QueryError

__all__ = [
    "SCALARS",
    "AGGREGATES",
    "like_match",
    "compare",
    "order_cmp",
    "count_star",
    "resolve_scalar",
    "resolve_aggregate",
    "arith",
]

_BOOLEAN = "boolean"
_NUMBER = "number"
_STRING = "string"

_ARITHMETIC_OPS = ("+", "-", "*", "/")


def _describe(value: object) -> str:
    if isinstance(value, bool):
        return _BOOLEAN
    if isinstance(value, (int, float)):
        return _NUMBER
    if isinstance(value, str):
        return _STRING
    return type(value).__name__


def _kind(value: object) -> str:
    kind = _describe(value)
    if kind not in (_BOOLEAN, _NUMBER, _STRING):
        raise QueryError("cannot compare value of type " + kind)
    return kind


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _single(name: str, args: tuple[object, ...]) -> object:
    if len(args) != 1:
        raise QueryError(
            name + " takes exactly 1 argument, got " + str(len(args))
        )
    return args[0]


def _sequence(name: str, values: object) -> tuple[object, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise QueryError(
            name + " requires a sequence of per-row values, got "
            + _describe(values)
        )
    try:
        return tuple(values)  # type: ignore[call-overload]
    except TypeError as exc:
        raise QueryError(
            name + " requires a sequence of per-row values, got "
            + _describe(values)
        ) from exc


def _upper(*args: object) -> str | None:
    value = _single("UPPER", args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("UPPER requires a string argument, got " + _describe(value))
    return value.upper()


def _lower(*args: object) -> str | None:
    value = _single("LOWER", args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LOWER requires a string argument, got " + _describe(value))
    return value.lower()


def _length(*args: object) -> int | None:
    value = _single("LENGTH", args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LENGTH requires a string argument, got " + _describe(value))
    return len(value)


def _absolute(*args: object) -> int | float | None:
    value = _single("ABS", args)
    if value is None:
        return None
    if not _is_number(value):
        raise QueryError("ABS requires a numeric argument, got " + _describe(value))
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
                name + " requires numeric input, got " + _describe(value)
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
        if (ordering > 0) if want_greater else (ordering < 0):
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


def count_star(rows: Sequence[object]) -> int:
    """Count every row of a group, NULLs included: the ``COUNT(*)`` form.

    Equivalent to ``AGGREGATES["COUNT"]`` fed one non-None placeholder per row,
    so either spelling is correct. Raises QueryError if `rows` is not a
    sequence; returns 0 for an empty group and never returns None.
    """
    return len(_sequence("COUNT(*)", rows))


def resolve_scalar(name: object) -> Callable[..., object]:
    """The SCALARS entry for `name`, matched case-insensitively.

    Raises QueryError for a non-string name or an unknown scalar function, so a
    caller never has to risk ``KeyError`` from a bare subscript.
    """
    if not isinstance(name, str):
        raise QueryError("function name must be a string, not " + _describe(name))
    found = SCALARS.get(name.upper())
    if found is None:
        raise QueryError("unknown scalar function " + repr(name))
    return found


def resolve_aggregate(name: object) -> Callable[[Sequence[object]], object]:
    """The AGGREGATES entry for `name`, matched case-insensitively.

    Raises QueryError for a non-string name or an unknown aggregate function.
    """
    if not isinstance(name, str):
        raise QueryError("function name must be a string, not " + _describe(name))
    found = AGGREGATES.get(name.upper())
    if found is None:
        raise QueryError("unknown aggregate function " + repr(name))
    return found


def arith(op: str, left: object, right: object) -> object:
    """Evaluate ``left op right`` for op in '+', '-', '*', '/'.

    Returns None when either operand is None and when dividing by zero, so
    ZeroDivisionError can never reach the caller. Raises QueryError for an
    unknown operator and for a non-numeric operand.
    """
    if not isinstance(op, str) or op not in _ARITHMETIC_OPS:
        raise QueryError("unknown arithmetic operator " + repr(op))
    if left is None or right is None:
        return None
    if not _is_number(left) or not _is_number(right):
        raise QueryError(
            "cannot apply " + op + " to " + _describe(left) + " and "
            + _describe(right)
        )
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if right == 0:
        return None
    return left / right


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
        raise QueryError("LIKE requires a string value, got " + _describe(value))
    if not isinstance(pattern, str):
        raise QueryError("LIKE requires a string pattern, got " + _describe(pattern))
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
        raise QueryError("cannot compare " + left_kind + " with " + right_kind)
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
