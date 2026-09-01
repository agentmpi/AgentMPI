"""Evaluation of a validated minidb query plan against in-memory tables.

Value semantics -- comparison, ordering, LIKE, the scalar and aggregate functions --
belong to `minidb.functions`, so they are delegated to it whenever it provides them;
the local equivalents here are only a fallback for a name that module does not
publish, since no interface for it was published to this module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cmp_to_key
from typing import Any, TypeAlias

from . import functions as _functions
from . import planner as _planner
from .errors import QueryError

__all__ = ["Row", "Tables", "execute"]

Row: TypeAlias = dict[str, Any]
Tables: TypeAlias = dict[str, list[Row]]

_JoinedRow: TypeAlias = dict[str, Row]

_MISSING = object()

_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})
_AGGREGATE_NAMES = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
_SCALAR_NAMES = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

_PEER_SCALARS = getattr(_functions, "SCALARS", None)
_PEER_AGGREGATES = getattr(_functions, "AGGREGATES", None)
_PEER_COMPARE = getattr(_functions, "compare", None)
_PEER_LIKE_MATCH = getattr(_functions, "like_match", None)
_PEER_ORDER_CMP = getattr(_functions, "order_cmp", None)


def _planner_node_kinds() -> dict[type, str]:
    """Dispatch table keyed by the planner's own expression classes."""
    pairs = (
        ("Literal", "literal"),
        ("Column", "column"),
        ("Unary", "unary"),
        ("Binary", "binary"),
        ("Func", "func"),
        ("IsNull", "isnull"),
        ("InList", "inlist"),
        ("Like", "like"),
        ("AggRef", "aggref"),
        ("AggregateCall", "aggregate"),
    )
    found: dict[type, str] = {}
    for name, kind in pairs:
        node_type = getattr(_planner, name, None)
        if isinstance(node_type, type):
            found[node_type] = kind
    return found


_NODE_KIND_BY_TYPE = _planner_node_kinds()

_KIND_BY_CLASS_NAME = {
    "literal": "literal",
    "const": "literal",
    "constant": "literal",
    "number": "literal",
    "string": "literal",
    "column": "column",
    "columnref": "column",
    "col": "column",
    "field": "column",
    "fieldref": "column",
    "unary": "unary",
    "unaryop": "unary",
    "unaryexpr": "unary",
    "binary": "binary",
    "binaryop": "binary",
    "binop": "binary",
    "binaryexpr": "binary",
    "arithop": "binary",
    "comparison": "binary",
    "func": "func",
    "function": "func",
    "funccall": "func",
    "functioncall": "func",
    "call": "func",
    "scalar": "func",
    "scalarcall": "func",
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
    "agg": "aggregate",
    "aggcall": "aggregate",
    "aggregate": "aggregate",
    "aggregatecall": "aggregate",
    "aggregateexpr": "aggregate",
    "aggregation": "aggregate",
}


class _Scope:
    """The evaluation context for one output row."""

    __slots__ = ("row", "members", "agg_values", "columns_allowed")

    def __init__(
        self,
        row: _JoinedRow,
        members: list[_JoinedRow] | None = None,
        agg_values: list[Any] | None = None,
        columns_allowed: bool = True,
    ) -> None:
        self.row = row
        self.members = members
        self.agg_values = agg_values
        self.columns_allowed = columns_allowed


def execute(plan: Any, tables: Tables) -> list[Row]:
    """Evaluate `plan` against `tables` and return the result rows."""
    try:
        return _execute(plan, tables)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"query evaluation failed: {exc}") from exc


def _execute(plan: Any, tables: Tables) -> list[Row]:
    if not isinstance(tables, (dict, Mapping)):
        raise QueryError("tables must be a mapping of table name to list of rows")

    select = _as_tuple(_field(plan, ("select", "select_items"), what="select list"))
    where = _field(plan, ("where",), default=None)
    group_by = _as_tuple(_field(plan, ("group_by", "grouping"), default=()))
    having = _field(plan, ("having",), default=None)
    order_by = _as_tuple(_field(plan, ("order_by", "ordering"), default=()))
    aggregates = _as_tuple(_field(plan, ("aggregates",), default=()))
    is_aggregate = bool(_field(plan, ("is_aggregate",), default=False))

    rows = _scan(_sources(plan), tables)
    if where is not None:
        rows = [row for row in rows if _truth(_eval(where, _Scope(row)))]

    grouped = bool(group_by) or bool(aggregates) or is_aggregate or _uses_aggregate(
        select, having, order_by
    )
    if grouped:
        entries = _grouped_entries(rows, select, group_by, having, order_by, aggregates)
    else:
        entries = []
        for row in rows:
            scope = _Scope(row)
            if having is not None and not _truth(_eval(having, scope)):
                continue
            entries.append((_project(select, scope), _sort_keys(order_by, scope)))

    if _field(plan, ("distinct",), default=False):
        entries = _distinct(entries)
    if order_by:
        entries = _sort(order_by, entries)

    return _slice(plan, [row for row, _keys in entries])


# --- structural access to the plan ---------------------------------------


def _field(obj: Any, names: tuple[str, ...], default: Any = _MISSING, what: str = "") -> Any:
    """First of `names` that `obj` actually carries, as attribute or mapping key.

    Presence, not truth, decides: a field explicitly set to None is returned as
    None instead of falling through to the next candidate name.
    """
    if isinstance(obj, Mapping):
        for name in names:
            if name in obj:
                return obj[name]
    else:
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
    if default is _MISSING:
        raise QueryError(f"the query plan has no {what or names[0]}")
    return default


def _has_field(obj: Any, name: str) -> bool:
    if isinstance(obj, Mapping):
        return name in obj
    return hasattr(obj, name)


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _sources(plan: Any) -> list[tuple[str, str, Any]]:
    """The FROM/JOIN tables as (table name, alias, ON condition), in order."""
    refs = list(_as_tuple(_field(plan, ("from_tables", "from_", "sources"), default=())))
    if not refs:
        base = _field(plan, ("from_table", "base_table"), default=None)
        if base is None:
            raise QueryError("the query plan has no FROM table")
        refs = [base]

    conditions = list(_as_tuple(_field(plan, ("join_conditions", "on_conditions"), default=())))
    joins = list(_as_tuple(_field(plan, ("joins",), default=())))

    sources: list[tuple[str, str, Any]] = []
    for position, ref in enumerate(refs):
        name, alias = _table_name_alias(ref)
        condition: Any = None
        if position:
            index = position - 1
            if index >= len(conditions) and index < len(joins):
                condition = _field(joins[index], ("condition", "on"), default=None)
            elif index < len(conditions):
                condition = conditions[index]
            if condition is None:
                raise QueryError(f"join of table '{name}' has no ON condition")
        sources.append((name, alias, condition))

    for entry in joins:
        table = _field(entry, ("table", "ref", "target"), default=None)
        if table is None:
            continue
        name, alias = _table_name_alias(table)
        if any(alias == known for _name, known, _cond in sources):
            continue
        condition = _field(entry, ("condition", "on"), default=None)
        if condition is None:
            raise QueryError(f"join of table '{name}' has no ON condition")
        sources.append((name, alias, condition))

    return sources


def _table_name_alias(ref: Any) -> tuple[str, str]:
    if isinstance(ref, str):
        return ref, ref
    if isinstance(ref, (tuple, list)) and ref:
        name = ref[0]
        alias = ref[1] if len(ref) > 1 and ref[1] else name
        return _as_name(name), _as_name(alias)
    name = _field(ref, ("name", "table", "table_name"), what="table name")
    alias = _field(ref, ("alias",), default=None)
    return _as_name(name), _as_name(alias if alias else name)


def _as_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise QueryError("table names and aliases must be non-empty strings")
    return value


# --- row production ------------------------------------------------------


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
                if _truth(_eval(condition, _Scope(candidate))):
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
    rows: list[_JoinedRow],
    select: tuple[Any, ...],
    group_by: tuple[Any, ...],
    having: Any,
    order_by: tuple[Any, ...],
    aggregates: tuple[Any, ...],
) -> list[tuple[Row, tuple[Any, ...]]]:
    entries: list[tuple[Row, tuple[Any, ...]]] = []
    for representative, members in _group(group_by, rows):
        scope = _Scope(
            representative,
            members,
            _aggregate_values(aggregates, members),
            columns_allowed=bool(group_by),
        )
        if having is not None and not _truth(_eval(having, scope)):
            continue
        entries.append((_project(select, scope), _sort_keys(order_by, scope)))
    return entries


def _group(
    group_by: tuple[Any, ...], rows: list[_JoinedRow]
) -> list[tuple[_JoinedRow, list[_JoinedRow]]]:
    if not group_by:
        return [({}, rows)]

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
        name = _aggregate_name(_field(call, ("func", "name", "function"), what="aggregate name"))
        computed.append(_apply_aggregate(name, _aggregate_inputs(call, members)))
    return computed


def _aggregate_inputs(call: Any, members: list[_JoinedRow]) -> list[Any]:
    argument = _aggregate_argument(call)
    if argument is None:
        return [1] * len(members)
    return [_eval(argument, _Scope(row)) for row in members]


def _aggregate_argument(call: Any) -> Any:
    for name in ("arg", "argument", "operand"):
        if _has_field(call, name):
            argument = _field(call, (name,))
            return None if argument == "*" else argument
    args = _field(call, ("args", "arguments"), default=())
    if isinstance(args, (tuple, list)) and args:
        first = args[0]
        return None if first == "*" else first
    return None


def _aggregate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise QueryError("an aggregate call has no function name")
    name = value.upper()
    if name not in _AGGREGATE_NAMES:
        raise QueryError(f"unknown aggregate function '{value}'")
    return name


def _project(select: tuple[Any, ...], scope: _Scope) -> Row:
    row: Row = {}
    for item in select:
        expr = _field(item, ("expr", "expression"), what="select expression")
        row[_output_name(item, expr)] = _eval(expr, scope)
    return row


def _output_name(item: Any, expr: Any) -> str:
    name = _field(item, ("name", "alias", "output_name"), default=None)
    if isinstance(name, str) and name:
        return name
    if _node_kind(expr) == "column":
        column = _field(expr, ("name", "column"), default=None)
        if isinstance(column, str) and column:
            return column
    raise QueryError("a select item has no output name")


def _sort_keys(order_by: tuple[Any, ...], scope: _Scope) -> tuple[Any, ...]:
    return tuple(_eval(_order_expr(item), scope) for item in order_by)


def _order_expr(item: Any) -> Any:
    return _field(item, ("expr", "expression", "key"), what="ORDER BY expression")


def _order_descending(item: Any) -> bool:
    """True when this ORDER BY key sorts descending.

    Both polarities are accepted, because a plan may spell the flag either way.
    """
    for name in ("descending", "desc", "is_descending"):
        if _has_field(item, name):
            return _direction_is_descending(_field(item, (name,)), descending_flag=True)
    for name in ("ascending", "asc", "is_ascending"):
        if _has_field(item, name):
            return _direction_is_descending(_field(item, (name,)), descending_flag=False)
    for name in ("direction", "sort_order", "order"):
        if _has_field(item, name):
            return _direction_is_descending(_field(item, (name,)), descending_flag=True)
    return False


def _direction_is_descending(value: Any, descending_flag: bool) -> bool:
    if isinstance(value, str):
        text = value.strip().upper()
        if text in {"DESC", "DESCENDING"}:
            return True
        if text in {"ASC", "ASCENDING"}:
            return False
        return descending_flag if text == "TRUE" else not descending_flag
    if value is None:
        return False
    return bool(value) if descending_flag else not bool(value)


def _uses_aggregate(select: tuple[Any, ...], having: Any, order_by: tuple[Any, ...]) -> bool:
    if having is not None and _contains_aggregate(having):
        return True
    for item in select:
        if _contains_aggregate(_field(item, ("expr", "expression"), default=None)):
            return True
    for item in order_by:
        if _contains_aggregate(_field(item, ("expr", "expression", "key"), default=None)):
            return True
    return False


def _contains_aggregate(expr: Any) -> bool:
    if expr is None:
        return False
    if _node_kind(expr, strict=False) in {"aggref", "aggregate"}:
        return True
    return any(_contains_aggregate(child) for child in _children(expr))


def _children(expr: Any) -> list[Any]:
    found: list[Any] = []
    for name in ("operand", "left", "right", "pattern", "arg", "expr", "expression"):
        child = _field(expr, (name,), default=None)
        if child is not None and _is_node(child):
            found.append(child)
    for name in ("args", "items", "arguments", "values"):
        group = _field(expr, (name,), default=())
        if isinstance(group, (tuple, list)):
            found.extend(child for child in group if _is_node(child))
    return found


def _is_node(value: Any) -> bool:
    return not isinstance(value, (str, bytes, int, float, bool, type(None)))


# --- expression evaluation ----------------------------------------------


def _node_kind(expr: Any, strict: bool = True) -> str:
    kind = _NODE_KIND_BY_TYPE.get(type(expr))
    if kind is not None:
        return _refine_kind(expr, kind)
    kind = _KIND_BY_CLASS_NAME.get(type(expr).__name__.lower().replace("_", ""))
    if kind is not None:
        return _refine_kind(expr, kind)
    if isinstance(expr, Mapping):
        for tag_name in ("kind", "type", "node"):
            tag = expr.get(tag_name)
            if isinstance(tag, str):
                named = _KIND_BY_CLASS_NAME.get(tag.lower().replace("_", ""))
                if named is not None:
                    return _refine_kind(expr, named)
    return _refine_kind(expr, _kind_from_shape(expr, strict))


def _kind_from_shape(expr: Any, strict: bool) -> str:
    if _has_field(expr, "op") and _has_field(expr, "left") and _has_field(expr, "right"):
        return "binary"
    if _has_field(expr, "op") and _has_field(expr, "operand"):
        return "unary"
    if _has_field(expr, "operand") and _has_field(expr, "pattern"):
        return "like"
    if _has_field(expr, "operand") and _has_field(expr, "items"):
        return "inlist"
    if _has_field(expr, "operand") and _has_field(expr, "negated"):
        return "isnull"
    if _has_field(expr, "func"):
        return "aggregate"
    if _has_field(expr, "name") and (_has_field(expr, "args") or _has_field(expr, "arg")):
        return "func"
    if _has_field(expr, "index") and not _has_field(expr, "name"):
        return "aggref"
    if _has_field(expr, "name") or _has_field(expr, "column"):
        return "column"
    if _has_field(expr, "value"):
        return "literal"
    if not strict:
        return "unknown"
    raise QueryError(f"unsupported expression of type {type(expr).__name__}")


def _refine_kind(expr: Any, kind: str) -> str:
    if kind in {"func", "aggregate"}:
        name = _field(expr, ("name", "func", "function"), default=None)
        if isinstance(name, str) and name.upper() in _AGGREGATE_NAMES:
            return "aggregate"
        return "func"
    return kind


def _eval(expr: Any, scope: _Scope) -> Any:
    kind = _node_kind(expr)
    if kind == "literal":
        return _field(expr, ("value",), default=None)
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
        negated = bool(_field(expr, ("negated",), default=False))
        return (not is_null) if negated else is_null
    if kind == "inlist":
        return _eval_in(expr, scope)
    if kind == "like":
        value = _eval(_operand(expr), scope)
        pattern = _eval(_field(expr, ("pattern",), what="LIKE pattern"), scope)
        return _like_match(value, pattern)
    raise QueryError(f"unsupported expression of type {type(expr).__name__}")


def _operand(expr: Any) -> Any:
    return _field(expr, ("operand", "expr", "expression"), what="operand")


def _lookup(expr: Any, scope: _Scope) -> Any:
    name = _field(expr, ("name", "column", "column_name"), what="column name")
    if not isinstance(name, str):
        raise QueryError("a column reference has no name")
    if not scope.columns_allowed:
        raise QueryError(f"column '{name}' is not allowed without GROUP BY in an aggregate query")

    qualifier = _field(expr, ("alias", "table", "qualifier"), default=None)
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
        raise QueryError("an aggregate function is not allowed here")
    index = _field(expr, ("index", "slot"), what="aggregate index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise QueryError("an aggregate reference has no index")
    if not 0 <= index < len(scope.agg_values):
        raise QueryError("aggregate reference out of range")
    return scope.agg_values[index]


def _eval_aggregate(expr: Any, scope: _Scope) -> Any:
    if scope.members is None:
        raise QueryError("an aggregate function is not allowed here")
    name = _aggregate_name(_field(expr, ("name", "func", "function"), what="aggregate name"))
    return _apply_aggregate(name, _aggregate_inputs(expr, scope.members))


def _eval_unary(expr: Any, scope: _Scope) -> Any:
    raw = _field(expr, ("op", "operator"), what="unary operator")
    op = raw.upper() if isinstance(raw, str) else raw
    value = _eval(_operand(expr), scope)
    if op == "-":
        return None if value is None else -_number(value, "unary '-'")
    if op == "+":
        return None if value is None else _number(value, "unary '+'")
    if op == "NOT":
        boolean = _boolean(value, "NOT")
        return None if boolean is None else not boolean
    raise QueryError(f"unsupported unary operator '{raw}'")


def _eval_binary(expr: Any, scope: _Scope) -> Any:
    raw = _field(expr, ("op", "operator"), what="operator")
    op = raw.upper() if isinstance(raw, str) else raw
    left_expr = _field(expr, ("left",), what="left operand")
    right_expr = _field(expr, ("right",), what="right operand")

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
    raw = _field(expr, ("name", "func", "function"), what="function name")
    if not isinstance(raw, str):
        raise QueryError("a function call has no name")
    name = raw.upper()
    if name not in _SCALAR_NAMES:
        raise QueryError(f"unknown function '{raw}'")

    args = _field(expr, ("args", "arguments"), default=None)
    if args is None:
        single = _field(expr, ("arg", "operand"), default=None)
        args = () if single is None else (single,)
    if not isinstance(args, (tuple, list)):
        raise QueryError(f"function '{raw}' has malformed arguments")
    return _apply_scalar(name, [_eval(arg, scope) for arg in args])


def _eval_in(expr: Any, scope: _Scope) -> bool | None:
    value = _eval(_operand(expr), scope)
    items = _field(expr, ("items", "values", "elements"), default=())
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


# --- value semantics, delegated to functions when it provides them -------


def _apply_scalar(name: str, values: list[Any]) -> Any:
    if isinstance(_PEER_SCALARS, Mapping):
        peer = _PEER_SCALARS.get(name)
        if callable(peer):
            return peer(*values)
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
    if isinstance(_PEER_AGGREGATES, Mapping):
        peer = _PEER_AGGREGATES.get(name)
        if callable(peer):
            return peer(values)
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


def _compare(op: str, left: Any, right: Any) -> bool | None:
    if callable(_PEER_COMPARE):
        return _PEER_COMPARE(op, left, right)
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
    if callable(_PEER_ORDER_CMP):
        outcome = _PEER_ORDER_CMP(left, right)
        if isinstance(outcome, int):
            return outcome
        raise QueryError("the ordering comparator returned a non-integer result")
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    if callable(_PEER_COMPARE):
        if _PEER_COMPARE("=", left, right) is True:
            return 0
        return -1 if _PEER_COMPARE("<", left, right) is True else 1
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
    if callable(_PEER_LIKE_MATCH):
        return _PEER_LIKE_MATCH(value, pattern)
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


# --- output shaping ------------------------------------------------------


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
    offset = _field(plan, ("offset",), default=None)
    if offset is not None:
        rows = rows[_count(offset, "OFFSET"):]
    limit = _field(plan, ("limit",), default=None)
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
