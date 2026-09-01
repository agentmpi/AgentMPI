"""Execution of a planned minidb SELECT statement."""

from __future__ import annotations

from functools import cmp_to_key

from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match
from .planner import Plan, resolve_column

_ARITHMETIC_OPS = frozenset({'+', '-', '*', '/'})
_COMPARISON_OPS = frozenset({'=', '==', '<>', '!=', '<', '<=', '>', '>='})
_EXPRESSION_NODES = frozenset({'Column', 'Literal', 'BinOp', 'UnaryOp', 'Func', 'Star'})

_MISSING = object()


def execute(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute a planned SELECT against `tables` and return the result rows."""
    try:
        return _run(plan, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f'query execution failed: {exc}') from exc


def _run(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    rows = _from_clause(plan, tables)
    if plan.where is not None:
        rows = [env for env in rows if _is_true(_eval(plan.where, env, plan, None))]
    if plan.is_aggregate:
        records = _aggregate_records(plan, rows)
    else:
        records = [(_output_row(plan, env, None), env, None) for env in rows]
    if plan.distinct:
        records = _distinct(records)
    if plan.order_by:
        records = _order(plan, records)
    return _slice(plan, [record[0] for record in records])


def _source_rows(tables: dict[str, list[dict]], table_name: str) -> list[dict]:
    if not isinstance(tables, dict) or table_name not in tables:
        raise QueryError(f'unknown table: {table_name}')
    rows = tables[table_name]
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise QueryError(f'table {table_name} is not a list of rows')
    return list(rows)


def _from_clause(plan: Plan, tables: dict[str, list[dict]]) -> list[dict]:
    if not plan.sources:
        return [{}]
    table_name, alias = plan.sources[0]
    envs = [{alias: row} for row in _source_rows(tables, table_name)]
    for index in range(1, len(plan.sources)):
        table_name, alias = plan.sources[index]
        right_rows = _source_rows(tables, table_name)
        condition = None
        if index - 1 < len(plan.join_conditions):
            condition = plan.join_conditions[index - 1]
        joined = []
        for env in envs:
            for row in right_rows:
                candidate = dict(env)
                candidate[alias] = row
                if condition is None or _is_true(_eval(condition, candidate, plan, None)):
                    joined.append(candidate)
        envs = joined
    return envs


def _aggregate_records(plan: Plan, rows: list[dict]) -> list[tuple]:
    groups: list[list[dict]] = []
    position_of_key: dict[tuple, int] = {}
    if plan.group_by:
        for env in rows:
            key = tuple(_hashable(_eval(expr, env, plan, None)) for expr in plan.group_by)
            position = position_of_key.get(key)
            if position is None:
                position_of_key[key] = len(groups)
                groups.append([env])
            else:
                groups[position].append(env)
    else:
        groups.append(list(rows))

    records = []
    for members in groups:
        aggregates = _group_aggregates(plan, members)
        env = members[0] if members else {}
        if plan.having is not None and not _is_true(_eval(plan.having, env, plan, aggregates)):
            continue
        records.append((_output_row(plan, env, aggregates), env, aggregates))
    return records


def _group_aggregates(plan: Plan, members: list[dict]) -> dict[int, object]:
    aggregates: dict[int, object] = {}
    for call in plan.aggregate_calls:
        name = _function_name(call)
        if name not in AGGREGATES:
            raise QueryError(f'unknown aggregate function: {name}')
        arguments = _function_args(call)
        if name == 'COUNT' and (not arguments or _is_star(arguments[0])):
            # AGGREGATES['COUNT'] counts non-NULL entries, so COUNT(*) is one
            # non-NULL sentinel per row of the group.
            values = [True] * len(members)
        elif len(arguments) != 1:
            raise QueryError(f'{name} takes exactly one argument')
        else:
            values = [_eval(arguments[0], env, plan, None) for env in members]
        aggregates[id(call)] = AGGREGATES[name](values)
    return aggregates


def _output_row(plan: Plan, env: dict, aggregates: dict | None) -> dict:
    row: dict = {}
    for item in plan.select_items:
        row[item.name] = _eval(item.expr, env, plan, aggregates)
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


def _order(plan: Plan, records: list[tuple]) -> list[tuple]:
    decorated = []
    for record in records:
        out_row, env, aggregates = record
        keys = []
        for order_key in plan.order_by:
            index = order_key.output_index
            if index is not None:
                if not 0 <= index < len(plan.select_items):
                    raise QueryError('ORDER BY refers to an output column that does not exist')
                keys.append(out_row.get(plan.select_items[index].name))
            else:
                keys.append(_eval(order_key.expr, env, plan, aggregates))
        decorated.append((keys, record))
    directions = [bool(order_key.descending) for order_key in plan.order_by]
    decorated.sort(key=cmp_to_key(lambda left, right: _compare_keys(left[0], right[0], directions)))
    return [record for _keys, record in decorated]


def _compare_keys(left: list, right: list, directions: list[bool]) -> int:
    for a, b, descending in zip(left, right, directions):
        if a is None and b is None:
            continue
        if a is None:
            result = -1
        elif b is None:
            result = 1
        else:
            result = compare(a, b)
            if result is None:
                continue
        if descending:
            result = -result
        if result:
            return result
    return 0


def _slice(plan: Plan, rows: list[dict]) -> list[dict]:
    offset = plan.offset or 0
    if offset < 0:
        raise QueryError('OFFSET must not be negative')
    limit = plan.limit
    if limit is not None and limit < 0:
        raise QueryError('LIMIT must not be negative')
    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _eval(node, env: dict, plan: Plan, aggregates: dict | None):
    kind = type(node).__name__
    if kind == 'Literal':
        return _attr(node, ('value', 'val', 'literal'))
    if kind == 'Column':
        return _column_value(node, env, plan)
    if kind == 'Func':
        return _eval_func(node, env, plan, aggregates)
    if kind == 'UnaryOp':
        return _eval_unary(node, env, plan, aggregates)
    if kind == 'BinOp':
        return _eval_binary(node, env, plan, aggregates)
    if kind == 'Star':
        raise QueryError('* is not valid in this position')
    if node is None or isinstance(node, (bool, int, float, str)):
        return node
    raise QueryError(f'cannot evaluate expression node: {kind}')


def _attr(node, names: tuple, default=_MISSING):
    for name in names:
        value = getattr(node, name, _MISSING)
        if value is not _MISSING:
            return value
    if default is not _MISSING:
        return default
    raise QueryError(f'unsupported {type(node).__name__} node in query')


def _column_value(node, env: dict, plan: Plan):
    qualifier = _attr(node, ('table', 'qualifier', 'table_name', 'prefix'), None)
    name = _attr(node, ('name', 'column', 'column_name', 'col'))
    source_alias, column_name = resolve_column(plan, qualifier, name)
    row = env.get(source_alias)
    if not isinstance(row, dict):
        return None
    return row.get(column_name)


def _function_name(node) -> str:
    name = _attr(node, ('name', 'func', 'function', 'func_name', 'fname'))
    if not isinstance(name, str):
        raise QueryError('function name must be a string')
    return name.upper()


def _function_args(node) -> list:
    arguments = _attr(node, ('args', 'arguments', 'params', 'operands'), ())
    if arguments is None:
        return []
    if isinstance(arguments, (list, tuple)):
        return list(arguments)
    return [arguments]


def _is_star(node) -> bool:
    if type(node).__name__ == 'Star':
        return True
    return node is None or node == '*'


def _eval_func(node, env: dict, plan: Plan, aggregates: dict | None):
    name = _function_name(node)
    if name in AGGREGATES:
        if aggregates is None:
            raise QueryError(f'aggregate function {name} is not allowed here')
        if id(node) not in aggregates:
            raise QueryError(f'aggregate function {name} was not planned for this group')
        return aggregates[id(node)]
    if name not in SCALARS:
        raise QueryError(f'unknown function: {name}')
    values = [_eval(argument, env, plan, aggregates) for argument in _function_args(node)]
    return SCALARS[name](*values)


def _operator(node) -> str:
    op = _attr(node, ('op', 'operator', 'kind', 'name'))
    if not isinstance(op, str):
        raise QueryError('operator must be a string')
    return ' '.join(op.upper().split())


def _eval_unary(node, env: dict, plan: Plan, aggregates: dict | None):
    op = _operator(node)
    operand = _attr(node, ('operand', 'expr', 'value', 'arg', 'child', 'right'))
    if op in ('IS NULL', 'ISNULL'):
        return _eval(operand, env, plan, aggregates) is None
    if op in ('IS NOT NULL', 'ISNOTNULL', 'NOT NULL'):
        return _eval(operand, env, plan, aggregates) is not None
    value = _eval(operand, env, plan, aggregates)
    if op == 'NOT':
        return _negate(_tri(value))
    if op in ('-', '+'):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError(f'unary {op} requires a number')
        return -value if op == '-' else value
    raise QueryError(f'unsupported unary operator: {op}')


def _eval_binary(node, env: dict, plan: Plan, aggregates: dict | None):
    op = _operator(node)
    left_node = _attr(node, ('left', 'lhs', 'a', 'first', 'operand'))
    right_node = _attr(node, ('right', 'rhs', 'b', 'second'), None)
    if op in ('AND', 'OR'):
        left = _tri(_eval(left_node, env, plan, aggregates))
        right = _tri(_eval(right_node, env, plan, aggregates))
        return _logic(op, left, right)
    left = _eval(left_node, env, plan, aggregates)
    if op in ('IS NULL', 'ISNULL'):
        return left is None
    if op in ('IS NOT NULL', 'ISNOTNULL'):
        return left is not None
    if op in ('IN', 'NOT IN'):
        result = _eval_in(left, _list_values(right_node, env, plan, aggregates))
        return _negate(result) if op == 'NOT IN' else result
    if op in ('LIKE', 'NOT LIKE'):
        result = like_match(left, _eval(right_node, env, plan, aggregates))
        return _negate(result) if op == 'NOT LIKE' else result
    if op in ('IS', 'IS NOT'):
        right = _eval(right_node, env, plan, aggregates)
        if right is None:
            result = left is None
        else:
            result = _compare_op('=', left, right)
        return _negate(result) if op == 'IS NOT' else result
    right = _eval(right_node, env, plan, aggregates)
    if op in _ARITHMETIC_OPS:
        return _arithmetic(op, left, right)
    if op in _COMPARISON_OPS:
        return _compare_op(op, left, right)
    raise QueryError(f'unsupported operator: {op}')


def _list_values(node, env: dict, plan: Plan, aggregates: dict | None) -> list:
    if node is None:
        return []
    if isinstance(node, (list, tuple)):
        items = list(node)
    elif type(node).__name__ in _EXPRESSION_NODES:
        items = [node]
    else:
        items = [node]
        for name in ('items', 'values', 'elements', 'expressions', 'args'):
            candidate = getattr(node, name, None)
            if isinstance(candidate, (list, tuple)):
                items = list(candidate)
                break
    return [_eval(item, env, plan, aggregates) for item in items]


def _eval_in(left, values: list):
    if left is None:
        return None
    unknown = False
    for value in values:
        if value is None:
            unknown = True
            continue
        result = compare(left, value)
        if result is None:
            unknown = True
        elif result == 0:
            return True
    return None if unknown else False


def _arithmetic(op: str, left, right):
    if left is None or right is None:
        return None
    for operand in (left, right):
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            raise QueryError(f'arithmetic operator {op} requires numbers')
    if op == '+':
        return left + right
    if op == '-':
        return left - right
    if op == '*':
        return left * right
    if right == 0:
        return None
    return left / right


def _compare_op(op: str, left, right):
    result = compare(left, right)
    if result is None:
        return None
    if op in ('=', '=='):
        return result == 0
    if op in ('<>', '!='):
        return result != 0
    if op == '<':
        return result < 0
    if op == '<=':
        return result <= 0
    if op == '>':
        return result > 0
    return result >= 0


def _logic(op: str, left, right):
    if op == 'AND':
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


def _tri(value):
    if value is None:
        return None
    return _is_true(value)


def _negate(value):
    if value is None:
        return None
    return not value


def _is_true(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return bool(value)


def _hashable(value):
    try:
        hash(value)
    except TypeError:
        return ('<unhashable>', repr(value))
    return (isinstance(value, bool), value)
