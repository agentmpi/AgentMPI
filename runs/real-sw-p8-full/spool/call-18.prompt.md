You own `minidb/planner.py`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}

### minidb/functions.py (published exports: ["SCALARS", "AGGREGATES", "like_match", "compare"])
```python
'''Scalar functions, aggregate functions, LIKE matching and value comparison.

SQL NULL is Python None throughout. Every failure raised from this module is a
QueryError; no other exception type is allowed to escape.
'''

from __future__ import annotations

import re
from typing import Any, Callable

from .errors import QueryError

_NUMBER = 'number'
_STRING = 'string'
_BOOLEAN = 'boolean'


def _value_class(value: Any) -> str | None:
    '''Return the comparison class of a value, or None if it has none.'''
    if isinstance(value, bool):
        return _BOOLEAN
    if isinstance(value, (int, float)):
        return _NUMBER
    if isinstance(value, str):
        return _STRING
    return None


def _type_name(value: Any) -> str:
    return type(value).__name__


def compare(a: Any, b: Any) -> int | None:
    '''Three-way comparison: -1, 0 or 1, or None when either side is NULL.

    Raises QueryError when the two values are not of the same comparison class,
    which is what makes a mixed-type comparison a query error.
    '''
    if a is None or b is None:
        return None
    class_a = _value_class(a)
    class_b = _value_class(b)
    if class_a is None or class_b is None or class_a != class_b:
        raise QueryError(
            'cannot compare ' + _type_name(a) + ' with ' + _type_name(b)
        )
    try:
        if a < b:
            return -1
        if b < a:
            return 1
    except TypeError as exc:
        raise QueryError(
            'cannot compare ' + _type_name(a) + ' with ' + _type_name(b)
        ) from exc
    return 0


_LIKE_CACHE: dict[str, re.Pattern[str]] = {}


def _like_pattern(pattern: str) -> re.Pattern[str]:
    '''Compile a SQL LIKE pattern, caching the result.'''
    compiled = _LIKE_CACHE.get(pattern)
    if compiled is not None:
        return compiled
    parts: list[str] = []
    for char in pattern:
        if char == '%':
            parts.append('.*')
        elif char == '_':
            parts.append('.')
        else:
            parts.append(re.escape(char))
    try:
        compiled = re.compile(''.join(parts), re.DOTALL)
    except re.error as exc:
        raise QueryError('invalid LIKE pattern: ' + pattern) from exc
    _LIKE_CACHE[pattern] = compiled
    return compiled


def like_match(value: Any, pattern: Any) -> bool | None:
    '''SQL LIKE: %% matches any run of characters, _ matches exactly one.

    Returns None (unknown) when either operand is NULL. Raises QueryError when
    either operand is present but is not a string.
    '''
    if value is None or pattern is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'LIKE requires a string value, got ' + _type_name(value)
        )
    if not isinstance(pattern, str):
        raise QueryError(
            'LIKE requires a string pattern, got ' + _type_name(pattern)
        )
    return _like_pattern(pattern).fullmatch(value) is not None


def _single_argument(name: str, args: tuple[Any, ...]) -> Any:
    if len(args) != 1:
        raise QueryError(
            name + ' takes exactly one argument, got ' + str(len(args))
        )
    return args[0]


def _upper(*args: Any) -> str | None:
    value = _single_argument('UPPER', args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'UPPER requires a string argument, got ' + _type_name(value)
        )
    return value.upper()


def _lower(*args: Any) -> str | None:
    value = _single_argument('LOWER', args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'LOWER requires a string argument, got ' + _type_name(value)
        )
    return value.lower()


def _length(*args: Any) -> int | None:
    value = _single_argument('LENGTH', args)
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(
            'LENGTH requires a string argument, got ' + _type_name(value)
        )
    return len(value)


def _abs(*args: Any) -> int | float | None:
    value = _single_argument('ABS', args)
    if value is None:
        return None
    if _value_class(value) != _NUMBER:
        raise QueryError(
            'ABS requires a numeric argument, got ' + _type_name(value)
        )
    return abs(value)


def _coalesce(*args: Any) -> Any:
    if not args:
        raise QueryError('COALESCE requires at least one argument')
    for value in args:
        if value is not None:
            return value
    return None


def _numeric_values(name: str, values: list[Any]) -> list[int | float]:
    numbers: list[int | float] = []
    for value in values:
        if value is None:
            continue
        if _value_class(value) != _NUMBER:
            raise QueryError(
                name + ' requires numeric values, got ' + _type_name(value)
            )
        numbers.append(value)
    return numbers


def _count(values: list[Any]) -> int:
    total = 0
    for value in values:
        if value is not None:
            total += 1
    return total


def _sum(values: list[Any]) -> int | float | None:
    numbers = _numeric_values('SUM', values)
    if not numbers:
        return None
    total = numbers[0]
    for value in numbers[1:]:
        total = total + value
    return total


def _avg(values: list[Any]) -> float | None:
    numbers = _numeric_values('AVG', values)
    if not numbers:
        return None
    total = 0.0
    for value in numbers:
        total += value
    return total / len(numbers)


def _extreme(name: str, values: list[Any], wanted: int) -> Any:
    '''Return the non-NULL value whose comparison against the running best is
    `wanted`, or None when there is no non-NULL value.'''
    best: Any = None
    seen = False
    for value in values:
        if value is None:
            continue
        if not seen:
            best = value
            seen = True
            continue
        order = compare(value, best)
        if order == wanted:
            best = value
    if not seen:
        return None
    return best


def _min(values: list[Any]) -> Any:
    return _extreme('MIN', values, -1)


def _max(values: list[Any]) -> Any:
    return _extreme('MAX', values, 1)


SCALARS: dict[str, Callable[..., Any]] = {
    'UPPER': _upper,
    'LOWER': _lower,
    'LENGTH': _length,
    'ABS': _abs,
    'COALESCE': _coalesce,
}

AGGREGATES: dict[str, Callable[[list[Any]], Any]] = {
    'COUNT': _count,
    'SUM': _sum,
    'AVG': _avg,
    'MIN': _min,
    'MAX': _max,
}

__all__ = ['SCALARS', 'AGGREGATES', 'like_match', 'compare']

```

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