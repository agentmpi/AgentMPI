"""Execution of a resolved :class:`~minidb.planner.Plan`."""

from __future__ import annotations

from functools import cmp_to_key
from typing import Any

from . import planner
from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match

_EMPTY_ROW: dict = {}


def execute(plan: Any, tables: Any) -> list[dict]:
    """Run ``plan`` against ``tables`` and return the result rows.

    Raises :class:`~minidb.errors.QueryError` for type errors met while
    evaluating expressions, comparisons of mixed types, and unhashable values
    used as a ``GROUP BY`` key or under ``DISTINCT``.
    """
    if not isinstance(plan, planner.Plan):
        raise QueryError("invalid plan")
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to rows")

    frames = _build_frames(plan, tables)

    if plan.where is not None:
        frames = [frame for frame in frames if _eval(plan.where, frame) is True]

    records: list[tuple[list, list]] = []
    if plan.grouped:
        for frame, values in _groups(plan, frames):
            if plan.having is not None and _eval(plan.having, frame, values) is not True:
                continue
            records.append(_record(plan, frame, values))
    else:
        for frame in frames:
            records.append(_record(plan, frame, ()))

    if plan.distinct:
        records = _distinct(records)

    if plan.order:
        records = _sort(plan, records)

    start = plan.offset or 0
    if start:
        records = records[start:]
    if plan.limit is not None:
        records = records[: plan.limit]

    rows: list[dict] = []
    for values, _keys in records:
        row: dict = {}
        for name, value in zip(plan.output_names, values):
            row[name] = value
        rows.append(row)
    return rows


def _table_rows(tables: dict, name: str) -> list:
    rows = tables.get(name)
    if rows is None:
        raise QueryError(f"unknown table {name!r}")
    if not isinstance(rows, list):
        raise QueryError(f"table {name!r} must be a list of rows")
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError(f"table {name!r} must contain row dicts")
    return rows


def _build_frames(plan: Any, tables: dict) -> list[list]:
    width = len(plan.sources)
    frames: list[list] = []
    for row in _table_rows(tables, plan.sources[0].name):
        frame = [_EMPTY_ROW] * width
        frame[0] = row
        frames.append(frame)

    for step in plan.joins:
        right_rows = _table_rows(tables, plan.sources[step.source_index].name)
        joined: list[list] = []
        for frame in frames:
            for row in right_rows:
                candidate = list(frame)
                candidate[step.source_index] = row
                if _eval(step.on, candidate) is True:
                    joined.append(candidate)
        frames = joined
    return frames


def _groups(plan: Any, frames: list[list]):
    width = len(plan.sources)
    if not plan.group_exprs:
        empty = [_EMPTY_ROW] * width
        frame = frames[0] if frames else empty
        yield frame, _aggregate(plan, frames)
        return

    ordered: dict = {}
    for frame in frames:
        key = _key([_eval(expr, frame) for expr in plan.group_exprs], "GROUP BY")
        bucket = ordered.get(key)
        if bucket is None:
            ordered[key] = [frame]
        else:
            bucket.append(frame)

    for bucket in ordered.values():
        yield bucket[0], _aggregate(plan, bucket)


def _aggregate(plan: Any, frames: list[list]) -> list:
    values: list = []
    for spec in plan.aggregates:
        function = AGGREGATES.get(spec.name)
        if function is None:
            raise QueryError(f"unknown aggregate function {spec.name}")
        if spec.star or spec.arg is None:
            inputs: list = [1] * len(frames)
        else:
            inputs = [_eval(spec.arg, frame) for frame in frames]
        values.append(function(inputs))
    return values


def _record(plan: Any, frame: list, agg_values: Any) -> tuple[list, list]:
    outputs = [_eval(expr, frame, agg_values) for expr in plan.output_exprs]
    keys = [_eval(term.expr, frame, agg_values, outputs) for term in plan.order]
    return outputs, keys


def _key(values: list, clause: str):
    try:
        candidate = tuple(values)
        hash(candidate)
    except TypeError as exc:
        raise QueryError(f"{clause} cannot use values of this type") from exc
    return candidate


def _distinct(records: list) -> list:
    seen = set()
    out = []
    for record in records:
        key = _key(record[0], "DISTINCT")
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def _sort(plan: Any, records: list) -> list:
    terms = plan.order

    def compare_records(left, right) -> int:
        for position, term in enumerate(terms):
            result = _compare_sort_values(left[1][position], right[1][position])
            if term.desc:
                result = -result
            if result:
                return result
        return 0

    return sorted(records, key=cmp_to_key(compare_records))


def _compare_sort_values(left: Any, right: Any) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    return compare(left, right)


def _eval(expr: Any, frame: list, agg_values: Any = (), outputs: Any = ()) -> Any:
    if isinstance(expr, planner.Const):
        return expr.value
    if isinstance(expr, planner.ColRef):
        if expr.source >= len(frame):
            return None
        return frame[expr.source].get(expr.column)
    if isinstance(expr, planner.AggRef):
        if expr.index >= len(agg_values):
            raise QueryError("aggregate used outside of a grouped context")
        return agg_values[expr.index]
    if isinstance(expr, planner.OutputRef):
        if expr.index >= len(outputs):
            raise QueryError("ORDER BY refers to an unknown output column")
        return outputs[expr.index]
    if isinstance(expr, planner.Arith):
        return _arith(
            expr.op,
            _eval(expr.left, frame, agg_values, outputs),
            _eval(expr.right, frame, agg_values, outputs),
        )
    if isinstance(expr, planner.Negate):
        value = _eval(expr.operand, frame, agg_values, outputs)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError("unary '-' requires a numeric value")
        return -value
    if isinstance(expr, planner.Compare):
        left = _eval(expr.left, frame, agg_values, outputs)
        right = _eval(expr.right, frame, agg_values, outputs)
        if left is None or right is None:
            return None
        return _apply_comparison(expr.op, compare(left, right))
    if isinstance(expr, planner.AndExpr):
        unknown = False
        for part in expr.parts:
            value = _eval(part, frame, agg_values, outputs)
            if value is None:
                unknown = True
            elif value is not True:
                return False
        return None if unknown else True
    if isinstance(expr, planner.OrExpr):
        unknown = False
        for part in expr.parts:
            value = _eval(part, frame, agg_values, outputs)
            if value is None:
                unknown = True
            elif value is True:
                return True
        return None if unknown else False
    if isinstance(expr, planner.NotExpr):
        value = _eval(expr.operand, frame, agg_values, outputs)
        if value is None:
            return None
        return value is not True
    if isinstance(expr, planner.IsNullExpr):
        value = _eval(expr.operand, frame, agg_values, outputs)
        return (value is not None) if expr.negated else (value is None)
    if isinstance(expr, planner.InExpr):
        value = _eval(expr.operand, frame, agg_values, outputs)
        if value is None:
            return None
        unknown = False
        for option in expr.options:
            other = _eval(option, frame, agg_values, outputs)
            if other is None:
                unknown = True
                continue
            try:
                if compare(value, other) == 0:
                    return True
            except QueryError:
                continue
        return None if unknown else False
    if isinstance(expr, planner.LikeExpr):
        return like_match(
            _eval(expr.operand, frame, agg_values, outputs),
            _eval(expr.pattern, frame, agg_values, outputs),
        )
    if isinstance(expr, planner.ScalarCall):
        function = SCALARS.get(expr.name)
        if function is None:
            raise QueryError(f"unknown function {expr.name}")
        return function(
            [_eval(arg, frame, agg_values, outputs) for arg in expr.args]
        )
    raise QueryError("cannot evaluate this expression")


def _apply_comparison(op: str, result: int) -> bool:
    if op == "=":
        return result == 0
    if op in ("<>", "!="):
        return result != 0
    if op == "<":
        return result < 0
    if op == "<=":
        return result <= 0
    if op == ">":
        return result > 0
    if op == ">=":
        return result >= 0
    raise QueryError(f"unsupported comparison {op!r}")


def _arith(op: str, left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    for value in (left, right):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError(f"cannot apply {op!r} to {type(value).__name__}")
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        if right == 0:
            return None
        return left / right
    raise QueryError(f"unsupported operator {op!r}")
