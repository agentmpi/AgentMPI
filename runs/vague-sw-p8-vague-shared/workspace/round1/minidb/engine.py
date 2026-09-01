"""Evaluation of a validated minidb query plan against in-memory tables."""

from __future__ import annotations

from functools import cmp_to_key
from typing import Any, TypeAlias

from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match, order_cmp
from .planner import (
    AggRef,
    Binary,
    Column,
    Func,
    InList,
    IsNull,
    Like,
    Literal,
    Unary,
)

__all__ = ["Row", "Tables", "execute"]

Row: TypeAlias = dict[str, Any]
Tables: TypeAlias = dict[str, list[Row]]

_JoinedRow: TypeAlias = dict[str, Row]

_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})


def execute(plan: Any, tables: Tables) -> list[Row]:
    """Evaluate `plan` against `tables` and return the result rows."""
    try:
        return _execute(plan, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"query evaluation failed: {exc}") from exc


def _execute(plan: Any, tables: Tables) -> list[Row]:
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to list of rows")

    rows = _scan(plan, tables)

    if plan.where is not None:
        rows = [row for row in rows if _truth(_eval(plan.where, row, None))]

    if plan.is_aggregate or plan.group_by:
        entries = _grouped_entries(plan, rows)
    else:
        entries = [(_project(plan, row, None), _sort_keys(plan, row, None)) for row in rows]

    if plan.distinct:
        entries = _distinct(entries)
    if plan.order_by:
        entries = _sort(plan, entries)

    return _slice(plan, [row for row, _keys in entries])


def _scan(plan: Any, tables: Tables) -> list[_JoinedRow]:
    refs = tuple(plan.from_tables)
    if not refs:
        return [{}]

    joined: list[_JoinedRow] = [{refs[0].alias: source} for source in _table_rows(refs[0].name, tables)]
    conditions = tuple(plan.join_conditions)

    for index, ref in enumerate(refs[1:]):
        condition = conditions[index] if index < len(conditions) else None
        right = _table_rows(ref.name, tables)
        combined: list[_JoinedRow] = []
        for left in joined:
            for source in right:
                candidate = dict(left)
                candidate[ref.alias] = source
                if condition is None or _truth(_eval(condition, candidate, None)):
                    combined.append(candidate)
        joined = combined

    return joined


def _table_rows(name: str, tables: Tables) -> list[Row]:
    if name not in tables:
        raise QueryError(f"unknown table '{name}'")
    rows = tables[name]
    if isinstance(rows, (str, bytes, dict)) or not isinstance(rows, (list, tuple)):
        raise QueryError(f"table '{name}' must be a list of rows")
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError(f"table '{name}' contains a row that is not a mapping")
    return list(rows)


def _grouped_entries(plan: Any, rows: list[_JoinedRow]) -> list[tuple[Row, tuple[Any, ...]]]:
    entries: list[tuple[Row, tuple[Any, ...]]] = []
    for context, members in _group(plan, rows):
        values = _aggregate_values(plan, members)
        if plan.having is not None and not _truth(_eval(plan.having, context, values)):
            continue
        entries.append((_project(plan, context, values), _sort_keys(plan, context, values)))
    return entries


def _group(plan: Any, rows: list[_JoinedRow]) -> list[tuple[_JoinedRow, list[_JoinedRow]]]:
    group_by = tuple(plan.group_by)
    if not group_by:
        return [(rows[0] if rows else {}, rows)]

    buckets: dict[tuple[Any, ...], tuple[_JoinedRow, list[_JoinedRow]]] = {}
    for row in rows:
        key = tuple(_value_key(_eval(expr, row, None)) for expr in group_by)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = (row, [row])
        else:
            bucket[1].append(row)
    return list(buckets.values())


def _aggregate_values(plan: Any, members: list[_JoinedRow]) -> list[Any]:
    computed: list[Any] = []
    for call in plan.aggregates:
        name = call.func.upper() if isinstance(call.func, str) else ""
        aggregate = AGGREGATES.get(name)
        if aggregate is None:
            raise QueryError(f"unknown aggregate function '{call.func}'")
        if call.arg is None:
            inputs: list[Any] = [1] * len(members)
        else:
            inputs = [_eval(call.arg, row, None) for row in members]
        computed.append(aggregate(inputs))
    return computed


def _project(plan: Any, context: _JoinedRow, agg_values: list[Any] | None) -> Row:
    return {item.name: _eval(item.expr, context, agg_values) for item in plan.select}


def _sort_keys(plan: Any, context: _JoinedRow, agg_values: list[Any] | None) -> tuple[Any, ...]:
    return tuple(_eval(item.expr, context, agg_values) for item in plan.order_by)


def _eval(expr: Any, context: _JoinedRow, agg_values: list[Any] | None) -> Any:
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Column):
        source = context.get(expr.alias)
        if not isinstance(source, dict):
            return None
        return source.get(expr.name)
    if isinstance(expr, AggRef):
        if agg_values is None:
            raise QueryError("aggregate function is not allowed here")
        if not 0 <= expr.index < len(agg_values):
            raise QueryError("aggregate reference out of range")
        return agg_values[expr.index]
    if isinstance(expr, Unary):
        return _eval_unary(expr, context, agg_values)
    if isinstance(expr, Binary):
        return _eval_binary(expr, context, agg_values)
    if isinstance(expr, Func):
        name = expr.name.upper() if isinstance(expr.name, str) else ""
        scalar = SCALARS.get(name)
        if scalar is None:
            raise QueryError(f"unknown function '{expr.name}'")
        return scalar(*[_eval(arg, context, agg_values) for arg in expr.args])
    if isinstance(expr, IsNull):
        is_null = _eval(expr.operand, context, agg_values) is None
        return (not is_null) if expr.negated else is_null
    if isinstance(expr, InList):
        return _eval_in(expr, context, agg_values)
    if isinstance(expr, Like):
        value = _eval(expr.operand, context, agg_values)
        pattern = _eval(expr.pattern, context, agg_values)
        return like_match(value, pattern)
    raise QueryError(f"unsupported expression of type {type(expr).__name__}")


def _eval_unary(expr: Any, context: _JoinedRow, agg_values: list[Any] | None) -> Any:
    op = expr.op.upper() if isinstance(expr.op, str) else expr.op
    value = _eval(expr.operand, context, agg_values)
    if op == "-":
        if value is None:
            return None
        return -_number(value, "unary '-'")
    if op == "NOT":
        return _not(_boolean(value, "NOT"))
    raise QueryError(f"unsupported unary operator '{expr.op}'")


def _eval_binary(expr: Any, context: _JoinedRow, agg_values: list[Any] | None) -> Any:
    op = expr.op.upper() if isinstance(expr.op, str) else expr.op
    if op == "AND":
        return _eval_and(expr, context, agg_values)
    if op == "OR":
        return _eval_or(expr, context, agg_values)

    left = _eval(expr.left, context, agg_values)
    right = _eval(expr.right, context, agg_values)
    if op in _COMPARISONS:
        return compare(op, left, right)
    if op in _ARITHMETIC:
        return _arithmetic(op, left, right)
    raise QueryError(f"unsupported operator '{expr.op}'")


def _eval_and(expr: Any, context: _JoinedRow, agg_values: list[Any] | None) -> bool | None:
    left = _boolean(_eval(expr.left, context, agg_values), "AND")
    if left is False:
        return False
    right = _boolean(_eval(expr.right, context, agg_values), "AND")
    if right is False:
        return False
    if left is None or right is None:
        return None
    return True


def _eval_or(expr: Any, context: _JoinedRow, agg_values: list[Any] | None) -> bool | None:
    left = _boolean(_eval(expr.left, context, agg_values), "OR")
    if left is True:
        return True
    right = _boolean(_eval(expr.right, context, agg_values), "OR")
    if right is True:
        return True
    if left is None or right is None:
        return None
    return False


def _arithmetic(op: str, left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    lhs = _number(left, f"operator '{op}'")
    rhs = _number(right, f"operator '{op}'")
    if op == "+":
        return lhs + rhs
    if op == "-":
        return lhs - rhs
    if op == "*":
        return lhs * rhs
    if rhs == 0:
        return None
    return lhs / rhs


def _eval_in(expr: Any, context: _JoinedRow, agg_values: list[Any] | None) -> bool | None:
    value = _eval(expr.operand, context, agg_values)
    unknown = False
    for item in expr.items:
        outcome = compare("=", value, _eval(item, context, agg_values))
        if outcome is True:
            return True
        if outcome is None:
            unknown = True
    return None if unknown else False


def _number(value: Any, what: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QueryError(f"{what} requires a numeric operand")
    return value


def _boolean(value: Any, what: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise QueryError(f"{what} requires a boolean operand")


def _not(value: bool | None) -> bool | None:
    return None if value is None else not value


def _truth(value: Any) -> bool:
    return _boolean(value, "condition") is True


def _distinct(entries: list[tuple[Row, tuple[Any, ...]]]) -> list[tuple[Row, tuple[Any, ...]]]:
    seen: set[tuple[Any, ...]] = set()
    kept: list[tuple[Row, tuple[Any, ...]]] = []
    for row, keys in entries:
        marker = tuple((name, _value_key(value)) for name, value in row.items())
        if marker in seen:
            continue
        seen.add(marker)
        kept.append((row, keys))
    return kept


def _sort(plan: Any, entries: list[tuple[Row, tuple[Any, ...]]]) -> list[tuple[Row, tuple[Any, ...]]]:
    flags = tuple(bool(item.descending) for item in plan.order_by)

    def compare_keys(left: tuple[Any, ...], right: tuple[Any, ...]) -> int:
        for lhs, rhs, descending in zip(left, right, flags):
            outcome = order_cmp(lhs, rhs)
            if outcome:
                return -outcome if descending else outcome
        return 0

    wrapper = cmp_to_key(compare_keys)
    return sorted(entries, key=lambda entry: wrapper(entry[1]))


def _slice(plan: Any, rows: list[Row]) -> list[Row]:
    offset = plan.offset
    if offset is not None:
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise QueryError("OFFSET must be an integer")
        if offset < 0:
            raise QueryError("OFFSET must not be negative")
        rows = rows[offset:]
    limit = plan.limit
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise QueryError("LIMIT must be an integer")
        if limit < 0:
            raise QueryError("LIMIT must not be negative")
        rows = rows[:limit]
    return rows


def _value_key(value: Any) -> tuple[Any, ...]:
    if value is None:
        return (0, None)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    try:
        hash(value)
    except Exception:
        return (5, repr(value))
    return (4, value)
