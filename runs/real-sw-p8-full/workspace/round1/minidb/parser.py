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
            left = BinOp(op='AND', left=left, right=self._not_expression())
        return left

    def _not_expression(self) -> Expr:
        if self._match_keyword('NOT'):
            return UnaryOp(op='NOT', operand=self._not_expression())
        return self._predicate()

    def _predicate(self) -> Expr:
        left = self._additive()
        while True:
            token = self._peek()
            if token is None:
                return left
            if token.kind == 'OP' and token.value in _COMPARISON_OPS:
                self._index += 1
                left = BinOp(op=str(token.value), left=left, right=self._additive())
                continue
            if token.kind == 'KEYWORD' and token.value == 'IS':
                self._index += 1
                if self._match_keyword('NOT'):
                    self._expect_keyword('NULL')
                    left = UnaryOp(op='IS NOT NULL', operand=left)
                else:
                    self._expect_keyword('NULL')
                    left = UnaryOp(op='IS NULL', operand=left)
                continue
            if token.kind == 'KEYWORD' and token.value == 'IN':
                self._index += 1
                self._expect_punct('(')
                members = self._expression_list()
                self._expect_punct(')')
                left = BinOp(op='IN', left=left, right=tuple(members))
                continue
            if token.kind == 'KEYWORD' and token.value == 'LIKE':
                self._index += 1
                left = BinOp(op='LIKE', left=left, right=self._additive())
                continue
            return left

    def _additive(self) -> Expr:
        left = self._multiplicative()
        while self._is_op('+', '-'):
            token = self._advance('operand')
            left = BinOp(op=str(token.value), left=left, right=self._multiplicative())
        return left

    def _multiplicative(self) -> Expr:
        left = self._unary()
        while self._is_op('*', '/'):
            token = self._advance('operand')
            left = BinOp(op=str(token.value), left=left, right=self._unary())
        return left

    def _unary(self) -> Expr:
        if self._is_op('-', '+'):
            token = self._advance('operand')
            return UnaryOp(op=str(token.value), operand=self._unary())
        return self._primary()

    def _primary(self) -> Expr:
        token = self._peek()
        if token is None:
            raise QueryError('unexpected end of query, expected an expression')
        if token.kind == 'PUNCT' and token.value == '(':
            self._index += 1
            inner = self._expression()
            self._expect_punct(')')
            return inner
        if token.kind == 'NUMBER' or token.kind == 'STRING':
            self._index += 1
            return Literal(value=token.value)
        if token.kind == 'KEYWORD' and token.value == 'NULL':
            self._index += 1
            return Literal(value=None)
        if token.kind == 'IDENT':
            self._index += 1
            name = str(token.value)
            if self._is_punct('('):
                return self._function_call(name)
            if self._match_punct('.'):
                column = self._expect_ident('column name after ' + name + '.')
                return Column(name=column, table=name)
            return Column(name=name)
        raise QueryError(
            'unexpected ' + repr(token.value) + ' at position ' + str(token.pos)
            + ' where an expression was expected'
        )

    def _function_call(self, name: str) -> Func:
        self._expect_punct('(')
        args: list[Expr | Star] = []
        if self._is_op('*'):
            self._index += 1
            args.append(Star())
        elif not self._is_punct(')'):
            args.extend(self._expression_list())
        self._expect_punct(')')
        return Func(name=name.upper(), args=tuple(args))
