"""Execution of a planned minidb SELECT statement."""

from __future__ import annotations

from functools import cmp_to_key

from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match
from .planner import Plan

__all__ = ["execute"]

_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_COMPARISONS = {
    "=": lambda outcome: outcome == 0,
    "<>": lambda outcome: outcome != 0,
    "!=": lambda outcome: outcome != 0,
    "<": lambda outcome: outcome < 0,
    "<=": lambda outcome: outcome <= 0,
    ">": lambda outcome: outcome > 0,
    ">=": lambda outcome: outcome >= 0,
}


def execute(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute a planned SELECT against `tables` and return the result rows."""
    try:
        return _run(plan, tables)
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is nested too deeply to evaluate") from exc
    except Exception as exc:
        raise QueryError(f"query execution failed: {type(exc).__name__}: {exc}") from exc


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _run(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    if not isinstance(tables, dict):
        raise QueryError("tables must be a mapping of table name to rows")
    envs = _scan(plan, tables)
    if plan.where is not None:
        envs = [env for env in envs if _evaluate(plan.where, env, plan, None) is True]
    if plan.is_aggregate:
        records = _aggregate_records(plan, envs)
    else:
        records = [(_project(plan, env, None), env, None) for env in envs]
    if plan.distinct:
        records = _distinct(records)
    if plan.order_by:
        records = _sort(plan, records)
    return _slice(plan, [record[0] for record in records])


def _table_rows(tables: dict[str, list[dict]], table_name: str) -> list[dict]:
    if table_name not in tables:
        raise QueryError(f"unknown table: {table_name}")
    rows = tables[table_name]
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise QueryError(f"table {table_name} is not a list of rows")
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError(f"table {table_name} contains a row that is not a mapping")
    return list(rows)


def _scan(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    envs: list[dict] | None = None
    for index, (table_name, alias) in enumerate(plan.sources):
        rows = _table_rows(tables, table_name)
        if envs is None:
            envs = [{alias: row} for row in rows]
            continue
        if index - 1 >= len(plan.join_conditions):
            raise QueryError("JOIN requires an ON condition")
        condition = plan.join_conditions[index - 1]
        joined = []
        for env in envs:
            for row in rows:
                candidate = dict(env)
                candidate[alias] = row
                if _evaluate(condition, candidate, plan, None) is True:
                    joined.append(candidate)
        envs = joined
    return envs if envs is not None else [{}]


def _aggregate_records(plan: Plan, envs: list[dict]) -> list[tuple]:
    groups: list[list[dict]] = []
    positions: dict[tuple, int] = {}
    if plan.group_by:
        for env in envs:
            key = tuple(_hashable(_evaluate(expr, env, plan, None)) for expr in plan.group_by)
            position = positions.get(key)
            if position is None:
                positions[key] = len(groups)
                groups.append([env])
            else:
                groups[position].append(env)
    else:
        groups.append(list(envs))
    records = []
    for members in groups:
        values = _aggregate_values(plan, members)
        env = members[0] if members else {}
        if plan.having is not None and _evaluate(plan.having, env, plan, values) is not True:
            continue
        records.append((_project(plan, env, values), env, values))
    return records


def _aggregate_values(plan: Plan, members: list[dict]) -> dict:
    values: dict[int, object] = {}
    for call in plan.aggregate_calls:
        name = call.name.upper()
        function = AGGREGATES.get(name)
        if function is None:
            raise QueryError(f"unknown aggregate function: {name}")
        if not call.args:
            raise QueryError(f"{name} takes exactly one argument")
        argument = call.args[0]
        if type(argument).__name__ == "Star":
            inputs: list = [1] * len(members)
        else:
            inputs = [_evaluate(argument, env, plan, None) for env in members]
        values[id(call)] = function(inputs)
    return values


def _project(plan: Plan, env: dict, aggregates: dict | None) -> dict:
    row: dict = {}
    for item in plan.items:
        row[item.name] = _evaluate(item.expr, env, plan, aggregates)
    return row


def _distinct(records: list[tuple]) -> list[tuple]:
    seen = set()
    unique = []
    for record in records:
        key = tuple(_hashable(value) for value in record[0].values())
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _sort(plan: Plan, records: list[tuple]) -> list[tuple]:
    decorated = []
    for record in records:
        row, env, aggregates = record
        keys = []
        for key in plan.order_by:
            if key.output_index is not None:
                keys.append(row.get(plan.items[key.output_index].name))
            else:
                keys.append(_evaluate(key.expr, env, plan, aggregates))
        decorated.append((keys, record))
    directions = [bool(key.descending) for key in plan.order_by]
    decorated.sort(key=cmp_to_key(lambda left, right: _compare_keys(left[0], right[0], directions)))
    return [record for _keys, record in decorated]


def _compare_keys(left: list, right: list, directions: list) -> int:
    for a, b, descending in zip(left, right, directions):
        if a is None and b is None:
            continue
        if a is None:
            outcome = -1
        elif b is None:
            outcome = 1
        else:
            outcome = compare(a, b)
            if not outcome:
                continue
        if descending:
            outcome = -outcome
        if outcome:
            return outcome
    return 0


def _slice(plan: Plan, rows: list[dict]) -> list[dict]:
    offset = plan.offset or 0
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise QueryError("OFFSET must be a non-negative integer")
    limit = plan.limit
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise QueryError("LIMIT must be a non-negative integer")
    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _hashable(value: object) -> tuple:
    if value is None:
        return ("null",)
    try:
        hash(value)
    except TypeError:
        return ("repr", repr(value))
    return (type(value).__name__, value)


def _evaluate(node: object, env: dict, plan: Plan, aggregates: dict | None) -> object:
    kind = type(node).__name__
    if kind == "Literal":
        return node.value
    if kind == "Column":
        alias, column = plan.bindings.get((node.table, node.name), (node.table, node.name))
        row = env.get(alias)
        if not isinstance(row, dict):
            return None
        return row.get(column)
    if kind == "Func":
        return _call(node, env, plan, aggregates)
    if kind == "UnaryOp":
        return _unary(node, env, plan, aggregates)
    if kind == "BinOp":
        return _binary(node, env, plan, aggregates)
    if kind == "Star":
        raise QueryError("* is not valid in this position")
    raise QueryError(f"cannot evaluate expression node: {kind}")


def _call(node: object, env: dict, plan: Plan, aggregates: dict | None) -> object:
    name = node.name.upper()
    if name in AGGREGATES:
        if aggregates is None:
            raise QueryError(f"aggregate function {name} is not allowed here")
        if id(node) not in aggregates:
            raise QueryError(f"aggregate function {name} was not planned for this query")
        return aggregates[id(node)]
    function = SCALARS.get(name)
    if function is None:
        raise QueryError(f"unknown function: {name}")
    return function([_evaluate(argument, env, plan, aggregates) for argument in node.args])


def _boolean(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    raise QueryError(f"expected a boolean value, got {type(value).__name__}")


def _unary(node: object, env: dict, plan: Plan, aggregates: dict | None) -> object:
    op = node.op
    value = _evaluate(node.operand, env, plan, aggregates)
    if op == "IS NULL":
        return value is None
    if op == "IS NOT NULL":
        return value is not None
    if op == "NOT":
        value = _boolean(value)
        return None if value is None else not value
    if op == "-":
        if value is None:
            return None
        if not _is_number(value):
            raise QueryError("cannot negate a non-numeric value")
        return -value
    raise QueryError(f"unsupported unary operator: {op}")


def _binary(node: object, env: dict, plan: Plan, aggregates: dict | None) -> object:
    op = node.op
    if op in ("AND", "OR"):
        left = _boolean(_evaluate(node.left, env, plan, aggregates))
        right = _boolean(_evaluate(node.right, env, plan, aggregates))
        return _logical(op, left, right)
    if op == "IN":
        return _in(node, env, plan, aggregates)
    left = _evaluate(node.left, env, plan, aggregates)
    if op == "LIKE":
        return like_match(left, _evaluate(node.right, env, plan, aggregates))
    right = _evaluate(node.right, env, plan, aggregates)
    if op in _ARITHMETIC:
        return _arithmetic(op, left, right)
    test = _COMPARISONS.get(op)
    if test is None:
        raise QueryError(f"unsupported operator: {op}")
    outcome = compare(left, right)
    if outcome is None:
        return None
    return test(outcome)


def _logical(op: str, left: object, right: object) -> object:
    if op == "AND":
        if left is False or right is False:
            return False
        if left is None or right is None:
            return None
        return True
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False


def _arithmetic(op: str, left: object, right: object) -> object:
    if left is None or right is None:
        return None
    if not _is_number(left) or not _is_number(right):
        raise QueryError(
            f"arithmetic requires numeric operands, got {type(left).__name__} and {type(right).__name__}"
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


def _in(node: object, env: dict, plan: Plan, aggregates: dict | None) -> object:
    left = _evaluate(node.left, env, plan, aggregates)
    candidates = node.right if isinstance(node.right, tuple) else (node.right,)
    if left is None:
        return None
    unknown = False
    for candidate in candidates:
        outcome = compare(left, _evaluate(candidate, env, plan, aggregates))
        if outcome is None:
            unknown = True
        elif outcome == 0:
            return True
    return None if unknown else False
