"""Scalar functions, aggregate functions and value comparison for minidb."""

from __future__ import annotations

import re

from .errors import QueryError

__all__ = ["SCALARS", "AGGREGATES", "like_match", "compare"]

_LIKE_CACHE: dict[str, re.Pattern] = {}


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compare(a: object, b: object) -> int | None:
    """Return -1, 0 or 1, or None when either side is NULL.

    Comparing values of different kinds (a number with a string, say) is a
    type error and raises QueryError.
    """
    if a is None or b is None:
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        if not (isinstance(a, bool) and isinstance(b, bool)):
            raise QueryError(
                f"cannot compare {type(a).__name__} with {type(b).__name__}"
            )
        return 0 if a == b else (-1 if b else 1)
    if _is_number(a) and _is_number(b):
        if a == b:
            return 0
        return -1 if a < b else 1
    if isinstance(a, str) and isinstance(b, str):
        if a == b:
            return 0
        return -1 if a < b else 1
    raise QueryError(f"cannot compare {type(a).__name__} with {type(b).__name__}")


def like_match(value: object, pattern: object) -> bool | None:
    """Match `value` against a LIKE `pattern`; None if either side is NULL."""
    if value is None or pattern is None:
        return None
    if not isinstance(pattern, str):
        raise QueryError("LIKE pattern must be a string")
    if not isinstance(value, str):
        raise QueryError("LIKE requires a string value")
    return _like_regex(pattern).fullmatch(value) is not None


def _like_regex(pattern: str) -> re.Pattern:
    compiled = _LIKE_CACHE.get(pattern)
    if compiled is None:
        parts = []
        for char in pattern:
            if char == "%":
                parts.append(".*")
            elif char == "_":
                parts.append(".")
            else:
                parts.append(re.escape(char))
        compiled = re.compile("".join(parts), re.DOTALL)
        _LIKE_CACHE[pattern] = compiled
    return compiled


def _arity(name: str, args: list, low: int, high: int | None) -> None:
    if len(args) < low or (high is not None and len(args) > high):
        raise QueryError(f"{name} called with {len(args)} argument(s)")


def _upper(args: list) -> object:
    _arity("UPPER", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("UPPER requires a string argument")
    return value.upper()


def _lower(args: list) -> object:
    _arity("LOWER", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LOWER requires a string argument")
    return value.lower()


def _length(args: list) -> object:
    _arity("LENGTH", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LENGTH requires a string argument")
    return len(value)


def _abs(args: list) -> object:
    _arity("ABS", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not _is_number(value):
        raise QueryError("ABS requires a numeric argument")
    return abs(value)


def _coalesce(args: list) -> object:
    _arity("COALESCE", args, 1, None)
    for value in args:
        if value is not None:
            return value
    return None


def _count(values: list) -> int:
    return sum(1 for value in values if value is not None)


def _present(name: str, values: list) -> list:
    return [value for value in values if value is not None]


def _sum(values: list) -> object:
    present = _present("SUM", values)
    if not present:
        return None
    total: object = 0
    for value in present:
        if not _is_number(value):
            raise QueryError("SUM requires numeric values")
        total = total + value
    return total


def _avg(values: list) -> object:
    present = _present("AVG", values)
    if not present:
        return None
    total = 0.0
    for value in present:
        if not _is_number(value):
            raise QueryError("AVG requires numeric values")
        total += value
    return total / len(present)


def _min(values: list) -> object:
    present = _present("MIN", values)
    if not present:
        return None
    best = present[0]
    for value in present[1:]:
        if compare(value, best) == -1:
            best = value
    return best


def _max(values: list) -> object:
    present = _present("MAX", values)
    if not present:
        return None
    best = present[0]
    for value in present[1:]:
        if compare(value, best) == 1:
            best = value
    return best


SCALARS = {
    "UPPER": _upper,
    "LOWER": _lower,
    "LENGTH": _length,
    "ABS": _abs,
    "COALESCE": _coalesce,
}

AGGREGATES = {
    "COUNT": _count,
    "SUM": _sum,
    "AVG": _avg,
    "MIN": _min,
    "MAX": _max,
}
