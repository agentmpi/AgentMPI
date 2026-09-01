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

from .errors import QueryError
from .nodes import (
    BinOp,
    Column,
    Expr,
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
from .tokens import Token, tokenize

_COMPARISON_OPS = frozenset({'=', '<>', '!=', '<', '<=', '>', '>='})


def parse(sql: str) -> Select:
    '''Parse one complete SELECT statement and return its AST.'''
    return _Parser(sql).statement()


class _Parser:
    '''Token cursor plus one method per grammar production.'''

    def __init__(self, sql: str) -> None:
        if not isinstance(sql, str):
            raise QueryError('query must be a string, not ' + type(sql).__name__)
        self._sql = sql
        self._tokens = tokenize(sql)
        self._index = 0

    def _peek(self, offset: int = 0) -> Token | None:
        index = self._index + offset
        if 0 <= index < len(self._tokens):
            return self._tokens[index]
        return None

    def _at_end(self) -> bool:
        return self._index >= len(self._tokens)

    def _source_pos(self) -> int:
        token = self._peek()
        if token is None:
            return len(self._sql)
        return token.pos

    def _advance(self, what: str) -> Token:
        token = self._peek()
        if token is None:
            raise QueryError('unexpected end of query, expected ' + what)
        self._index += 1
        return token

    def _unexpected(self, what: str) -> QueryError:
        token = self._peek()
        if token is None:
            return QueryError('unexpected end of query, expected ' + what)
        return QueryError(
            'expected ' + what + ' but found ' + repr(token.value)
            + ' at position ' + str(token.pos)
        )

    def _token_is(self, offset: int, kind: str, value: str) -> bool:
        token = self._peek(offset)
        return token is not None and token.kind == kind and token.value == value

    def _is_keyword(self, *names: str) -> bool:
        token = self._peek()
        return token is not None and token.kind == 'KEYWORD' and token.value in names

    def _match_keyword(self, name: str) -> bool:
        if self._is_keyword(name):
            self._index += 1
            return True
        return False

    def _expect_keyword(self, name: str) -> None:
        if not self._is_keyword(name):
            raise self._unexpected(name)
        self._index += 1

    def _is_punct(self, value: str) -> bool:
        return self._token_is(0, 'PUNCT', value)

    def _match_punct(self, value: str) -> bool:
        if self._is_punct(value):
            self._index += 1
            return True
        return False

    def _expect_punct(self, value: str) -> None:
        if not self._is_punct(value):
            raise self._unexpected(repr(value))
        self._index += 1

    def _is_op(self, *values: str) -> bool:
        token = self._peek()
        return token is not None and token.kind == 'OP' and token.value in values

    def _is_ident(self, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token is not None and token.kind == 'IDENT'

    def _expect_ident(self, what: str) -> str:
        if not self._is_ident():
            raise self._unexpected(what)
        return str(self._advance(what).value)

    def statement(self) -> Select:
        if not self._tokens:
            raise QueryError('empty query')
        self._expect_keyword('SELECT')
        distinct = self._match_keyword('DISTINCT')
        items = self._select_list()
        self._expect_keyword('FROM')
        from_table = self._table_ref('table name after FROM')
        joins: list[Join] = []
        while self._is_keyword('INNER', 'JOIN'):
            joins.append(self._join())
        where = None
        if self._match_keyword('WHERE'):
            where = self._expression()
        group_by: tuple[Expr, ...] = ()
        if self._match_keyword('GROUP'):
            self._expect_keyword('BY')
            group_by = tuple(self._expression_list())
        having = None
        if self._match_keyword('HAVING'):
            having = self._expression()
        order_by: tuple[OrderKey, ...] = ()
        if self._match_keyword('ORDER'):
            self._expect_keyword('BY')
            order_by = tuple(self._order_keys())
        limit = None
        offset = None
        while self._is_keyword('LIMIT', 'OFFSET'):
            token = self._advance('LIMIT or OFFSET')
            if token.value == 'LIMIT':
                if limit is not None:
                    raise QueryError('duplicate LIMIT clause at position ' + str(token.pos))
                limit = self._row_count('LIMIT')
            else:
                if offset is not None:
                    raise QueryError('duplicate OFFSET clause at position ' + str(token.pos))
                offset = self._row_count('OFFSET')
        if not self._at_end():
            token = self._advance('end of query')
            raise QueryError(
                'unexpected trailing input ' + repr(token.value)
                + ' at position ' + str(token.pos)
            )
        return Select(
            items=tuple(items),
            from_table=from_table,
            distinct=distinct,
            joins=tuple(joins),
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def _row_count(self, clause: str) -> int:
        if self._is_op('-'):
            token = self._advance(clause + ' count')
            raise QueryError(clause + ' must not be negative (position ' + str(token.pos) + ')')
        token = self._advance(clause + ' count')
        value = token.value
        if token.kind != 'NUMBER' or not isinstance(value, int) or isinstance(value, bool):
            raise QueryError(
                clause + ' requires a non-negative integer, found ' + repr(value)
                + ' at position ' + str(token.pos)
            )
        if value < 0:
            raise QueryError(clause + ' must not be negative (position ' + str(token.pos) + ')')
        return int(value)

    def _select_list(self) -> list[SelectItem]:
        items = [self._select_item()]
        while self._match_punct(','):
            items.append(self._select_item())
        return items

    def _select_item(self) -> SelectItem:
        if self._is_op('*'):
            self._index += 1
            return SelectItem(expr=Star(), alias=None, source_text='*')
        if (
            self._is_ident()
            and self._token_is(1, 'PUNCT', '.')
            and self._token_is(2, 'OP', '*')
        ):
            table = str(self._advance('table name').value)
            self._index += 2
            return SelectItem(expr=Star(table=table), alias=None, source_text=table + '.*')
        start = self._source_pos()
        expr = self._expression()
        source_text = self._sql[start:self._source_pos()]
        alias = self._optional_alias()
        return SelectItem(expr=expr, alias=alias, source_text=source_text)

    def _optional_alias(self) -> str | None:
        if self._match_keyword('AS'):
            return self._expect_ident('alias name after AS')
        if self._is_ident():
            return str(self._advance('alias name').value)
        return None

    def _table_ref(self, what: str) -> TableRef:
        name = self._expect_ident(what)
        return TableRef(name=name, alias=self._optional_alias())

    def _join(self) -> Join:
        self._match_keyword('INNER')
        self._expect_keyword('JOIN')
        table = self._table_ref('table name after JOIN')
        self._expect_keyword('ON')
        condition = self._expression()
        return Join(table=table, condition=condition, kind='INNER')

    def _order_keys(self) -> list[OrderKey]:
        keys = [self._order_key()]
        while self._match_punct(','):
            keys.append(self._order_key())
        return keys

    def _order_key(self) -> OrderKey:
        expr = self._expression()
        descending = False
        if self._match_keyword('DESC'):
            descending = True
        else:
            self._match_keyword('ASC')
        return OrderKey(expr=expr, descending=descending)

    def _expression_list(self) -> list[Expr]:
        exprs = [self._expression()]
        while self._match_punct(','):
            exprs.append(self._expression())
        return exprs

    def _expression(self) -> Expr:
        return self._or_expression()

    def _or_expression(self) -> Expr:
        left = self._and_expression()
        while self._match_keyword('OR'):
            left = BinOp(op='OR', left=left, right=self._and_expression())
        return left

    def _and_expression(self) -> Expr:
        left = self._not_expression()
        while self._match_keyword('AND'):
            left = BinOp(op='AND', left=left, right=se
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

    def build_sources(self) -> None:
        refs = [self.select.from_table]
        for join in self.select.joins:
            if str(join.kind).upper() != "INNER":
                raise QueryError(f"unsupported join kind: {join.kind}")
            refs.append(join.table)
        for ref in refs:
            name = ref.name
            alias = ref.ref_name()
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
                            raise QueryError("COUNT(table.*) is not supported")
                        continue
                    self.validate(
                        arg, clause, allow_aggregate=allow_aggregate, in_aggregate=True
                    )
                return
            if name in SCALAR_ARITY:
                self.check_arity(name, expr.args, SCALAR_ARITY[name])
                for arg in expr.args:
                    self.validate(
                        arg,
                        clause,
                        allow_aggregate=allow_aggregate,
                        
```