"""SQL text -> syntax tree.

No interface was published for `minidb.tokens` or `minidb.nodes`, so this module
scans its own tokens and defines its own node types rather than guessing at the
names those modules export. Only `minidb.errors.QueryError`, which the
specification itself names, is imported.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QueryError

__all__ = [
    "parse",
    "Select",
    "SelectItem",
    "TableRef",
    "JoinClause",
    "OrderItem",
    "Literal",
    "ColumnRef",
    "Star",
    "UnaryOp",
    "BinaryOp",
    "FuncCall",
    "IsNull",
    "InList",
    "Like",
    "AGGREGATE_FUNCTIONS",
    "SCALAR_FUNCTIONS",
]

AGGREGATE_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})
SCALAR_FUNCTIONS = frozenset({"UPPER", "LOWER", "LENGTH", "ABS", "COALESCE"})

KEYWORDS = frozenset(
    {
        "SELECT",
        "DISTINCT",
        "FROM",
        "INNER",
        "JOIN",
        "ON",
        "WHERE",
        "GROUP",
        "HAVING",
        "ORDER",
        "BY",
        "ASC",
        "DESC",
        "LIMIT",
        "OFFSET",
        "AS",
        "AND",
        "OR",
        "NOT",
        "IS",
        "IN",
        "LIKE",
    }
)

_WHITESPACE = " \t\n\r\f\v"
_TWO_CHAR_OPS = {"<=": "<=", ">=": ">=", "<>": "<>", "!=": "<>"}
_ONE_CHAR_OPS = frozenset({"=", "<", ">", "+", "-", "*", "/"})
_PUNCT = frozenset({"(", ")", ",", "."})
_COMPARISON_OPS = frozenset({"=", "<>", "<", "<=", ">", ">="})
_ADDITIVE_OPS = ("+", "-")
_MULTIPLICATIVE_OPS = ("*", "/")

# COUNT (one argument or '*') and COALESCE (one or more) are handled apart.
_FIXED_ARITY = {
    "UPPER": 1,
    "LOWER": 1,
    "LENGTH": 1,
    "ABS": 1,
    "SUM": 1,
    "AVG": 1,
    "MIN": 1,
    "MAX": 1,
}


# --- syntax tree ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Literal:
    """A literal value; `NULL` is Literal(None), strings are already unescaped."""

    value: int | float | str | None


@dataclass(frozen=True, slots=True)
class ColumnRef:
    """`col` is ColumnRef(None, 'col'); `t.col` is ColumnRef('t', 'col')."""

    table: str | None
    name: str


@dataclass(frozen=True, slots=True)
class Star:
    """`*` is Star(None); `t.*` is Star('t'). Only ever a SelectItem.expr."""

    table: str | None = None


@dataclass(frozen=True, slots=True)
class UnaryOp:
    """op is one of 'NOT', '-', '+'."""

    op: str
    operand: "Expr"


@dataclass(frozen=True, slots=True)
class BinaryOp:
    """op is one of '+', '-', '*', '/', '=', '<>', '<', '<=', '>', '>=', 'AND', 'OR'."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True, slots=True)
class FuncCall:
    """name is upper-cased; COUNT(*) is FuncCall('COUNT', (), True)."""

    name: str
    args: tuple["Expr", ...] = ()
    star: bool = False


@dataclass(frozen=True, slots=True)
class IsNull:
    """`x IS NULL` is negated=False, `x IS NOT NULL` is negated=True."""

    operand: "Expr"
    negated: bool = False


@dataclass(frozen=True, slots=True)
class InList:
    """`x IN (a, b, ...)`; items is never empty."""

    operand: "Expr"
    items: tuple["Expr", ...] = ()


@dataclass(frozen=True, slots=True)
class Like:
    """`x LIKE 'pat'`; pattern is normally Literal(str)."""

    operand: "Expr"
    pattern: "Expr"


Expr = (
    Literal
    | ColumnRef
    | Star
    | UnaryOp
    | BinaryOp
    | FuncCall
    | IsNull
    | InList
    | Like
)


@dataclass(frozen=True, slots=True)
class SelectItem:
    """One select_list entry.

    `source_text` is the entry's expression source with whitespace removed.
    `output_name` is the Output naming result, and is None only for a star item,
    which the engine must expand itself.
    """

    expr: Expr
    alias: str | None = None
    source_text: str = ""
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class TableRef:
    """A FROM/JOIN table with its optional alias."""

    name: str
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class JoinClause:
    """One INNER JOIN; condition is never None."""

    table: TableRef
    condition: Expr


@dataclass(frozen=True, slots=True)
class OrderItem:
    """One ORDER BY key; ASC or absent is ascending=True."""

    expr: Expr
    ascending: bool = True


@dataclass(frozen=True, slots=True)
class Select:
    """A whole parsed query: every clause slot is always present."""

    items: tuple[SelectItem, ...]
    source: TableRef
    distinct: bool = False
    joins: tuple[JoinClause, ...] = ()
    where: Expr | None = None
    group_by: tuple[Expr, ...] = ()
    having: Expr | None = None
    order_by: tuple[OrderItem, ...] = ()
    limit: int | None = None
    offset: int | None = None


# --- tokens --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # KEYWORD, IDENT, NUMBER, STRING, NULL, OP, PUNCT, EOF
    value: object
    text: str
    pos: int

    def is_keyword(self, *names: str) -> bool:
        if self.kind != "KEYWORD":
            return False
        if not names:
            return True
        return any(self.value == name.upper() for name in names)


def _scan_string(sql: str, start: int) -> tuple[str, int]:
    chunks: list[str] = []
    i = start + 1
    length = len(sql)
    while True:
        if i >= length:
            raise QueryError(
                f"unterminated string literal starting at position {start}"
            )
        char = sql[i]
        if char == "'":
            if i + 1 < length and sql[i + 1] == "'":
                chunks.append("'")
                i += 2
                continue
            return "".join(chunks), i + 1
        chunks.append(char)
        i += 1


def _scan_number(sql: str, start: int) -> tuple[int | float, int]:
    length = len(sql)
    i = start
    is_float = False
    while i < length and sql[i].isdigit():
        i += 1
    if i < length and sql[i] == ".":
        is_float = True
        i += 1
        if i >= length or not sql[i].isdigit():
            raise QueryError(f"malformed number at position {start}")
        while i < length and sql[i].isdigit():
            i += 1
    if i < length and sql[i] in "eE":
        is_float = True
        i += 1
        if i < length and sql[i] in "+-":
            i += 1
        if i >= length or not sql[i].isdigit():
            raise QueryError(f"malformed number at position {start}")
        while i < length and sql[i].isdigit():
            i += 1
    text = sql[start:i]
    return (float(text) if is_float else int(text)), i


def _tokenize(sql: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    length = len(sql)
    while i < length:
        char = sql[i]
        if char in _WHITESPACE:
            i += 1
            continue
        if char == "'":
            value, end = _scan_string(sql, i)
            tokens.append(_Token("STRING", value, sql[i:end], i))
            i = end
            continue
        if char.isdigit():
            number, end = _scan_number(sql, i)
            tokens.append(_Token("NUMBER", number, sql[i:end], i))
            i = end
            continue
        if char.isalpha() or char == "_":
            end = i
            while end < length and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            text = sql[i:end]
            upper = text.upper()
            if upper == "NULL":
                tokens.append(_Token("NULL", None, text, i))
            elif upper in KEYWORDS:
                tokens.append(_Token("KEYWORD", upper, text, i))
            else:
                tokens.append(_Token("IDENT", text, text, i))
            i = end
            continue
        pair = sql[i : i + 2]
        if pair in _TWO_CHAR_OPS:
            tokens.append(_Token("OP", _TWO_CHAR_OPS[pair], pair, i))
            i += 2
            continue
        if char in _ONE_CHAR_OPS:
            tokens.append(_Token("OP", char, char, i))
            i += 1
            continue
        if char in _PUNCT:
            tokens.append(_Token("PUNCT", char, char, i))
            i += 1
            continue
        raise QueryError(f"unexpected character {char!r} at position {i}")
    tokens.append(_Token("EOF", None, "", length))
    return tokens


# --- parser --------------------------------------------------------------


class _Parser:
    """Recursive-descent parser over one token list."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self, ahead: int = 0) -> _Token:
        index = self._pos + ahead
        if index >= len(self._tokens):
            return self._tokens[-1]
        return self._tokens[index]

    def _advance(self) -> _Token:
        token = self._peek()
        if token.kind != "EOF":
            self._pos += 1
        return token

    def _accept_keyword(self, *names: str) -> _Token | None:
        if self._peek().is_keyword(*names):
            return self._advance()
        return None

    def _expect_keyword(self, name: str, context: str) -> None:
        if self._accept_keyword(name) is None:
            raise QueryError(
                f"expected {name} {context}, found {self._describe(self._peek())}"
            )

    def _accept_punct(self, char: str) -> bool:
        token = self._peek()
        if token.kind == "PUNCT" and token.value == char:
            self._advance()
            return True
        return False

    def _expect_punct(self, char: str, context: str) -> None:
        if not self._accept_punct(char):
            raise QueryError(
                f"expected '{char}' {context}, found {self._describe(self._peek())}"
            )

    def _at_op(self, *values: str) -> bool:
        token = self._peek()
        return token.kind == "OP" and token.value in values

    def _expect_identifier(self, context: str) -> str:
        token = self._peek()
        if token.kind != "IDENT":
            raise QueryError(f"expected {context}, found {self._describe(token)}")
        self._advance()
        return str(token.value)

    @staticmethod
    def _describe(token: _Token) -> str:
        if token.kind == "EOF":
            return "end of input"
        return f"{token.text!r} at position {token.pos}"

    def _source_text(self, start: int, end: int) -> str:
        # Token texts carry no whitespace outside string literals, so joining
        # them gives the source with all whitespace removed.
        return "".join(token.text for token in self._tokens[start:end])

    # statement

    def parse_statement(self) -> Select:
        self._expect_keyword("SELECT", "at the start of the query")
        distinct = self._accept_keyword("DISTINCT") is not None
        items = self._parse_select_list()
        self._expect_keyword("FROM", "after the select list")
        source = self._parse_table_ref("a table name after FROM")
        joins = self._parse_joins()

        where: Expr | None = None
        if self._accept_keyword("WHERE") is not None:
            where = self._parse_expression()

        group_by: tuple[Expr, ...] = ()
        if self._accept_keyword("GROUP") is not None:
            self._expect_keyword("BY", "after GROUP")
            group_by = self._parse_expression_list()

        having: Expr | None = None
        if self._accept_keyword("HAVING") is not None:
            having = self._parse_expression()

        order_by: tuple[OrderItem, ...] = ()
        if self._accept_keyword("ORDER") is not None:
            self._expect_keyword("BY", "after ORDER")
            order_by = self._parse_order_list()

        limit: int | None = None
        if self._accept_keyword("LIMIT") is not None:
            limit = self._parse_count("LIMIT")

        offset: int | None = None
        if self._accept_keyword("OFFSET") is not None:
            offset = self._parse_count("OFFSET")

        trailing = self._peek()
        if trailing.kind != "EOF":
            raise QueryError(
                f"unexpected {self._describe(trailing)} after the end of the query"
            )

        return Select(
            items,
            source,
            distinct,
            joins,
            where,
            group_by,
            having,
            order_by,
            limit,
            offset,
        )

    def _parse_joins(self) -> tuple[JoinClause, ...]:
        joins: list[JoinClause] = []
        while True:
            if self._accept_keyword("INNER") is not None:
                self._expect_keyword("JOIN", "after INNER")
            elif self._accept_keyword("JOIN") is None:
                break
            table = self._parse_table_ref("a table name after JOIN")
            self._expect_keyword("ON", "after the joined table")
            joins.append(JoinClause(table, self._parse_expression()))
        return tuple(joins)

    def _parse_table_ref(self, context: str) -> TableRef:
        name = self._expect_identifier(context)
        alias: str | None = None
        if self._accept_keyword("AS") is not None:
            alias = self._expect_identifier("a table alias after AS")
        elif self._peek().kind == "IDENT":
            alias = str(self._advance().value)
        return TableRef(name, alias)

    # select list

    def _parse_select_list(self) -> tuple[SelectItem, ...]:
        items = [self._parse_select_item()]
        while self._accept_punct(","):
            items.append(self._parse_select_item())
        return tuple(items)

    def _parse_select_item(self) -> SelectItem:
        token = self._peek()
        if token.kind == "OP" and token.value == "*":
            self._advance()
            return SelectItem(Star(None), None, "*", None)
        after = self._peek(1)
        beyond = self._peek(2)
        if (
            token.kind == "IDENT"
            and after.kind == "PUNCT"
            and after.value == "."
            and beyond.kind == "OP"
            and beyond.value == "*"
        ):
            table = str(token.value)
            self._advance()
            self._advance()
            self._advance()
            return SelectItem(Star(table), None, f"{table}.*", None)

        start = self._pos
        expr = self._parse_expression()
        source_text = self._source_text(start, self._pos)

        alias: str | None = None
        if self._accept_keyword("AS") is not None:
            alias = self._expect_identifier("an alias after AS")
        elif self._peek().kind == "IDENT":
            alias = str(self._advance().value)

        if alias is not None:
            name = alias
        elif isinstance(expr, ColumnRef):
            name = expr.name
        else:
            name = source_text
        return SelectItem(expr, alias, source_text, name)

    def _parse_expression_list(self) -> tuple[Expr, ...]:
        exprs = [self._parse_expression()]
        while self._accept_punct(","):
            exprs.append(self._parse_expression())
        return tuple(exprs)

    def _parse_order_list(self) -> tuple[OrderItem, ...]:
        items = [self._parse_order_item()]
        while self._accept_punct(","):
            items.append(self._parse_order_item())
        return tuple(items)

    def _parse_order_item(self) -> OrderItem:
        expr = self._parse_expression()
        ascending = True
        if self._accept_keyword("DESC") is not None:
            ascending = False
        elif self._accept_keyword("ASC") is not None:
            ascending = True
        return OrderItem(expr, ascending)

    def _parse_count(self, clause: str) -> int:
        negative = False
        if self._at_op("-", "+"):
            negative = self._advance().value == "-"
        token = self._peek()
        if token.kind != "NUMBER":
            raise QueryError(
                f"expected an integer after {clause}, found {self._describe(token)}"
            )
        self._advance()
        value = token.value
        if not isinstance(value, int) or isinstance(value, bool):
            raise QueryError(f"{clause} requires an integer, not {token.text!r}")
        if negative:
            raise QueryError(f"{clause} must not be negative")
        return value

    # expressions

    def _parse_expression(self) -> Expr:
        return self._parse_or()

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while self._accept_keyword("OR") is not None:
            left = BinaryOp("OR", left, self._parse_and())
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_not()
        while self._accept_keyword("AND") is not None:
            left = BinaryOp("AND", left, self._parse_not())
        return left

    def _parse_not(self) -> Expr:
        if self._accept_keyword("NOT") is not None:
            return UnaryOp("NOT", self._parse_not())
        return self._parse_predicate()

    def _parse_predicate(self) -> Expr:
        left = self._parse_additive()
        while True:
            token = self._peek()
            if token.kind == "OP" and token.value in _COMPARISON_OPS:
                self._advance()
                left = BinaryOp(str(token.value), left, self._parse_additive())
            elif token.is_keyword("IS"):
                self._advance()
                negated = self._accept_keyword("NOT") is not None
                null_token = self._peek()
                if null_token.kind != "NULL":
                    raise QueryError(
                        f"expected NULL after IS, found {self._describe(null_token)}"
                    )
                self._advance()
                left = IsNull(left, negated)
            elif token.is_keyword("IN"):
                self._advance()
                self._expect_punct("(", "after IN")
                items = [self._parse_expression()]
                while self._accept_punct(","):
                    items.append(self._parse_expression())
                self._expect_punct(")", "to close the IN list")
                left = InList(left, tuple(items))
            elif token.is_keyword("LIKE"):
                self._advance()
                left = Like(left, self._parse_additive())
            else:
                return left

    def _parse_additive(self) -> Expr:
        left = self._parse_multiplicative()
        while self._at_op(*_ADDITIVE_OPS):
            op = str(self._advance().value)
            left = BinaryOp(op, left, self._parse_multiplicative())
        return left

    def _parse_multiplicative(self) -> Expr:
        left = self._parse_unary()
        while self._at_op(*_MULTIPLICATIVE_OPS):
            op = str(self._advance().value)
            left = BinaryOp(op, left, self._parse_unary())
        return left

    def _parse_unary(self) -> Expr:
        if self._at_op("-", "+"):
            op = str(self._advance().value)
            return UnaryOp(op, self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        token = self._peek()
        if token.kind == "NUMBER":
            self._advance()
            value = token.value
            if not isinstance(value, (int, float)):
                raise QueryError(f"malformed number {token.text!r}")
            return Literal(value)
        if token.kind == "STRING":
            self._advance()
            return Literal(str(token.value))
        if token.kind == "NULL":
            self._advance()
            return Literal(None)
        if token.kind == "PUNCT" and token.value == "(":
            self._advance()
            inner = self._parse_expression()
            self._expect_punct(")", "to close a parenthesised expression")
            return inner
        if token.kind == "IDENT":
            after = self._peek(1)
            if after.kind == "PUNCT" and after.value == "(":
                return self._parse_function_call()
            self._advance()
            name = str(token.value)
            if self._accept_punct("."):
                column = self._expect_identifier("a column name after '.'")
                return ColumnRef(name, column)
            return ColumnRef(None, name)
        raise QueryError(f"unexpected {self._describe(token)} in expression")

    def _parse_function_call(self) -> Expr:
        name_token = self._advance()
        written = str(name_token.value)
        name = written.upper()
        self._expect_punct("(", f"after the function name '{written}'")
        if name not in AGGREGATE_FUNCTIONS and name not in SCALAR_FUNCTIONS:
            raise QueryError(f"unknown function '{written}'")

        star = False
        args: list[Expr] = []
        if self._at_op("*"):
            if name != "COUNT":
                raise QueryError(f"'*' is not a valid argument for {name}")
            self._advance()
            star = True
        elif not (self._peek().kind == "PUNCT" and self._peek().value == ")"):
            args.append(self._parse_expression())
            while self._accept_punct(","):
                args.append(self._parse_expression())
        self._expect_punct(")", f"to close the call to {name}")
        self._check_arity(name, len(args), star)
        return FuncCall(name, tuple(args), star)

    @staticmethod
    def _check_arity(name: str, count: int, star: bool) -> None:
        if name == "COUNT":
            if not star and count != 1:
                raise QueryError("COUNT takes exactly one argument or '*'")
            return
        if name == "COALESCE":
            if count == 0:
                raise QueryError("COALESCE takes at least one argument")
            return
        expected = _FIXED_ARITY.get(name)
        if expected is None:
            raise QueryError(f"unknown function '{name}'")
        if count != expected:
            raise QueryError(
                f"{name} takes exactly {expected} argument(s), got {count}"
            )


def parse(sql: str) -> Select:
    """Parse `sql` into a `Select` tree, raising `QueryError` on any problem."""
    if not isinstance(sql, str):
        raise QueryError("query must be a string")
    try:
        tokens = _tokenize(sql)
        return _Parser(tokens).parse_statement()
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is nested too deeply to parse") from exc
    except Exception as exc:
        raise QueryError(f"malformed query: {exc}") from exc
