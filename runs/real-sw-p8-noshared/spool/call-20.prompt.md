You own `minidb/engine.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/parser.py (published exports: ["parse"])
```python
'''Recursive-descent parser for the minidb SQL dialect.'''

from __future__ import annotations

import inspect
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Any

from .errors import QueryError
from .nodes import (
    BinOp,
    Column,
    Func,
    Join,
    Literal,
    OrderKey,
    Select,
    SelectItem,
    Star,
    TableRef,
    UnaryOp,
)
from .tokens import tokenize

__all__ = ['parse']

_KEYWORDS = frozenset({
    'SELECT', 'DISTINCT', 'FROM', 'AS', 'INNER', 'JOIN', 'ON', 'WHERE', 'GROUP',
    'BY', 'HAVING', 'ORDER', 'ASC', 'DESC', 'LIMIT', 'OFFSET', 'AND', 'OR',
    'NOT', 'IS', 'NULL', 'IN', 'LIKE',
})
_SYMBOLS = frozenset({
    '=', '<>', '!=', '<', '<=', '>', '>=', '+', '-', '*', '/', '(', ')', ',', '.',
})
_COMPARISON_OPS = frozenset({'=', '<>', '!=', '<', '<=', '>', '>='})

# The token `kind` vocabulary is not fixed by a published interface here, so each
# token is re-classified into one of our own categories, by kind when the kind is
# recognisable and by value otherwise.
_NUMBER_KINDS = frozenset({'NUMBER', 'NUM', 'INT', 'INTEGER', 'FLOAT', 'DECIMAL'})
_STRING_KINDS = frozenset({'STRING', 'STR', 'TEXT', 'LITERAL_STRING'})
_KEYWORD_KINDS = frozenset({'KEYWORD', 'KW', 'RESERVED'})
_IDENT_KINDS = frozenset({'IDENT', 'IDENTIFIER', 'NAME', 'WORD'})
_SYMBOL_KINDS = frozenset({'OP', 'OPERATOR', 'PUNCT', 'PUNCTUATION', 'SYMBOL', 'SYM'})
_NULL_KINDS = frozenset({'NULL', 'NONE'})
_SENTINEL_KINDS = frozenset({'EOF', 'END', 'ENDMARKER', 'EOL', 'WHITESPACE', 'SPACE'})

_SOURCE_FIELDS = frozenset({
    'source_text', 'sourcetext', 'source', 'text', 'raw', 'raw_text', 'rawtext',
    'src', 'source_sql', 'sql',
})
_NAME_FIELDS = frozenset({
    'name', 'output_name', 'outputname', 'out_name', 'output', 'column_name',
    'label_name', 'result_name',
})
_DIRECTION_FIELDS = frozenset({'direction', 'dir', 'order', 'sort', 'ordering'})
_JOIN_KIND_FIELDS = frozenset({'kind', 'type', 'join_type', 'jointype'})


class _Tok:
    '''A token reduced to the categories this parser reasons about.'''

    __slots__ = ('cat', 'value', 'pos')

    def __init__(self, cat: str, value: Any, pos: int) -> None:
        self.cat = cat
        self.value = value
        self.pos = pos


def _normalise(name: str) -> str:
    return name.strip('_').lower()


def _field_names(cls: Any) -> list[str]:
    '''The constructor field names of a node class, in declaration order.'''
    if is_dataclass(cls):
        return [field.name for field in dataclass_fields(cls) if field.init]
    try:
        signature = inspect.signature(cls)
    except (TypeError, ValueError):
        return []
    names: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            parameter.POSITIONAL_ONLY,
            parameter.POSITIONAL_OR_KEYWORD,
            parameter.KEYWORD_ONLY,
        ):
            names.append(parameter.name)
    return names


def _build(cls: Any, spec: tuple[tuple[frozenset[str], Any], ...]) -> Any:
    '''Instantiate a node class, mapping our values onto its actual field names.

    Field names are matched case-insensitively and ignoring surrounding
    underscores, so `from_`, `From` and `from` all receive the FROM table. If no
    name matches, the values are passed positionally in canonical order.
    '''
    names = _field_names(cls)
    if names:
        kwargs: dict[str, Any] = {}
        used: set[int] = set()
        for name in names:
            key = _normalise(name)
            for index, (candidates, value) in enumerate(spec):
                if index not in used and key in candidates:
                    kwargs[name] = value
                    used.add(index)
                    break
        if kwargs:
            try:
                return cls(**kwargs)
            except TypeError:
                pass
    values = [value for _, value in spec]
    try:
        return cls(*values)
    except TypeError:
        pass
    if names and len(names) < len(values):
        try:
            return cls(*values[:len(names)])
        except TypeError as exc:
            raise QueryError(
                'cannot construct AST node ' + getattr(cls, '__name__', 'node')
                + ': ' + str(exc)
            ) from exc
    raise QueryError('cannot construct AST node ' + getattr(cls, '__name__', 'node'))


def _make_column(name: str, table: str | None) -> Any:
    return _build(Column, (
        (frozenset({'name', 'column', 'col', 'column_name', 'columnname', 'ident'}), name),
        (frozenset({'table', 'qualifier', 'table_name', 'tablename', 'prefix', 'tbl', 'scope'}), table),
    ))


def _make_literal(value: Any) -> Any:
    return _build(Literal, ((frozenset({'value', 'val', 'literal', 'v', 'data'}), value),))


def _make_star(table: str | None) -> Any:
    return _build(Star, ((
        frozenset({'table', 'qualifier', 'table_name', 'tablename', 'prefix', 'tbl', 'scope'}),
        table,
    ),))


def _make_binop(op: str, left: Any, right: Any) -> Any:
    return _build(BinOp, (
        (frozenset({'op', 'operator', 'kind', 'symbol'}), op),
        (frozenset({'left', 'lhs', 'left_expr', 'leftexpr', 'a', 'first'}), left),
        (frozenset({'right', 'rhs', 'right_expr', 'rightexpr', 'b', 'second'}), right),
    ))


def _make_unaryop(op: str, operand: Any) -> Any:
    return _build(UnaryOp, (
        (frozenset({'op', 'operator', 'kind', 'symbol'}), op),
        (frozenset({
            'operand', 'expr', 'expression', 'value', 'arg', 'argument', 'right', 'child',
        }), operand),
    ))


def _make_func(name: str, args: tuple[Any, ...]) -> Any:
    return _build(Func, (
        (frozenset({
            'name', 'func', 'func_name', 'funcname', 'function', 'function_name', 'fname',
        }), name),
        (frozenset({
            'args', 'arguments', 'argv', 'params', 'parameters', 'operands', 'exprs',
        }), args),
    ))


def _make_table_ref(name: str, alias: str | None) -> Any:
    return _build(TableRef, (
        (frozenset({'name', 'table', 'table_name', 'tablename'}), name),
        (frozenset({'alias', 'as_name', 'asname', 'label'}), alias),
    ))


def _make_join(table: Any, condition: Any) -> Any:
    spec: list[tuple[frozenset[str], Any]] = [
        (frozenset({'table', 'table_ref', 'tableref', 'right', 'target', 'to'}), table),
        (frozenset({
            'on', 'condition', 'cond', 'on_condition', 'oncondition', 'predicate', 'expr',
        }), condition),
    ]
    if {_normalise(name) for name in _field_names(Join)} & _JOIN_KIND_FIELDS:
        spec.append((frozenset(_JOIN_KIND_FIELDS), 'INNER'))
    return _build(Join, tuple(spec))


def _make_order_key(expr: Any, descending: bool) -> Any:
    key_candidates = frozenset({'expr', 'expression', 'key', 'node', 'value'})
    if {_normalise(name) for name in _field_names(OrderKey)} & _DIRECTION_FIELDS:
        return _build(OrderKey, (
            (key_candidates, expr),
            (frozenset(_DIRECTION_FIELDS), 'DESC' if descending else 'ASC'),
        ))
    return _build(OrderKey, (
        (key_candidates, expr),
        (frozenset({
            'descending', 'desc', 'is_desc', 'isdesc', 'reverse', 'descending_flag',
        }), descending),
    ))


def _column_name_of(expr: Any) -> str | None:
    for attribute in ('name', 'column', 'col', 'column_name'):
        value = getattr(expr, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


def _output_name(expr: Any, alias: str | None, source_text: str) -> str:
    if alias is not None:
        return alias
    if isinstance(expr, Star):
        table = getattr(expr, 'table', None)
        if isinstance(table, str) and table:
            return table + '.*'
        return '*'
    stripped = ''.join(source_text.split())
    if isinstance(expr, Column):
        name = _column_name_of(expr)
        if name is not None:
            return name
        if '.' in stripped:
            return stripped.rsplit('.', 1)[1]
    return stripped


def _make_select_item(expr: Any, alias: str | None, source_text: str) -> Any:
    normalised = {_normalise(name) for name in _field_names(SelectItem)}
    spec: list[tuple[frozenset[str], Any]] = [
        (frozenset({'expr', 'expression', 'node'}), expr),
        (frozenset({'alias', 'as_name', 'asname', 'label'}), alias),
    ]
    if normalised & _NAME_FIELDS:
        spec.append((frozenset(_NAME_FIELDS), _output_name(expr, alias, source_text)))
    spec.append((frozenset(_SOURCE_FIELDS), source_text))
    return _build(SelectItem, tuple(spec))


def _make_select(
    items: tuple[Any, ...],
    from_table: Any,
    distinct: bool,
    joins: tuple[Any, ...],
    where: Any,
    group_by: tuple[Any, ...],
    having: Any,
    order_by: tuple[Any, ...],
    limit: int | None,
    offset: int | None,
) -> Any:
    return _build(Select, (
        (frozenset({
            'items', 'select', 'select_list', 'selectlist', 'select_items
```

### minidb/planner.py (published exports: ["Plan", "PlannedItem", "PlannedOrderKey", "plan", "resolve_column", "SCALAR_ARITY", "AGGREGATE_ARITY"])
```python
"""Name resolution, aggregate classification and validation for minidb.

The planner turns the parsed :class:`~minidb.nodes.Select` into a fully
validated :class:`Plan`: every table and column reference is bound to a
source, ``*`` is expanded, output names are computed and the statement is
classified as aggregate or row-wise.  It evaluates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import QueryError
from .nodes import BinOp, Column, Func, Literal, Select, Star, UnaryOp

SCALAR_ARITY: dict[str, tuple[int, int | None]] = {
    "UPPER": (1, 1),
    "LOWER": (1, 1),
    "LENGTH": (1, 1),
    "ABS": (1, 1),
    "COALESCE": (1, None),
}

AGGREGATE_ARITY: dict[str, tuple[int, int | None]] = {
    "COUNT": (1, 1),
    "SUM": (1, 1),
    "AVG": (1, 1),
    "MIN": (1, 1),
    "MAX": (1, 1),
}

__all__ = [
    "AGGREGATE_ARITY",
    "SCALAR_ARITY",
    "Plan",
    "PlannedItem",
    "PlannedOrderKey",
    "plan",
    "resolve_column",
]


@dataclass
class PlannedItem:
    """One computed output column."""

    name: str
    expr: object


@dataclass
class PlannedOrderKey:
    """One ORDER BY key, possibly pointing at a computed output column."""

    expr: object
    descending: bool = False
    output_index: int | None = None


@dataclass
class Plan:
    """A validated, name-resolved description of one SELECT statement."""

    sources: list[tuple[str, str]] = field(default_factory=list)
    join_conditions: list[object] = field(default_factory=list)
    where: object | None = None
    group_by: list[object] = field(default_factory=list)
    having: object | None = None
    select_items: list[PlannedItem] = field(default_factory=list)
    distinct: bool = False
    order_by: list[PlannedOrderKey] = field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    is_aggregate: bool = False
    aggregate_calls: list[object] = field(default_factory=list)
    column_bindings: dict[tuple[str | None, str], tuple[str, str]] = field(
        default_factory=dict
    )


def _written(node: Column) -> str:
    return f"{node.table}.{node.name}" if node.table is not None else node.name


class _Planner:
    def __init__(self, select: Select, tables: dict[str, list[dict]]) -> None:
        self.select = select
        self.tables = tables
        self.sources: list[tuple[str, str]] = []
        self.columns: dict[str, list[str]] = {}
        self.known: dict[str, set[str]] = {}
        self.open_schema: set[str] = set()
        self.bindings: dict[tuple[str | None, str], tuple[str, str]] = {}
        self.ambiguous: set[str] = set()
        self.is_aggregate = False

    # -- sources and bindings ------------------------------------------------

    @property
    def joins(self) -> tuple:
        return tuple(getattr(self.select, "joins", ()) or ())

    @staticmethod
    def alias_of(ref: object) -> str:
        namer = getattr(ref, "ref_name", None)
        if callable(namer):
            alias = namer()
            if alias:
                return str(alias)
        return str(getattr(ref, "alias", None) or ref.name)

    def build_sources(self) -> None:
        refs = [self.select.from_table]
        for join in self.joins:
            if str(getattr(join, "kind", "INNER")).upper() != "INNER":
                raise QueryError(f"unsupported join kind: {join.kind}")
            refs.append(join.table)
        for ref in refs:
            name = ref.name
            alias = self.alias_of(ref)
            if name not in self.tables:
                raise QueryError(f"unknown table: {name}")
            if alias in self.known:
                raise QueryError(f"duplicate table alias: {alias}")
            rows = self.tables[name]
            if not isinstance(rows, list):
                raise QueryError(f"table {name} is not a list of rows")
            ordered: list[str] = []
            known: set[str] = set()
            for position, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise QueryError(f"row {position} of table {name} is not a mapping")
                for column in row:
                    if not isinstance(column, str):
                        raise QueryError(
                            f"column name in table {name} is not a string"
                        )
                    if column not in known:
                        known.add(column)
                        if position == 0:
                            ordered.append(column)
            if not rows:
                self.open_schema.add(alias)
            self.sources.append((name, alias))
            self.columns[alias] = ordered
            self.known[alias] = known

    def build_bindings(self) -> None:
        owners: dict[str, list[str]] = {}
        for _, alias in self.sources:
            for column in self.known[alias]:
                self.bindings[(alias, column)] = (alias, column)
                owners.setdefault(column, []).append(alias)
        for column, holders in owners.items():
            if len(holders) == 1:
                self.bindings[(None, column)] = (holders[0], column)
            else:
                self.ambiguous.add(column)

    def bind_column(self, node: Column) -> tuple[str, str]:
        key = (node.table, node.name)
        bound = self.bindings.get(key)
        if bound is not None:
            return bound
        if node.table is not None:
            if node.table not in self.known:
                raise QueryError(f"unknown table or alias: {node.table}")
            if node.table in self.open_schema:
                self.bindings[key] = (node.table, node.name)
                return self.bindings[key]
            raise QueryError(f"unknown column: {node.table}.{node.name}")
        if node.name in self.ambiguous:
            raise QueryError(
                f"ambiguous column: {node.name}; qualify it with a table name"
            )
        blank = [alias for _, alias in self.sources if alias in self.open_schema]
        if len(blank) == 1:
            self.bindings[key] = (blank[0], node.name)
            return self.bindings[key]
        if len(blank) > 1:
            raise QueryError(
                f"ambiguous column: {node.name}; qualify it with a table name"
            )
        raise QueryError(f"unknown column: {node.name}")

    # -- validation ----------------------------------------------------------

    @staticmethod
    def check_arity(name: str, args: tuple, bounds: tuple[int, int | None]) -> None:
        low, high = bounds
        count = len(args)
        if count < low or (high is not None and count > high):
            expected = f"{low}" if high == low else f"at least {low}"
            raise QueryError(
                f"{name} takes {expected} argument(s), got {count}"
            )

    def validate(
        self,
        expr: object,
        clause: str,
        *,
        allow_aggregate: bool,
        in_aggregate: bool = False,
    ) -> None:
        if isinstance(expr, Column):
            self.bind_column(expr)
            return
        if isinstance(expr, Literal):
            return
        if isinstance(expr, Star):
            raise QueryError(f"* is not allowed in {clause}")
        if isinstance(expr, UnaryOp):
            self.validate(
                expr.operand,
                clause,
                allow_aggregate=allow_aggregate,
                in_aggregate=in_aggregate,
            )
            return
        if isinstance(expr, BinOp):
            self.validate(
                expr.left,
                clause,
                allow_aggregate=allow_aggregate,
                in_aggregate=in_aggregate,
            )
            right = expr.right
            operands = right if isinstance(right, tuple) else (right,)
            for operand in operands:
                self.validate(
                    operand,
                    clause,
                    allow_aggregate=allow_aggregate,
                    in_aggregate=in_aggregate,
                )
            return
        if isinstance(expr, Func):
            name = expr.name
            if name in AGGREGATE_ARITY:
                if not allow_aggregate:
                    raise QueryError(
                        f"aggregate function {name} is not allowed in {clause}"
                    )
                if in_aggregate:
                    raise QueryError(
                        f"aggregate function {name} may not be nested "
                        "inside another aggregate"
                    )
                self.check_arity(name, expr.args, AGGREGATE_ARITY[name])
                for arg in expr.args:
                    if isinstance(arg, Star):
                        if name != "COUNT":
                            raise QueryError(f"* is not a valid argument of {name}")
                        if arg.table is not None:
                            raise QueryError("COUNT(tabl
```