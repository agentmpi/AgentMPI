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
            'items', 'select', 'select_list', 'selectlist', 'select_items',
            'selectitems', 'columns', 'projection', 'projections', 'targets',
        }), items),
        (frozenset({
            'from_table', 'from', 'fromtable', 'table', 'source', 'from_ref',
            'fromref', 'from_clause', 'base', 'base_table',
        }), from_table),
        (frozenset({'distinct', 'is_distinct', 'isdistinct', 'distinct_flag'}), distinct),
        (frozenset({'joins', 'join', 'join_list', 'joinlist', 'join_clauses'}), joins),
        (frozenset({
            'where', 'where_clause', 'whereclause', 'predicate', 'filter', 'condition',
        }), where),
        (frozenset({
            'group_by', 'groupby', 'group', 'group_by_exprs', 'groups', 'group_exprs',
        }), group_by),
        (frozenset({'having', 'having_clause', 'havingclause', 'having_condition'}), having),
        (frozenset({
            'order_by', 'orderby', 'order', 'order_keys', 'orderkeys', 'sort', 'sort_keys',
        }), order_by),
        (frozenset({'limit', 'limit_count', 'limitcount', 'row_limit'}), limit),
        (frozenset({'offset', 'offset_count', 'offsetcount', 'skip'}), offset),
    ))


def _classify(token: Any) -> _Tok | None:
    '''Reduce one lexer token to a category, a value and a source position.'''
    kind = str(getattr(token, 'kind', '') or '').upper()
    value = getattr(token, 'value', None)
    raw_pos = getattr(token, 'pos', -1)
    pos = raw_pos if isinstance(raw_pos, int) and not isinstance(raw_pos, bool) else -1
    if kind in _SENTINEL_KINDS:
        return None
    if kind in _NULL_KINDS:
        return _Tok('NULL', None, pos)
    if kind in _NUMBER_KINDS:
        return _Tok('NUMBER', _to_number(value, pos), pos)
    if kind in _STRING_KINDS:
        return _Tok('STRING', '' if value is None else str(value), pos)
    if kind in _KEYWORD_KINDS:
        return _Tok('KEYWORD', str(value).upper(), pos)
    if kind in _IDENT_KINDS:
        text = str(value)
        if text.upper() in _KEYWORDS:
            return _Tok('KEYWORD', text.upper(), pos)
        return _Tok('IDENT', text, pos)
    if kind in _SYMBOL_KINDS:
        return _Tok('SYMBOL', str(value), pos)
    if isinstance(value, bool):
        return _Tok('OTHER', value, pos)
    if isinstance(value, (int, float)):
        return _Tok('NUMBER', value, pos)
    if value is None:
        return _Tok('NULL', None, pos)
    text = str(value)
    if text in _SYMBOLS:
        return _Tok('SYMBOL', text, pos)
    if text.upper() in _KEYWORDS:
        return _Tok('KEYWORD', text.upper(), pos)
    if text and (text[0].isalpha() or text[0] == '_'):
        return _Tok('IDENT', text, pos)
    return _Tok('OTHER', text, pos)


def _to_number(value: Any, pos: int) -> int | float:
    if isinstance(value, bool):
        raise QueryError('invalid numeric literal at position ' + str(pos))
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError as exc:
            raise QueryError(
                'invalid numeric literal ' + repr(text) + ' at position ' + str(pos)
            ) from exc


def parse(sql: str) -> Any:
    '''Parse one complete SELECT statement and return the Select AST node.'''
    return _Parser(sql).statement()


class _Parser:
    '''Token cursor plus one method per grammar production.'''

    def __init__(self, sql: str) -> None:
        if not isinstance(sql, str):
            raise QueryError('query must be a string, not ' + type(sql).__name__)
        self._sql = sql
        try:
            raw_tokens = list(tokenize(sql))
        except QueryError:
            raise
        except Exception as exc:
            raise QueryError('cannot tokenize query: ' + str(exc)) from exc
        tokens: list[_Tok] = []
        for raw in raw_tokens:
            token = _classify(raw)
            if token is not None:
                tokens.append(token)
        self._tokens = tokens
        self._index = 0

    def _peek(self, offset: int = 0) -> _Tok | None:
        index = self._index + offset
        if 0 <= index < len(self._tokens):
            return self._tokens[index]
        return None

    def _at_end(self) -> bool:
        return self._index >= len(self._tokens)

    def _advance(self, what: str) -> _Tok:
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

    def _is_keyword(self, *names: str) -> bool:
        token = self._peek()
        return token is not None and token.cat == 'KEYWORD' and token.value in names

    def _match_keyword(self, name: str) -> bool:
        if self._is_keyword(name):
            self._index += 1
            return True
        return False

    def _expect_keyword(self, name: str) -> None:
        if not self._is_keyword(name):
            raise self._unexpected(name)
        self._index += 1

    def _is_symbol(self, *values: str) -> bool:
        token = self._peek()
        return token is not None and token.cat == 'SYMBOL' and token.value in values

    def _symbol_at(self, offset: int, value: str) -> bool:
        token = self._peek(offset)
        return token is not None and token.cat == 'SYMBOL' and token.value == value

    def _match_symbol(self, value: str) -> bool:
        if self._is_symbol(value):
            self._index += 1
            return True
        return False

    def _expect_symbol(self, value: str) -> None:
        if not self._is_symbol(value):
            raise self._unexpected(repr(value))
        self._index += 1

    def _is_ident(self, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token is not None and token.cat == 'IDENT'

    def _expect_ident(self, what: str) -> str:
        if not self._is_ident():
            raise self._unexpected(what)
        return str(self._advance(what).value)

    def _source_slice(self, start_index: int, end_index: int) -> str:
        if start_index >= end_index or start_index >= len(self._tokens):
            return ''
        start = self._tokens[start_index].pos
        if start >= 0:
            if end_index < len(self._tokens) and self._tokens[end_index].pos >= start:
                text = self._sql[start:self._tokens[end_index].pos]
            else:
                text = self._sql[start:]
            text = text.strip()
            if text:
                return text
        parts: list[str] = []
        for token in self._tokens[start_index:end_index]:
            if token.cat == 'STRING':
                parts.append("'" + str(token.value).replace("'", "''") + "'")
            elif token.cat == 'NULL':
                parts.append('NULL')
            else:
                parts.append(str(token.value))
        return ''.join(parts)

    def statement(self) -> Any:
        if not self._tokens:
            raise QueryError('empty query')
        self._expect_keyword('SELECT')
        distinct = self._match_keyword('DISTINCT')
        items = self._select_list()
        self._expect_keyword('FROM')
        from_table = self._table_ref('table name after FROM')
        joins: list[Any] = []
        while self._is_keyword('INNER', 'JOIN'):
            joins.append(self._join())
        where = None
        if self._match_keyword('WHERE'):
            where = self._expression()
        group_by: list[Any] = []
        if self._match_keyword('GROUP'):
            self._expect_keyword('BY')
            group_by = self._expression_list()
        having = None
        if self._match_keyword('HAVING'):
            having = self._expression()
        order_by: list[Any] = []
        if self._match_keyword('ORDER'):
            self._expect_keyword('BY')
            order_by = self._order_keys()
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
        return _make_select(
            tuple(items),
            from_table,
            distinct,
            tuple(joins),
            where,
            tuple(group_by),
            having,
            tuple(order_by),
            limit,
            offset,
        )

    def _row_count(self, clause: str) -> int:
        negative = False
        sign_pos = -1
        if self._is_symbol('-'):
            negative = True
            sign_pos = self._advance(clause + ' count').pos
        elif self._is_symbol('+'):
            self._index += 1
        token = self._advance(clause + ' count')
        value = token.value
        if token.cat != 'NUMBER' or not isinstance(value, int) or isinstance(value, bool):
            raise QueryError(
                clause + ' requires a non-negative integer, found ' + repr(value)
                + ' at position ' + str(token.pos)
            )
        if negative and value != 0:
            raise QueryError(
                clause + ' must not be negative (position ' + str(sign_pos) + ')'
            )
        return int(value)

    def _select_list(self) -> list[Any]:
        items = [self._select_item()]
        while self._match_symbol(','):
            items.append(self._select_item())
        return items

    def _select_item(self) -> Any:
        if self._is_symbol('*'):
            self._index += 1
            return _make_select_item(_make_star(None), None, '*')
        if (
            self._is_ident()
            and self._symbol_at(1, '.')
            and self._symbol_at(2, '*')
        ):
            table = str(self._advance('table name').value)
            self._index += 2
            return _make_select_item(_make_star(table), None, table + '.*')
        start_index = self._index
        expr = self._expression()
        source_text = self._source_slice(start_index, self._index)
        alias = self._optional_alias()
        return _make_select_item(expr, alias, source_text)

    def _optional_alias(self) -> str | None:
        if self._match_keyword('AS'):
            return self._expect_ident('alias name after AS')
        if self._is_ident():
            return str(self._advance('alias name').value)
        return None

    def _table_ref(self, what: str) -> Any:
        name = self._expect_ident(what)
        return _make_table_ref(name, self._optional_alias())

    def _join(self) -> Any:
        self._match_keyword('INNER')
        self._expect_keyword('JOIN')
        table = self._table_ref('table name after JOIN')
        self._expect_keyword('ON')
        return _make_join(table, self._expression())

    def _order_keys(self) -> list[Any]:
        keys = [self._order_key()]
        while self._match_symbol(','):
            keys.append(self._order_key())
        return keys

    def _order_key(self) -> Any:
        expr = self._expression()
        descending = False
        if self._match_keyword('DESC'):
            descending = True
        else:
            self._match_keyword('ASC')
        return _make_order_key(expr, descending)

    def _expression_list(self) -> list[Any]:
        exprs = [self._expression()]
        while self._match_symbol(','):
            exprs.append(self._expression())
        return exprs

    def _expression(self) -> Any:
        return self._or_expression()

    def _or_expression(self) -> Any:
        left = self._and_expression()
        while self._match_keyword('OR'):
            left = _make_binop('OR', left, self._and_expression())
        return left

    def _and_expression(self) -> Any:
        left = self._not_expression()
        while self._match_keyword('AND'):
            left = _make_binop('AND', left, self._not_expression())
        return left

    def _not_expression(self) -> Any:
        if self._match_keyword('NOT'):
            return _make_unaryop('NOT', self._not_expression())
        return self._predicate()

    def _predicate(self) -> Any:
        left = self._additive()
        while True:
            token = self._peek()
            if token is None:
                return left
            if token.cat == 'SYMBOL' and token.value in _COMPARISON_OPS:
                self._index += 1
                left = _make_binop(str(token.value), left, self._additive())
                continue
            if token.cat == 'KEYWORD' and token.value == 'IS':
                self._index += 1
                if self._match_keyword('NOT'):
                    self._expect_keyword('NULL')
                    left = _make_unaryop('IS NOT NULL', left)
                else:
                    self._expect_keyword('NULL')
                    left = _make_unaryop('IS NULL', left)
                continue
            if token.cat == 'KEYWORD' and token.value == 'IN':
                self._index += 1
                self._expect_symbol('(')
                members = self._expression_list()
                self._expect_symbol(')')
                left = _make_binop('IN', left, tuple(members))
                continue
            if token.cat == 'KEYWORD' and token.value == 'LIKE':
                self._index += 1
                left = _make_binop('LIKE', left, self._additive())
                continue
            return left

    def _additive(self) -> Any:
        left = self._multiplicative()
        while self._is_symbol('+', '-'):
            token = self._advance('operand')
            left = _make_binop(str(token.value), left, self._multiplicative())
        return left

    def _multiplicative(self) -> Any:
        left = self._unary()
        while self._is_symbol('*', '/'):
            token = self._advance('operand')
            left = _make_binop(str(token.value), left, self._unary())
        return left

    def _unary(self) -> Any:
        if self._is_symbol('-', '+'):
            token = self._advance('operand')
            return _make_unaryop(str(token.value), self._unary())
        return self._primary()

    def _primary(self) -> Any:
        token = self._peek()
        if token is None:
            raise QueryError('unexpected end of query, expected an expression')
        if token.cat == 'SYMBOL' and token.value == '(':
            self._index += 1
            inner = self._expression()
            self._expect_symbol(')')
            return inner
        if token.cat == 'NUMBER' or token.cat == 'STRING':
            self._index += 1
            return _make_literal(token.value)
        if token.cat == 'NULL' or (token.cat == 'KEYWORD' and token.value == 'NULL'):
            self._index += 1
            return _make_literal(None)
        if token.cat == 'IDENT':
            self._index += 1
            name = str(token.value)
            if self._is_symbol('('):
                return self._function_call(name)
            if self._match_symbol('.'):
                column = self._expect_ident('column name after ' + name + '.')
                return _make_column(column, name)
            return _make_column(name, None)
        raise QueryError(
            'unexpected ' + repr(token.value) + ' at position ' + str(token.pos)
            + ' where an expression was expected'
        )

    def _function_call(self, name: str) -> Any:
        self._expect_symbol('(')
        args: list[Any] = []
        if self._is_symbol('*'):
            self._index += 1
            args.append(_make_star(None))
        elif not self._is_symbol(')'):
            args.extend(self._expression_list())
        self._expect_symbol(')')
        return _make_func(name.upper(), tuple(args))
