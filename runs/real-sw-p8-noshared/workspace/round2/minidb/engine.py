"""Execution of a planned minidb SELECT statement."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cmp_to_key

from .errors import QueryError
from .functions import AGGREGATES, SCALARS, compare, like_match

_MISSING = object()

_ARITHMETIC_OPS = frozenset({'+', '-', '*', '/'})
_COMPARISON_OPS = frozenset({'=', '==', '<>', '!=', '<', '<=', '>', '>='})
_NODE_CLASSES = frozenset({'Column', 'Literal', 'BinOp', 'UnaryOp', 'Func', 'Star'})


@dataclass(frozen=True)
class _ColumnRef:
    """A column already bound to a source alias, produced by `*` expansion."""

    alias: str
    column: str


@dataclass
class _Compiled:
    """The plan reduced to the shapes execution needs."""

    sources: list = field(default_factory=list)
    join_conditions: list = field(default_factory=list)
    where: object = None
    group_by: list = field(default_factory=list)
    having: object = None
    items: list = field(default_factory=list)
    distinct: bool = False
    order_by: list = field(default_factory=list)
    limit: object = None
    offset: object = None
    is_aggregate: bool = False
    aggregate_calls: list = field(default_factory=list)
    schema: dict = field(default_factory=dict)
    schemaless: set = field(default_factory=set)
    bindings: dict = field(default_factory=dict)


def execute(plan, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute a planned SELECT against `tables` and return the result rows."""
    try:
        compiled = _compile(plan, tables)
        return _run(compiled, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f'query execution failed: {exc}') from exc


def _field(obj, names: tuple, default=_MISSING):
    for name in names:
        value = getattr(obj, name, _MISSING)
        if value is not _MISSING:
            return value
    if default is not _MISSING:
        return default
    raise QueryError(f'unsupported {type(obj).__name__} object in plan')


def _compile(plan, tables: dict[str, list[dict]]) -> _Compiled:
    if not isinstance(tables, dict):
        raise QueryError('tables must be a mapping of table name to rows')
    compiled = _Compiled()
    compiled.sources = _sources(plan)
    for table_name, alias in compiled.sources:
        columns = _column_order(_source_rows(tables, table_name))
        compiled.schema[alias] = columns
        if not columns:
            compiled.schemaless.add(alias)
    compiled.join_conditions = _join_conditions(plan, len(compiled.sources))
    compiled.where = _field(plan, ('where', 'where_clause', 'filter'), None)
    compiled.group_by = list(_field(plan, ('group_by', 'groupby', 'group_keys'), ()) or ())
    compiled.having = _field(plan, ('having', 'having_clause'), None)
    compiled.distinct = bool(_field(plan, ('distinct', 'is_distinct'), False))
    compiled.limit = _field(plan, ('limit',), None)
    compiled.offset = _field(plan, ('offset',), None)
    compiled.items = _select_items(plan, compiled)
    compiled.order_by = _order_keys(plan, compiled)
    compiled.aggregate_calls = _aggregate_calls(compiled)
    declared = bool(_field(plan, ('is_aggregate', 'aggregate', 'has_aggregates'), False))
    compiled.is_aggregate = declared or bool(compiled.group_by) or bool(compiled.aggregate_calls)
    return compiled


def _sources(plan) -> list[tuple[str, str]]:
    raw = _field(plan, ('sources', 'from_sources', 'source_list', 'from_tables'), None)
    if not raw:
        raise QueryError('the plan describes no FROM source')
    sources = []
    seen = set()
    for entry in raw:
        table_name, alias = _source_entry(entry)
        if alias in seen:
            raise QueryError(f'duplicate table alias: {alias}')
        seen.add(alias)
        sources.append((table_name, alias))
    return sources


def _source_entry(entry) -> tuple[str, str]:
    if isinstance(entry, str):
        return (entry, entry)
    if isinstance(entry, (tuple, list)):
        if len(entry) == 2:
            return (str(entry[0]), str(entry[1]))
        if len(entry) == 1:
            return (str(entry[0]), str(entry[0]))
        raise QueryError('malformed FROM source in plan')
    name = _field(entry, ('table', 'name', 'table_name'))
    alias = _field(entry, ('alias', 'as_name'), None)
    if hasattr(name, 'name'):
        name = _field(name, ('name', 'table'))
    return (str(name), str(alias) if alias else str(name))


def _source_rows(tables: dict[str, list[dict]], table_name: str) -> list[dict]:
    if table_name not in tables:
        raise QueryError(f'unknown table: {table_name}')
    rows = tables[table_name]
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise QueryError(f'table {table_name} is not a list of rows')
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError(f'table {table_name} contains a row that is not a mapping')
    return list(rows)


def _column_order(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    return [column for column in rows[0] if isinstance(column, str)]


def _join_conditions(plan, source_count: int) -> list:
    raw = _field(plan, ('join_conditions', 'on_conditions', 'joins', 'join_on'), None)
    conditions = []
    for entry in raw or ():
        if entry is None or type(entry).__name__ in _NODE_CLASSES:
            conditions.append(entry)
        else:
            conditions.append(_field(entry, ('condition', 'on', 'expr'), None))
    while len(conditions) < max(source_count - 1, 0):
        conditions.append(None)
    return conditions


def _select_items(plan, compiled: _Compiled) -> list[tuple[str, object]]:
    raw = _field(plan, ('select_items', 'items', 'select_list', 'projections'), None)
    if raw is None:
        raise QueryError('the plan describes no select list')
    pairs = []
    for entry in raw:
        pairs.extend(_select_item(entry, compiled))
    if not pairs:
        raise QueryError('the select list is empty')
    ordered: list[tuple[str, object]] = []
    for name, expr in pairs:
        ordered = [pair for pair in ordered if pair[0] != name]
        ordered.append((name, expr))
    return ordered


def _select_item(entry, compiled: _Compiled) -> list[tuple[str, object]]:
    kind = type(entry).__name__
    if kind == 'Star':
        return _expand_star(_field(entry, ('table', 'qualifier'), None), compiled)
    if kind in _NODE_CLASSES:
        return [(_output_name(entry, None, None), entry)]
    expr = _field(entry, ('expr', 'expression', 'value'), None)
    name = _output_name_of(entry)
    alias = _field(entry, ('alias', 'as_name', 'label'), None)
    source_text = _field(entry, ('source_text', 'source', 'text'), None)
    if expr is None and not isinstance(name, str):
        raise QueryError('malformed select item in plan')
    if expr is not None and type(expr).__name__ == 'Star':
        return _expand_star(_field(expr, ('table', 'qualifier'), None), compiled)
    if isinstance(name, str) and name:
        return [(name, expr)]
    return [(_output_name(expr, alias, source_text), expr)]


def _output_name_of(entry):
    """The name a plan item carries, whether as an attribute or a method."""
    name = _field(entry, ('name', 'output_name', 'out_name'), None)
    if callable(name):
        try:
            name = name()
        except Exception:
            name = None
    return name if isinstance(name, str) and name else None


def _expand_star(qualifier, compiled: _Compiled) -> list[tuple[str, object]]:
    if qualifier is not None:
        qualifier = str(qualifier)
        if qualifier not in compiled.schema:
            raise QueryError(f'unknown table or alias: {qualifier}')
    expanded = []
    for _table_name, alias in compiled.sources:
        if qualifier is not None and alias != qualifier:
            continue
        for column in compiled.schema[alias]:
            expanded.append((column, _ColumnRef(alias, column)))
    return expanded


def _output_name(expr, alias, source_text) -> str:
    if isinstance(alias, str) and alias:
        return alias
    if isinstance(expr, _ColumnRef):
        return expr.column
    if type(expr).__name__ == 'Column':
        return str(_field(expr, ('name', 'column', 'column_name')))
    if isinstance(source_text, str) and source_text.strip():
        return ''.join(source_text.split())
    return _render(expr)


def _order_keys(plan, compiled: _Compiled) -> list[tuple[object, object, bool]]:
    raw = _field(plan, ('order_by', 'order_keys', 'orderby'), ()) or ()
    keys = []
    for entry in raw:
        if type(entry).__name__ in _NODE_CLASSES:
            keys.append((entry, _alias_index(entry, compiled), False))
            continue
        expr = _field(entry, ('expr', 'expression', 'key'), None)
        index = _field(entry, ('output_index', 'index', 'position'), None)
        if isinstance(index, bool) or not isinstance(index, int):
            index = _alias_index(expr, compiled)
        keys.append((expr, index, _descending(entry)))
    return keys


def _descending(entry) -> bool:
    value = _field(entry, ('descending', 'desc', 'is_descending'), None)
    if isinstance(value, bool):
        return value
    direction = _field(entry, ('direction', 'order', 'sort'), None)
    if isinstance(direction, str):
        return direction.strip().upper() == 'DESC'
    return False


def _alias_index(expr, compiled: _Compiled):
    if type(expr).__name__ != 'Column':
        return None
    if _field(expr, ('table', 'qualifier'), None) is not None:
        return None
    name = _field(expr, ('name', 'column', 'column_name'), None)
    for index, (output_name, _expr) in enumerate(compiled.items):
        if output_name == name:
            return index
    return None


def _aggregate_calls(compiled: _Compiled) -> list:
    calls = []
    seen = set()

    def walk(node):
        if node is None or isinstance(node, _ColumnRef):
            return
        kind = type(node).__name__
        if kind == 'Func':
            if _function_name(node) in AGGREGATES:
                if id(node) not in seen:
                    seen.add(id(node))
                    calls.append(node)
                return
            for argument in _function_args(node):
                walk(argument)
            return
        if kind == 'UnaryOp':
            walk(_field(node, ('operand', 'expr', 'value', 'arg', 'child'), None))
            return
        if kind == 'BinOp':
            walk(_field(node, ('left', 'lhs', 'a'), None))
            right = _field(node, ('right', 'rhs', 'b'), None)
            if isinstance(right, (list, tuple)):
                for operand in right:
                    walk(operand)
            else:
                walk(right)

    for _name, expr in compiled.items:
        walk(expr)
    walk(compiled.having)
    for expr, index, _descending_flag in compiled.order_by:
        if index is None:
            walk(expr)
    return calls


def _run(compiled: _Compiled, tables: dict[str, list[dict]]) -> list[dict]:
    envs = _row_space(compiled, tables)
    if compiled.where is not None:
        envs = [env for env in envs if _is_true(_eval(compiled.where, env, compiled, None))]
    if compiled.is_aggregate:
        records = _aggregate_records(compiled, envs)
    else:
        records = [(_output_row(compiled, env, None), env, None) for env in envs]
    if compiled.distinct:
        records = _distinct(records)
    if compiled.order_by:
        records = _order(compiled, records)
    return _slice(compiled, [record[0] for record in records])


def _row_space(compiled: _Compiled, tables: dict[str, list[dict]]) -> list[dict]:
    table_name, alias = compiled.sources[0]
    envs = [{alias: row} for row in _source_rows(tables, table_name)]
    for index in range(1, len(compiled.sources)):
        table_name, alias = compiled.sources[index]
        right_rows = _source_rows(tables, table_name)
        condition = compiled.join_conditions[index - 1]
        joined = []
        for env in envs:
            for row in right_rows:
                candidate = dict(env)
                candidate[alias] = row
                if condition is None or _is_true(_eval(condition, candidate, compiled, None)):
                    joined.append(candidate)
        envs = joined
    return envs


def _aggregate_records(compiled: _Compiled, envs: list[dict]) -> list[tuple]:
    groups: list[list[dict]] = []
    position_of_key: dict[tuple, int] = {}
    if compiled.group_by:
        for env in envs:
            key = tuple(
                _hashable(_eval(expr, env, compiled, None)) for expr in compiled.group_by
            )
            position = position_of_key.get(key)
            if position is None:
                position_of_key[key] = len(groups)
                groups.append([env])
            else:
                groups[position].append(env)
    else:
        groups.append(list(envs))

    records = []
    for members in groups:
        aggregates = _group_aggregates(compiled, members)
        env = members[0] if members else {}
        if compiled.having is not None:
            if not _is_true(_eval(compiled.having, env, compiled, aggregates)):
                continue
        records.append((_output_row(compiled, env, aggregates), env, aggregates))
    return records


def _group_aggregates(compiled: _Compiled, members: list[dict]) -> dict:
    aggregates: dict = {}
    for call in compiled.aggregate_calls:
        name = _function_name(call)
        if name not in AGGREGATES:
            raise QueryError(f'unknown aggregate function: {name}')
        arguments = _function_args(call)
        if name == 'COUNT' and (not arguments or _is_star(arguments[0])):
            values = [True] * len(members)
        elif len(arguments) != 1:
            raise QueryError(f'{name} takes exactly one argument')
        else:
            values = [_eval(arguments[0], env, compiled, None) for env in members]
        aggregates[id(call)] = AGGREGATES[name](values)
    return aggregates


def _output_row(compiled: _Compiled, env: dict, aggregates: dict | None) -> dict:
    row: dict = {}
    for name, expr in compiled.items:
        row[name] = _eval(expr, env, compiled, aggregates)
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


def _order(compiled: _Compiled, records: list[tuple]) -> list[tuple]:
    decorated = []
    for record in records:
        out_row, env, aggregates = record
        keys = []
        for expr, index, _descending_flag in compiled.order_by:
            if index is not None:
                if not 0 <= index < len(compiled.items):
                    raise QueryError('ORDER BY refers to an output column that does not exist')
                keys.append(out_row.get(compiled.items[index][0]))
            else:
                keys.append(_eval(expr, env, compiled, aggregates))
        decorated.append((keys, record))
    directions = [bool(entry[2]) for entry in compiled.order_by]
    decorated.sort(
        key=cmp_to_key(lambda left, right: _compare_keys(left[0], right[0], directions))
    )
    return [record for _keys, record in decorated]


def _compare_keys(left: list, right: list, directions: list) -> int:
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


def _slice(compiled: _Compiled, rows: list[dict]) -> list[dict]:
    offset = compiled.offset or 0
    limit = compiled.limit
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise QueryError('OFFSET must be an integer')
    if offset < 0:
        raise QueryError('OFFSET must not be negative')
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise QueryError('LIMIT must be an integer')
        if limit < 0:
            raise QueryError('LIMIT must not be negative')
    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _resolve(compiled: _Compiled, qualifier, name) -> tuple[str, str]:
    if not isinstance(name, str):
        raise QueryError('column name must be a string')
    qualifier = str(qualifier) if qualifier is not None else None
    key = (qualifier, name)
    bound = compiled.bindings.get(key)
    if bound is not None:
        return bound
    if qualifier is not None:
        if qualifier not in compiled.schema:
            raise QueryError(f'unknown table or alias: {qualifier}')
        if name not in compiled.schema[qualifier] and qualifier not in compiled.schemaless:
            raise QueryError(f'unknown column: {qualifier}.{name}')
        bound = (qualifier, name)
    else:
        holders = [
            alias for _table_name, alias in compiled.sources
            if name in compiled.schema[alias]
        ]
        if len(holders) > 1:
            raise QueryError(f'ambiguous column: {name}; qualify it with a table name')
        if len(holders) == 1:
            bound = (holders[0], name)
        else:
            blank = [
                alias for _table_name, alias in compiled.sources
                if alias in compiled.schemaless
            ]
            if len(blank) == 1:
                bound = (blank[0], name)
            elif len(blank) > 1:
                raise QueryError(f'ambiguous column: {name}; qualify it with a table name')
            else:
                raise QueryError(f'unknown column: {name}')
    compiled.bindings[key] = bound
    return bound


def _eval(node, env: dict, compiled: _Compiled, aggregates: dict | None):
    if isinstance(node, _ColumnRef):
        row = env.get(node.alias)
        return row.get(node.column) if isinstance(row, dict) else None
    kind = type(node).__name__
    if kind == 'Literal':
        return _field(node, ('value', 'val', 'literal'))
    if kind == 'Column':
        qualifier = _field(node, ('table', 'qualifier', 'table_name', 'prefix'), None)
        name = _field(node, ('name', 'column', 'column_name', 'col'))
        alias, column = _resolve(compiled, qualifier, name)
        row = env.get(alias)
        return row.get(column) if isinstance(row, dict) else None
    if kind == 'Func':
        return _eval_func(node, env, compiled, aggregates)
    if kind == 'UnaryOp':
        return _eval_unary(node, env, compiled, aggregates)
    if kind == 'BinOp':
        return _eval_binary(node, env, compiled, aggregates)
    if kind == 'Star':
        raise QueryError('* is not valid in this position')
    if node is None or isinstance(node, (bool, int, float, str)):
        return node
    raise QueryError(f'cannot evaluate expression node: {kind}')


def _function_name(node) -> str:
    name = _field(node, ('name', 'func', 'function', 'func_name', 'fname'))
    if not isinstance(name, str):
        raise QueryError('function name must be a string')
    return name.upper()


def _function_args(node) -> list:
    arguments = _field(node, ('args', 'arguments', 'params', 'operands'), ())
    if arguments is None:
        return []
    if isinstance(arguments, (list, tuple)):
        return list(arguments)
    return [arguments]


def _is_star(node) -> bool:
    if type(node).__name__ == 'Star':
        return True
    return node is None or node == '*'


def _eval_func(node, env: dict, compiled: _Compiled, aggregates: dict | None):
    name = _function_name(node)
    if name in AGGREGATES:
        if aggregates is None:
            raise QueryError(f'aggregate function {name} is not allowed here')
        if id(node) not in aggregates:
            raise QueryError(f'aggregate function {name} was not computed for this group')
        return aggregates[id(node)]
    if name not in SCALARS:
        raise QueryError(f'unknown function: {name}')
    values = [_eval(argument, env, compiled, aggregates) for argument in _function_args(node)]
    return SCALARS[name](*values)


def _operator(node) -> str:
    op = _field(node, ('op', 'operator', 'kind', 'name'))
    if not isinstance(op, str):
        raise QueryError('operator must be a string')
    return ' '.join(op.upper().split())


def _eval_unary(node, env: dict, compiled: _Compiled, aggregates: dict | None):
    op = _operator(node)
    operand = _field(node, ('operand', 'expr', 'value', 'arg', 'child', 'right'))
    if op in ('IS NULL', 'ISNULL'):
        return _eval(operand, env, compiled, aggregates) is None
    if op in ('IS NOT NULL', 'ISNOTNULL', 'NOT NULL'):
        return _eval(operand, env, compiled, aggregates) is not None
    value = _eval(operand, env, compiled, aggregates)
    if op == 'NOT':
        return _negate(_tri(value))
    if op in ('-', '+'):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError(f'unary {op} requires a number')
        return -value if op == '-' else value
    raise QueryError(f'unsupported unary operator: {op}')


def _eval_binary(node, env: dict, compiled: _Compiled, aggregates: dict | None):
    op = _operator(node)
    left_node = _field(node, ('left', 'lhs', 'a', 'first', 'operand'))
    right_node = _field(node, ('right', 'rhs', 'b', 'second'), None)
    if op in ('AND', 'OR'):
        left = _tri(_eval(left_node, env, compiled, aggregates))
        right = _tri(_eval(right_node, env, compiled, aggregates))
        return _logic(op, left, right)
    left = _eval(left_node, env, compiled, aggregates)
    if op in ('IS NULL', 'ISNULL'):
        return left is None
    if op in ('IS NOT NULL', 'ISNOTNULL'):
        return left is not None
    if op in ('IN', 'NOT IN'):
        result = _eval_in(left, _list_values(right_node, env, compiled, aggregates))
        return _negate(result) if op == 'NOT IN' else result
    if op in ('LIKE', 'NOT LIKE'):
        result = like_match(left, _eval(right_node, env, compiled, aggregates))
        return _negate(result) if op == 'NOT LIKE' else result
    if op in ('IS', 'IS NOT'):
        right = _eval(right_node, env, compiled, aggregates)
        result = left is None if right is None else _compare_op('=', left, right)
        return _negate(result) if op == 'IS NOT' else result
    right = _eval(right_node, env, compiled, aggregates)
    if op in _ARITHMETIC_OPS:
        return _arithmetic(op, left, right)
    if op in _COMPARISON_OPS:
        return _compare_op(op, left, right)
    raise QueryError(f'unsupported operator: {op}')


def _list_values(node, env: dict, compiled: _Compiled, aggregates: dict | None) -> list:
    if node is None:
        return []
    if isinstance(node, (list, tuple)):
        items = list(node)
    elif type(node).__name__ in _NODE_CLASSES or isinstance(node, _ColumnRef):
        items = [node]
    else:
        items = [node]
        for name in ('items', 'values', 'elements', 'expressions', 'args'):
            candidate = getattr(node, name, None)
            if isinstance(candidate, (list, tuple)):
                items = list(candidate)
                break
    return [_eval(item, env, compiled, aggregates) for item in items]


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


def _render(node) -> str:
    return ''.join(_render_text(node).split())


def _render_text(node) -> str:
    if isinstance(node, _ColumnRef):
        return node.column
    kind = type(node).__name__
    if kind == 'Literal':
        return _literal_text(_field(node, ('value', 'val', 'literal'), None))
    if kind == 'Column':
        qualifier = _field(node, ('table', 'qualifier', 'table_name'), None)
        name = str(_field(node, ('name', 'column', 'column_name'), ''))
        return f'{qualifier}.{name}' if qualifier else name
    if kind == 'Star':
        qualifier = _field(node, ('table', 'qualifier'), None)
        return f'{qualifier}.*' if qualifier else '*'
    if kind == 'Func':
        arguments = ','.join(_render_text(argument) for argument in _function_args(node))
        return f'{_function_name(node)}({arguments})'
    if kind == 'UnaryOp':
        op = _operator(node)
        operand = _render_text(_field(node, ('operand', 'expr', 'value', 'arg', 'child'), None))
        return f'{op}{operand}' if op in ('-', '+') else f'{op} {operand}'
    if kind == 'BinOp':
        op = _operator(node)
        left = _render_text(_field(node, ('left', 'lhs', 'a'), None))
        right_node = _field(node, ('right', 'rhs', 'b'), None)
        if isinstance(right_node, (list, tuple)):
            right = '(' + ','.join(_render_text(item) for item in right_node) + ')'
        else:
            right = _render_text(right_node)
        if op in _ARITHMETIC_OPS or op in _COMPARISON_OPS:
            return f'{left}{op}{right}'
        return f'{left} {op} {right}'
    if node is None:
        return 'NULL'
    return str(node)


def _literal_text(value) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)
