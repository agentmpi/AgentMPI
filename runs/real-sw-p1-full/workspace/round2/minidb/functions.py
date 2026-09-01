"""Scalar functions, aggregate functions, comparison and LIKE matching.

``SCALARS`` maps an upper-case function name to a callable taking the list of
already-evaluated argument values and returning the result value.
``AGGREGATES`` maps an upper-case aggregate name to a callable taking the list
of per-row input values (NULLs included) and returning the aggregate value.
Every callable raises :class:`~minidb.errors.QueryError` on bad arity or on
values of a type it cannot handle.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .errors import QueryError

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, complex)


def compare(a: Any, b: Any) -> int:
    """Three-way comparison returning ``-1``, ``0`` or ``1``.

    ``None`` sorts before every non-NULL value.  Comparing values of
    incompatible types raises :class:`~minidb.errors.QueryError`.
    """
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    if _is_number(a) and _is_number(b):
        if a < b:
            return -1
        if a > b:
            return 1
        return 0
    if isinstance(a, str) and isinstance(b, str):
        if a < b:
            return -1
        if a > b:
            return 1
        return 0
    raise QueryError(
        f"cannot compare {type(a).__name__} with {type(b).__name__}"
    )


def like_match(value: Any, pattern: Any) -> bool | None:
    """Match ``value`` against a SQL ``LIKE`` ``pattern``.

    ``%`` matches any run of characters and ``_`` matches exactly one.
    Returns ``None`` (unknown) when either side is NULL, and raises
    :class:`~minidb.errors.QueryError` when either side is not text.
    """
    if value is None or pattern is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LIKE requires a text value")
    if not isinstance(pattern, str):
        raise QueryError("LIKE requires a text pattern")
    return _compile_like(pattern).fullmatch(value) is not None


def _compile_like(pattern: str) -> re.Pattern[str]:
    cached = _PATTERN_CACHE.get(pattern)
    if cached is not None:
        return cached
    parts: list[str] = []
    for ch in pattern:
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    compiled = re.compile("".join(parts), re.DOTALL)
    _PATTERN_CACHE[pattern] = compiled
    return compiled


def _arity(name: str, args: list, low: int, high: int | None) -> None:
    count = len(args)
    if count < low or (high is not None and count > high):
        if high == low:
            want = f"exactly {low}"
        elif high is None:
            want = f"at least {low}"
        else:
            want = f"between {low} and {high}"
        raise QueryError(f"{name} takes {want} argument(s), got {count}")


def _upper(args: list) -> Any:
    _arity("UPPER", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("UPPER requires a text value")
    return value.upper()


def _lower(args: list) -> Any:
    _arity("LOWER", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LOWER requires a text value")
    return value.lower()


def _length(args: list) -> Any:
    _arity("LENGTH", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError("LENGTH requires a text value")
    return len(value)


def _abs(args: list) -> Any:
    _arity("ABS", args, 1, 1)
    value = args[0]
    if value is None:
        return None
    if not _is_number(value) or isinstance(value, bool):
        raise QueryError("ABS requires a numeric value")
    return abs(value)


def _coalesce(args: list) -> Any:
    _arity("COALESCE", args, 1, None)
    for value in args:
        if value is not None:
            return value
    return None


def _numeric_values(name: str, values: list) -> list:
    out = []
    for value in values:
        if value is None:
            continue
        if not _is_number(value) or isinstance(value, bool):
            raise QueryError(f"{name} requires numeric values")
        out.append(value)
    return out


def _count(values: list) -> int:
    return sum(1 for value in values if value is not None)


def _sum(values: list) -> Any:
    present = _numeric_values("SUM", values)
    if not present:
        return None
    total = present[0]
    for value in present[1:]:
        total = total + value
    return total


def _avg(values: list) -> Any:
    present = _numeric_values("AVG", values)
    if not present:
        return None
    total = 0.0
    for value in present:
        total += value
    return total / len(present)


def _min(values: list) -> Any:
    best: Any = None
    seen = False
    for value in values:
        if value is None:
            continue
        if not seen:
            best = value
            seen = True
        elif compare(value, best) < 0:
            best = value
    return best if seen else None


def _max(values: list) -> Any:
    best: Any = None
    seen = False
    for value in values:
        if value is None:
            continue
        if not seen:
            best = value
            seen = True
        elif compare(value, best) > 0:
            best = value
    return best if seen else None


SCALARS: dict[str, Callable[[list], Any]] = {
    "UPPER": _upper,
    "LOWER": _lower,
    "LENGTH": _length,
    "ABS": _abs,
    "COALESCE": _coalesce,
}

AGGREGATES: dict[str, Callable[[list], Any]] = {
    "COUNT": _count,
    "SUM": _sum,
    "AVG": _avg,
    "MIN": _min,
    "MAX": _max,
}
