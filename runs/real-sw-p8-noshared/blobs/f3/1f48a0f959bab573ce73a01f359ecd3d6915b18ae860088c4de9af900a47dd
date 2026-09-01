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

SQL NULL is Python None throughout this module. Every failure raised here is a
QueryError: no KeyError, TypeError, ValueError, ZeroDivisionError or
AttributeError is allowed to escape.
'''

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

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
    '''Three-way comparison: -1 if a < b, 0 if equal, 1 if a > b.

    Returns None (unknown) when either operand is NULL. Raises QueryError for a
    mixed-type comparison, which is what makes ORDER BY over mixed types and a
    comparison of a number against a string query errors.
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
    '''Compile a LIKE pattern to an equivalent regex, caching the result.'''
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
    '''SQL LIKE: % matches any run of characters, _ matches exactly one.

    Every other pattern character matches itself. Returns None (unknown) when
    either operand is NULL, and raises QueryError when a present operand is not
    a string.
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


def _as_values(name: str, values: Iterable[Any]) -> list[Any]:
    '''Materialise the per-group values, reporting a bad shape as QueryError.'''
    if isinstance(values, list):
        return values
    try:
        return list(values)
    except TypeError as exc:
        raise QueryError(
            name + ' requires a list of values, got ' + _type_name(values)
        ) from exc


def _numeric_values(name: str, values: Iterable[Any]) -> list[int | float]:
    numbers: list[int | float] = []
    for value in _as_values(name, values):
        if value is None:
            continue
        if _value_class(value) != _NUMBER:
            raise QueryError(
                name + ' requires numeric values, got ' + _type_name(value)
            )
        numbers.append(value)
    return numbers


def _count(values: Iterable[Any]) -> int:
    '''Number of non-NULL values; 0 for an empty group.'''
    total = 0
    for value in _as_values('COUNT', values):
        if value is not None:
            total += 1
    return total


def _sum(values: Iterable[Any]) -> int | float | None:
    numbers = _numeric_values('SUM', values)
    if not numbers:
        return None
    total = numbers[0]
    for value in numbers[1:]:
        total = total + value
    return total


def _avg(values: Iterable[Any]) -> float | None:
    numbers = _numeric_values('AVG', values)
    if not numbers:
        return None
    total = 0.0
    for value in numbers:
        total += value
    return total / len(numbers)


def _extreme(name: str, values: Iterable[Any], wanted: int) -> Any:
    '''Fold the non-NULL values with compare, keeping the one whose comparison
    against the running best is `wanted`; None when every value is NULL.'''
    best: Any = None
    seen = False
    for value in _as_values(name, values):
        if value is None:
            continue
        if not seen:
            best = value
            seen = True
            continue
        if compare(value, best) == wanted:
            best = value
    if not seen:
        return None
    return best


def _min(values: Iterable[Any]) -> Any:
    return _extreme('MIN', values, -1)


def _max(values: Iterable[Any]) -> Any:
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