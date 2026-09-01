You own `minidb/api.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/planner.py (published exports: ["plan", "Plan", "TableRef", "SelectItem", "OrderItem", "AggregateCall", "Expr", "Literal", "Column", "Unary", "Binary", "Func", "InList", "IsNull", "Like", "AggRef", "AGGREGATE_FUNCTIONS", "SCALAR_FUNCTIONS"])
```python
"""Name resolution, aggregate classification and validation for minidb.

The planner turns the parser's syntax tree into a fully resolved :class:`Plan`:
every column is bound to one table alias, every aggregate is hoisted into
``Plan.aggregates`` and replaced by an :class:`AggRef`, output names are
computed, and every error the specification lets the planner detect is raised
as a :class:`~minidb.errors.QueryError`.

The syntax tree owned by ``minidb.nodes`` published no interface, so this
module reads it structurally: a node is recognised by the fields it carries
(``op``/``left``/``right``, ``name``/``args``, ``operand``/``pattern`` and so
on), accepting both attribute-style nodes and mapping-style nodes, and never
depending on a particular class name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import QueryError

__all__ = [
    "AggRef",
    "AggregateCall",
    "Binary",
    "Column",
    "Expr",
    "Func",
    "InList",
    "IsNull",
    "Like",
    "Literal",
    "OrderItem",
    "Plan",
    "SelectItem",
    "TableRef",
    "Unary",
    "plan",
]


@dataclass(frozen=True)
class Literal:
    """A constant value; SQL ``NULL`` is ``None``."""

    value: object


@dataclass(frozen=True)
class Column:
    """A column reference resolved to one table alias of the query."""

    alias: str
    name: str


@dataclass(frozen=True)
class Unary:
    """``-x`` or ``NOT x``."""

    op: str
    operand: "Expr"


@dataclass(frozen=True)
class Binary:
    """An arithmetic, comparison or logical operator over two operands."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Func:
    """A scalar function call with validated name and arity."""

    name: str
    args: tuple["Expr", ...]


@dataclass(frozen=True)
class IsNull:
    """``x IS NULL`` or, with ``negated``, ``x IS NOT NULL``."""

    operand: "Expr"
    negated: bool


@dataclass(frozen=True)
class InList:
    """``x IN (a, b, ...)``."""

    operand: "Expr"
    items: tuple["Expr", ...]


@dataclass(frozen=True)
class Like:
    """``x LIKE 'pat'``."""

    operand: "Expr"
    pattern: "Expr"


@dataclass(frozen=True)
class AggRef:
    """Placeholder for ``Plan.aggregates[index]`` evaluated for the group."""

    index: int


Expr = Literal | Column | Unary | Binary | Func | IsNull | InList | Like | AggRef


@dataclass(frozen=True)
class TableRef:
    """One table of the ``FROM``/``JOIN`` chain and the alias it is known by."""

    name: str
    alias: str


@dataclass(frozen=True)
class SelectItem:
    """One output column: its final name and the expression producing it."""

    name: str
    expr: Expr


@dataclass(frozen=True)
class OrderItem:
    """One ``ORDER BY`` key."""

    expr: Expr
    descending: bool


@dataclass(frozen=True)
class AggregateCall:
    """One aggregate; ``arg`` is ``None`` exactly for ``COUNT(*)``."""

    func: str
    arg: Expr | None


@dataclass(frozen=True)
class Plan:
    """A validated, fully resolved query, ready for the engine."""

    from_tables: tuple[TableRef, ...]
    join_conditions: tuple[Expr, ...]
    select: tuple[SelectItem, ...]
    where: Expr | None
    group_by: tuple[Expr, ...]
    having: Expr | None
    order_by: tuple[OrderItem, ...]
    distinct: bool
    limit: int | None
    offset: int | None
    is_aggregate: bool
    aggregates: tuple[AggregateCall, ...]


AGGREGATE_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
SCALAR_FUNCTIONS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

_ARITHMETIC_OPS = frozenset({"+", "-", "*", "/"})
_COMPARISON_OPS = frozenset({"=", "<>", "<", "<=", ">", ">="})
_LOGICAL_OPS = frozenset({"AND", "OR"})
_BINARY_OPS = _ARITHMETIC_OPS | _COMPARISON_OPS | _LOGICAL_OPS
_ONE_ARG_SCALARS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS"})

_MISSING = object()
_ABSENT = object()


def _field(node: Any, *names: str, default: Any = _MISSING) -> Any:
    """First of ``names`` present on ``node``, as attribute or mapping key."""

    if isinstance(node, Mapping):
        for name in names:
            if name in node:
                return node[name]
    else:
        for name in names:
            value = getattr(node, name, _MISSING)
            if value is not _MISSING:
                return value
    if default is _MISSING:
        raise QueryError("the planner cannot understand this parse tree")
    return default


def _has(node: Any, *names: str) -> bool:
    return _field(node, *names, default=_ABSENT) is not _ABSENT


def _sequence(value: Any) -> tuple[Any, ...]:
    """Normalise an optional node sequence to a tuple."""

    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _normalise_op(op: Any) -> str:
    if not isinstance(op, str):
        raise QueryError("unsupported operator in the query")
    text = op.strip()
    upper = text.upper()
    if upper in {"AND", "OR", "NOT"}:
        return upper
    if text == "!=":
        return "<>"
    if text == "==":
        return "="
    return text


def _is_star(node: Any) -> bool:
    """True when ``node`` denotes ``*`` or ``t.*``."""

    if isinstance(node, str):
        text = node.strip()
        return text == "*" or text.endswith(".*")
    if "star" in type(node).__name__.lower():
        return True
    kind = _field(node, "type", "kind", default=None)
    if isinstance(kind, str) and kind.strip().lower() in {"star", "wildcard", "asterisk"}:
        return True
    if _has(node, "args", "arguments"):
        return False
    name = _field(node, "name", "column", default=None)
    return name == "*"


def _star_qualifier(node: Any) -> str | None:
    """The table qualifier of a star node, or ``None`` for a bare ``*``."""

    if isinstance(node, str):
        text = node.strip()
        return None if text == "*" else text[:-2] or None
    qualifier = _field(node, "table", "qualifier", "prefix", "table_name", default=None)
    if isinstance(qualifier, str) and qualifier:
        return qualifier
    return None


class _Planner:
    """Resolution state for a single statement."""

    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        self.from_tables: list[TableRef] = []
        self.columns_by_alias: dict[str, tuple[str, ...] | None] = {}
        self.aliases_of_name: dict[str, list[str]] = {}
        self.aggregates: list[AggregateCall] = []
        self.aggregate_index: dict[AggregateCall, int] = {}

    # -- tables ---------------------------------------------------------

    def add_table(self, ref: Any) -> None:
        if isinstance(ref, str):
            name: Any = ref
            written_alias: Any = None
        else:
            name = _field(ref, "name", "table", "table_name")
            written_alias = _field(ref, "alias", "as_name", default=None)
        if not isinstance(name, str):
            raise QueryError("a FROM entry has no table name")
        if name not in self.tables:
            raise QueryError(f"unknown table {name!r}")
        rows = self.tables[name]
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise QueryError(f"table {name!r} is not a list of rows")
        alias = written_alias if isinstance(written_alias, str) and written_alias else name
        if alias in self.columns_by_alias:
            raise QueryError(f"duplicate table alias {alias!r}")
        self.from_tables.append(TableRef(name=name, alias=alias))
        self.columns_by_alias[alias] = self._schema_of(name, rows)
        self.aliases_of_name.setdefault(name, []).append(alias)

    @staticmethod
    def _schema_of(name: str, rows: Sequence[Any]) -> tuple[str, ...] | None:
        """Column names of a table, or ``None`` when the table has no rows."""

        if len(rows) == 0:
            return None
        first = rows[0]
        if not isinstance(first, Mapping):
            raise QueryError(f"a row of table {name!r} is not a dict")
        return tuple(str(key) for key in first)

    def alias_for_qualifier(self, qualifier: str) -> str:
        if qualifier in self.columns_by_alias:
            return qualifier
        candidates = self.aliases_of_name.get(qualifier, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise QueryError(f"ambiguous table reference {qualifier!r}")
        raise QueryError(f"unknown table {qualifier!r}")

    def resolve_column(self, qualifier: Any, name: Any) -> Column:
        if not isinstance(name, str) or not name:
            raise QueryError("a column reference has no name")
        if isinstance(qualifier, str) and qualifier:
            alias = self.alias_for_qualifier(qualifier)
            known = self.columns_by_alias[alias]
# ... 1 further lines of this file were not included in this review excerpt ...

```

### minidb/engine.py (published exports: ["Row", "Tables", "execute"])
```python
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
# ... 1 further lines of this file were not included in this review excerpt ...

```