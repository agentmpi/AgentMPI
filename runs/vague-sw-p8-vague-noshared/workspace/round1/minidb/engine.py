"""Evaluation of a validated minidb query plan against in-memory tables.

No interface was published for `planner` or `functions`, so this module invents no
signature for either of them: it imports only `QueryError`, reads the plan through
structural access (attribute or mapping lookup of the names the specification's own
vocabulary implies), and implements value semantics -- comparison, ordering, LIKE,
the scalar and aggregate functions -- for itself.
"""

from __future__ import annotations

import re
from functools import cmp_to_key
from typing import Any, TypeAlias

from .errors import QueryError

__all__ = ["Row", "Tables", "execute"]

Row: TypeAlias = dict[str, Any]
Tables: TypeAlias = dict[str, list[Row]]

_JoinedRow: TypeAlias = dict[str, Row]

_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})
_AGGREGATE_NAMES = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
_SCALAR_NAMES = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

_KIND_BY_CLASS = {
    "literal": "literal",
    "const": "literal",
    "constant": "literal",
    "number": "literal",
    "string": "literal",
    "null": "literal",
    "column": "column",
    "columnref": "column",
    "col": "column",
    "field": "column",
    "fieldref": "column",
    "identifier": "column",
    "unary": "unary",
    "unaryop": "unary",
    "unaryexpr": "unary",
    "binary": "binary",
    "binaryop": "binary",
    "binop": "binary",
    "binaryexpr": "binary",
    "comparison": "binary",
    "func": "func",
    "function": "func",
    "funccall": "func",
    "functioncall": "func",
    "call": "func",
    "scalar": "func",
    "scalarcall": "func",
    "scalarfunc": "func",
    "isnull": "isnull",
    "nulltest": "isnull",
    "inlist": "inlist",
    "in": "inlist",
    "intest": "inlist",
    "inexpr": "inlist",
    "like": "like",
    "likeexpr": "like",
    "aggref": "aggref",
    "aggregateref": "aggref",
    "aggslot": "aggref",
    "aggregateslot": "aggref",
    "agg": "aggregate",
    "aggcall": "aggregate",
    "aggregate": "aggregate",
    "aggregatecall": "aggregate",
    "aggregatefunc": "aggregate",
    "aggregation": "aggregate",
}


class _Scope:
    """The evaluation context for one output row."""

    __slots__ = ("row", "members", "agg_values")

    def __init__(
        self,
        row: _JoinedRow,
        members: list[_JoinedRow] | None = None,
        agg_values: list[Any] | None = None,
    ) -> None:
        self.row = row
        self.members = members
        self.agg_values = agg_values


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

    select = tuple(_pick(plan, ("select", "select_items", "projections", "outputs"), ()) or ())
    if not select:
        raise QueryError("query has no output columns")

    rows = _scan(_sources(plan), tables)

    where = _pick(plan, ("where", "where_clause", "filter"))
    if where is not None:
        rows = [row for row in rows if _truth(_eval(where, _Scope(row)))]

    group_by = tuple(_pick(plan, ("group_by", "group", "groups", "grouping"), ()) or ())
    having = _pick(plan, ("having", "having_clause"))
    order_by = tuple(_pick(plan, ("order_by", "order", "ordering"), ()) or ())
    aggregates = tuple(_pick(plan, ("aggregates", "agg_calls", "aggregate_calls"), ()) or ())

    grouped = bool(group_by) or bool(aggregates) or _uses_aggregate(select, having, order_by)
    if grouped:
        entries = _grouped_entries(plan, rows, select, group_by, having, order_by, aggregates)
    else:
        entries = [
            (_project(select, _Scope(row)), _sort_keys(order_by, _Scope(row)))
            for row in rows
        ]

    if _pick(plan, ("distinct", "is_distinct"), False):
        entries = _distinct(entries)
    if order_by:
        entries = _sort(order_by, entries)

    return _slice(plan, [row for row, _keys in entries])


def _pick(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    """Read the first of `names` that `obj` provides, as attribute or mapping key."""
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _sources(plan: Any) -> list[tuple[str, str, Any]]:
    """Return the FROM/JOIN tables as (table name, alias, ON condition) in order."""
    refs = list(_pick(plan, ("from_tables", "tables", "sources", "from_", "from"), ()) or ())
    if not refs:
        base = _pick(plan, ("from_table", "base_table", "table"))
        if base is not None:
            refs = [base]
    if not refs:
        raise QueryError("query has no FROM table")

    conditions = list(_pick(plan, ("join_conditions", "on_conditions", "join_on"), ()) or ())
    sources: list[tuple[str, str, Any]] = []
    for position, ref in enumerate(refs):
        name, alias = _table_name_alias(ref)
        condition = None
        if position and position - 1 < len(conditions):
            condition = conditions[position - 1]
        sources.append((name, alias, condition))

    for entry in list(_pick(plan, ("joins", "join_list"), ()) or ()):
        table = _pick(entry, ("table", "ref", "target", "right"))
        if table is None:
            continue
        name, alias = _table_name_alias(table)
        if any(alias == known for _name, known, _cond in sources):
            continue
        condition = _pick(entry, ("condition", "on", "on_condition", "predicate"))
        sources.append((name, alias, condition))

    return sources


def _table_name_alias(ref: Any) -> tuple[str, str]:
    if isinstance(ref, str):
        return ref, ref
    if isinstance(ref, (tuple, list)) and ref:
        name = ref[0]
        alias = ref[1] if len(ref) > 1 and ref[1] else name
        return _as_name(name), _as_name(alias)
    name = _pick(ref, ("name", "table", "table_name"))
    alias = _pick(ref, ("alias", "as_name"))
    if name is None:
        raise QueryError("FROM entry has no table name")
    return _as_name(name), _as_name(alias if alias else name)


def _as_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise QueryError("table name and alias must be non-empty strings")
    return value


def _scan(sources: list[tuple[str, str, Any]], tables: Tables) -> list[_JoinedRow]:
    first_name, first_alias, _condition = sources[0]
    joined: list[_JoinedRow] = [{first_alias: source} for source in _table_rows(first_name, tables)]

    for name, alias, condition in sources[1:]:
        right = _table_rows(name, tables)
        combined: list[_JoinedRow] = []
        for left in joined:
            for source in right:
                candidate = dict(left)
                candidate[alias] = source
                if condition is None or _truth(_eval(condition, _Scope(candidate))):
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


def _grouped_entries(
    plan: Any,
    rows: list[_JoinedRow],
    select: tuple[Any, ...],
    group_by: tuple[Any, ...],
    having: Any,
    order_by: tuple[Any, ...],
    aggregates: tuple[Any, ...],
) -> list[tuple[Row, tuple[Any, ...]]]:
    entries: list[tuple[Row, tuple[Any, ...]]] = []
    for representative, members in _group(group_by, rows):
        scope = _Scope(representative, members, _aggregate_values(aggregates, members))
        if having is not None and not _truth(_eval(having, scope)):
            continue
        entries.append((_project(select, scope), _sort_keys(order_by, scope)))
    return entries


def _group(
    group_by: tuple[Any, ...], rows: list[_JoinedRow]
) -> list[tuple[_JoinedRow, list[_JoinedRow]]]:
    if not group_by:
        return [(rows[0] if rows else {}, rows)]

    buckets: dict[tuple[Any, ...], tuple[_JoinedRow, list[_JoinedRow]]] = {}
    for row in rows:
        key = tuple(_value_key(_eval(expr, _Scope(row))) for expr in group_by)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = (row, [row])
        else:
            bucket[1].append(row)
    return list(buckets.values())


def _aggregate_values(aggregates: tuple[Any, ...], members: list[_JoinedRow]) -> list[Any]:
    computed: list[Any] = []
    for call in aggregates:
        name = _aggregate_name(_pick(call, ("func", "name", "function", "op")))
        computed.append(_apply_aggregate(name, _aggregate_inputs(call, members)))
    return computed


def _aggregate_inputs(call: Any, members: list[_JoinedRow]) -> list[Any]:
    argument = _aggregate_argument(call)
    if argument is None:
        return [1] * len(members)
    return [_eval(argument, _Scope(row)) for row in members]


def _aggregate_argument(call: Any) -> Any:
    argument = _pick(call, ("arg", "argument", "expr", "operand"))
    if argument is None:
        args = _pick(call, ("args", "arguments"), ())
        if isinstance(args, (tuple, list)) and args:
            argument = args[0]
    if isinstance(argument, str) and argument == "*":
        return None
    return argument


def _aggregate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise QueryError("aggregate call has no function name")
    name = value.upper()
    if name not in _AGGREGATE_NAMES:
        raise QueryError(f"unknown aggregate function '{value}'")
    return name


def _project(select: tuple[Any, ...], scope: _Scope) -> Row:
    row: Row = {}
    for item in select:
        expr = _pick(item, ("expr", "expression", "value"))
        if expr is None:
            raise QueryError("select item has no expression")
        row[_output_name(item, expr)] = _eval(expr, scope)
    return row


def _output_name(item: Any, expr: Any) -> str:
    name = _pick(item, ("name", "alias", "output_name", "label"))
    if isinstance(name, str) and name:
        return name
    if _node_kind(expr) == "column":
        column = _pick(expr, ("name", "column", "column_name"))
        if isinstance(column, str) and column:
            return column
    raise QueryError("select item has no output name")


def _sort_keys(order_by: tuple[Any, ...], scope: _Scope) -> tuple[Any, ...]:
    return tuple(_eval(_order_expr(item), scope) for item in order_by)


def _order_expr(item: Any) -> Any:
    expr = _pick(item, ("expr", "expression", "key"))
    if expr is None:
        raise QueryError("ORDER BY item has no expression")
    return expr


def _order_descending(item: Any) -> bool:
    flag = _pick(item, ("descending", "desc", "is_descending"), None)
    if isinstance(flag, bool):
        return flag
    direction = _pick(item, ("direction", "order", "sort"), None)
    if isinstance(direction, str):
        return direction.upper() in {"DESC", "DESCENDING"}
    return bool(flag)


def _uses_aggregate(select: tuple[Any, ...], having: Any, order_by: tuple[Any, ...]) -> bool:
    if having is not None and _contains_aggregate(having):
        return True
    for item in select:
        if _contains_aggregate(_pick(item, ("expr", "expression", "value"))):
            return True
    for item in order_by:
        if _contains_aggregate(_pick(item, ("expr", "expression", "key"))):
            return True
    return False


def _contains_aggregate(expr: Any) -> bool:
    if expr is None:
        return False
    kind = _node_kind(expr)
    if kind in {"aggref", "aggregate"}:
        return True
    for child in _children(expr):
        if _contains_aggregate(child):
            return True
    return False


def _children(expr: Any) -> list[Any]:
    found: list[Any] = []
    for name in ("operand", "left", "right", "pattern", "arg", "expr", "expression"):
        child = _pick(expr, (name,))
        if child is not None and _is_node(child):
            found.append(child)
    for name in ("args", "items", "arguments", "values"):
        group = _pick(expr, (name,), ())
        if isinstance(group, (tuple, list)):
            found.extend(child for child in group if _is_node(child))
    return found


def _is_node(value: Any) -> bool:
    return not isinstance(value, (str, bytes, int, float, bool, type(None)))


def _node_kind(expr: Any) -> str:
    kind = _KIND_BY_CLASS.get(type(expr).__name__.lower().replace("_", ""))
    if kind is not None:
        return _refine_kind(expr, kind)
    if isinstance(expr, dict):
        tag = expr.get("kind") or expr.get("type") or expr.get("node")
        if isinstance(tag, str):
            named = _KIND_BY_CLASS.get(tag.lower().replace("_", ""))
            if named is not None:
                return _refine_kind(expr, named)
    return _refine_kind(expr, _kind_from_shape(expr))


def _kind_from_shape(expr: Any) -> str:
    has = _has_field
    if has(expr, "op") and has(expr, "left") and has(expr, "right"):
        return "binary"
    if has(expr, "op") and has(expr, "operand"):
        return "unary"
    if has(expr, "operand") and has(expr, "pattern"):
        return "like"
    if has(expr, "operand") and has(expr, "items"):
        return "inlist"
    if has(expr, "operand") and has(expr, "negated"):
        return "isnull"
    if has(expr, "func") or (has(expr, "name") and (has(expr, "arg") or has(expr, "args"))):
        return "func"
    if has(expr, "index") and not has(expr, "name"):
        return "aggref"
    if has(expr, "name") or has(expr, "column"):
        return "column"
    if has(expr, "value"):
        return "literal"
    raise QueryError(f"unsupported expression of type {type(expr).__name__}")


def _refine_kind(expr: Any, kind: str) -> str:
    if kind in {"func", "aggregate"}:
        name = _pick(expr, ("name", "func", "function", "op"))
        if isinstance(name, str) and name.upper() in _AGGREGATE_NAMES:
            return "aggregate"
        return "func"
    return kind


def _has_field(expr: Any, name: str) -> bool:
    if isinstance(expr, dict):
        return name in expr
    return hasattr(expr, name)


def _eval(expr: Any, scope: _Scope) -> Any:
    kind = _node_kind(expr)
    if kind == "literal":
        return _pick(expr, ("value", "literal", "constant"))
    if kind == "column":
        return _lookup(expr, scope)
    if kind == "aggref":
        return _eval_aggref(expr, scope)
    if kind == "aggregate":
        return _eval_aggregate(expr, scope)
    if kind == "unary":
        return _eval_unary(expr, scope)
    if kind == "binary":
        return _eval_binary(expr, scope)
    if kind == "func":
        return _eval_func(expr, scope)
    if kind == "isnull":
        is_null = _eval(_operand(expr), scope) is None
        negated = bool(_pick(expr, ("negated", "is_not", "inverted"), False))
        return (not is_null) if negated else is_null
    if kind == "inlist":
        return _eval_in(expr, scope)
    if kind == "like":
        value = _eval(_operand(expr), scope)
        pattern = _eval(_pick(expr, ("pattern",)), scope)
        return _like_match(value, pattern)
    raise QueryError(f"unsupported expression of type {type(expr).__name__}")


def _operand(expr: Any) -> Any:
    operand = _pick(expr, ("operand", "expr", "expression", "value"))
    if operand is None:
        raise QueryError("expression has no operand")
    return operand


def _lookup(expr: Any, scope: _Scope) -> Any:
    name = _pick(expr, ("name", "column", "column_name", "field"))
    if not isinstance(name, str):
        raise QueryError("column reference has no name")
    qualifier = _pick(expr, ("alias", "table", "qualifier", "table_alias"))

    if isinstance(qualifier, str) and qualifier:
        source = scope.row.get(qualifier)
        if not isinstance(source, dict):
            return None
        return source.get(name)

    found = False
    value: Any = None
    for source in scope.row.values():
        if isinstance(source, dict) and name in source:
            if found:
                raise QueryError(f"ambiguous column '{name}'; qualify it with a table name")
            found = True
            value = source[name]
    return value


def _eval_aggref(expr: Any, scope: _Scope) -> Any:
    if scope.agg_values is None:
        raise QueryError("aggregate function is not allowed here")
    index = _pick(expr, ("index", "slot", "position"))
    if isinstance(index, bool) or not isinstance(index, int):
        raise QueryError("aggregate reference has no index")
    if not 0 <= index < len(scope.agg_values):
        raise QueryError("aggregate reference out of range")
    return scope.agg_values[index]


def _eval_aggregate(expr: Any, scope: _Scope) -> Any:
    if scope.members is None:
        raise QueryError("aggregate function is not allowed here")
    name = _aggregate_name(_pick(expr, ("name", "func", "function", "op")))
    return _apply_aggregate(name, _aggregate_inputs(expr, scope.members))


def _eval_unary(expr: Any, scope: _Scope) -> Any:
    raw = _pick(expr, ("op", "operator"))
    op = raw.upper() if isinstance(raw, str) else raw
    value = _eval(_operand(expr), scope)
    if op == "-":
        if value is None:
            return None
        return -_number(value, "unary '-'")
    if op == "+":
        if value is None:
            return None
        return _number(value, "unary '+'")
    if op == "NOT":
        boolean = _boolean(value, "NOT")
        return None if boolean is None else not boolean
    raise QueryError(f"unsupported unary operator '{raw}'")


def _eval_binary(expr: Any, scope: _Scope) -> Any:
    raw = _pick(expr, ("op", "operator"))
    op = raw.upper() if isinstance(raw, str) else raw
    left_expr = _pick(expr, ("left",))
    right_expr = _pick(expr, ("right",))
    if left_expr is None or right_expr is None:
        raise QueryError("binary expression is missing an operand")

    if op == "AND":
        left = _boolean(_eval(left_expr, scope), "AND")
        if left is False:
            return False
        right = _boolean(_eval(right_expr, scope), "AND")
        if right is False:
            return False
        return None if left is None or right is None else True
    if op == "OR":
        left = _boolean(_eval(left_expr, scope), "OR")
        if left is True:
            return True
        right = _boolean(_eval(right_expr, scope), "OR")
        if right is True:
            return True
        return None if left is None or right is None else False

    left_value = _eval(left_expr, scope)
    right_value = _eval(right_expr, scope)
    if op in _COMPARISONS:
        return _compare(op, left_value, right_value)
    if op in _ARITHMETIC:
        return _arithmetic(op, left_value, right_value)
    raise QueryError(f"unsupported operator '{raw}'")


def _eval_func(expr: Any, scope: _Scope) -> Any:
    raw = _pick(expr, ("name", "func", "function", "op"))
    if not isinstance(raw, str):
        raise QueryError("function call has no name")
    name = raw.upper()
    if name not in _SCALAR_NAMES:
        raise QueryError(f"unknown function '{raw}'")

    args = _pick(expr, ("args", "arguments"), None)
    if args is None:
        single = _pick(expr, ("arg", "argument", "operand"))
        args = () if single is None else (single,)
    if not isinstance(args, (tuple, list)):
        raise QueryError(f"function '{raw}' has malformed arguments")
    values = [_eval(arg, scope) for arg in args]
    return _apply_scalar(name, values)


def _eval_in(expr: Any, scope: _Scope) -> bool | None:
    value = _eval(_operand(expr), scope)
    items = _pick(expr, ("items", "values", "list", "elements"), ())
    if not isinstance(items, (tuple, list)) or not items:
        raise QueryError("IN requires at least one value")
    unknown = False
    for item in items:
        outcome = _compare("=", value, _eval(item, scope))
        if outcome is True:
            return True
        if outcome is None:
            unknown = True
    return None if unknown else False


def _apply_scalar(name: str, values: list[Any]) -> Any:
    if name == "COALESCE":
        if not values:
            raise QueryError("COALESCE requires at least one argument")
        for value in values:
            if value is not None:
                return value
        return None
    if len(values) != 1:
        raise QueryError(f"{name} takes exactly one argument")
    value = values[0]
    if value is None:
        return None
    if name == "ABS":
        return abs(_number(value, "ABS"))
    if not isinstance(value, str):
        raise QueryError(f"{name} requires a string argument")
    if name == "UPPER":
        return value.upper()
    if name == "LOWER":
        return value.lower()
    return len(value)


def _apply_aggregate(name: str, values: list[Any]) -> Any:
    if name == "COUNT":
        return sum(1 for value in values if value is not None)

    present = [value for value in values if value is not None]
    if not present:
        return None
    if name == "SUM":
        return sum(_number(value, "SUM") for value in present)
    if name == "AVG":
        return float(sum(_number(value, "AVG") for value in present)) / len(present)

    best = present[0]
    for value in present[1:]:
        outcome = _order_cmp(value, best)
        if (outcome < 0) if name == "MIN" else (outcome > 0):
            best = value
    return best


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


def _compare(op: str, left: Any, right: Any) -> bool | None:
    if op not in _COMPARISONS:
        raise QueryError(f"unknown comparison operator '{op}'")
    if left is None or right is None:
        return None
    outcome = _order_cmp(left, right)
    if op == "=":
        return outcome == 0
    if op in {"<>", "!="}:
        return outcome != 0
    if op == "<":
        return outcome < 0
    if op == "<=":
        return outcome <= 0
    if op == ">":
        return outcome > 0
    return outcome >= 0


def _order_cmp(left: Any, right: Any) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    if _value_kind(left) != _value_kind(right):
        raise QueryError("mixed-type comparison is not allowed")
    if left < right:
        return -1
    if right < left:
        return 1
    return 0


def _value_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    raise QueryError(f"unsupported value of type {type(value).__name__}")


def _like_match(value: Any, pattern: Any) -> bool | None:
    if value is None or pattern is None:
        return None
    if not isinstance(value, str) or not isinstance(pattern, str):
        raise QueryError("LIKE requires string operands")
    parts = ["(?s)\\A"]
    for char in pattern:
        if char == "%":
            parts.append(".*")
        elif char == "_":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    parts.append("\\Z")
    return re.match("".join(parts), value) is not None


def _number(value: Any, what: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QueryError(f"{what} requires a numeric operand")
    return value


def _boolean(value: Any, what: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise QueryError(f"{what} requires a boolean operand")


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


def _sort(
    order_by: tuple[Any, ...], entries: list[tuple[Row, tuple[Any, ...]]]
) -> list[tuple[Row, tuple[Any, ...]]]:
    flags = tuple(_order_descending(item) for item in order_by)

    def compare_keys(left: tuple[Any, ...], right: tuple[Any, ...]) -> int:
        for lhs, rhs, descending in zip(left, right, flags):
            outcome = _order_cmp(lhs, rhs)
            if outcome:
                return -outcome if descending else outcome
        return 0

    wrapper = cmp_to_key(compare_keys)
    return sorted(entries, key=lambda entry: wrapper(entry[1]))


def _slice(plan: Any, rows: list[Row]) -> list[Row]:
    offset = _pick(plan, ("offset", "skip"))
    if offset is not None:
        rows = rows[_count(offset, "OFFSET"):]
    limit = _pick(plan, ("limit", "row_limit"))
    if limit is not None:
        rows = rows[: _count(limit, "LIMIT")]
    return rows


def _count(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{what} must be an integer")
    if value < 0:
        raise QueryError(f"{what} must not be negative")
    return value


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
