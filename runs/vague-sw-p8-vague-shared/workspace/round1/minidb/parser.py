"""Recursive-descent parser: a token stream becomes a `SelectStmt` tree."""

from __future__ import annotations

from .errors import QueryError
from .nodes import (
    AGGREGATE_FUNCTIONS,
    SCALAR_FUNCTIONS,
    BinaryOp,
    ColumnRef,
    Expr,
    FuncCall,
    InList,
    IsNull,
    JoinClause,
    Like,
    Literal,
    OrderItem,
    SelectItem,
    SelectStmt,
    Star,
    TableRef,
    UnaryOp,
)
from .tokens import Token, tokenize

__all__ = ["parse"]

_COMPARISON_OPS = frozenset({"=", "<>", "<", "<=", ">", ">="})
_ADDITIVE_OPS = frozenset({"+", "-"})
_MULTIPLICATIVE_OPS = frozenset({"*", "/"})
_SIGN_OPS = frozenset({"+", "-"})

# Every known function except COUNT (0 or 1 args plus the '*' form) and
# COALESCE (variadic) takes exactly one argument.
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


class _Parser:
    """Single-use parser over one token list."""

    def __init__(self, token_list: list[Token]) -> None:
        self._tokens = token_list
        self._pos = 0

    # --- token access -----------------------------------------------------

    def _peek(self, ahead: int = 0) -> Token:
        index = self._pos + ahead
        if index >= len(self._tokens):
            return self._tokens[-1]
        return self._tokens[index]

    def _advance(self) -> Token:
        token = self._peek()
        if token.kind != "EOF":
            self._pos += 1
        return token

    def _accept_keyword(self, *names: str) -> Token | None:
        token = self._peek()
        if token.is_keyword(*names):
            return self._advance()
        return None

    def _expect_keyword(self, name: str, context: str) -> Token:
        token = self._accept_keyword(name)
        if token is None:
            raise QueryError(
                f"expected {name} {context}, found {self._describe(self._peek())}"
            )
        return token

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
            raise QueryError(
                f"expected {context}, found {self._describe(token)}"
            )
        self._advance()
        return str(token.value)

    @staticmethod
    def _describe(token: Token) -> str:
        if token.kind == "EOF":
            return "end of input"
        return f"{token.text!r} at position {token.pos}"

    def _source_text(self, start: int, end: int) -> str:
        # Token texts never contain whitespace outside string literals, so
        # concatenating them yields the source with whitespace removed.
        return "".join(token.text for token in self._tokens[start:end])

    # --- statement --------------------------------------------------------

    def parse_statement(self) -> SelectStmt:
        self._expect_keyword("SELECT", "at the start of the query")
        distinct = self._accept_keyword("DISTINCT") is not None
        items = self._parse_select_list()
        self._expect_keyword("FROM", "after the select list")
        source = self._parse_table_ref("table name after FROM")
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

        return SelectStmt(
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
            table = self._parse_table_ref("table name after JOIN")
            self._expect_keyword("ON", "after the joined table")
            joins.append(JoinClause(table, self._parse_expression()))
        return tuple(joins)

    def _parse_table_ref(self, context: str) -> TableRef:
        name = self._expect_identifier(context)
        alias: str | None = None
        if self._accept_keyword("AS") is not None:
            alias = self._expect_identifier("table alias after AS")
        elif self._peek().kind == "IDENT":
            alias = str(self._advance().value)
        return TableRef(name, alias)

    # --- select list ------------------------------------------------------

    def _parse_select_list(self) -> tuple[SelectItem, ...]:
        items = [self._parse_select_item()]
        while self._accept_punct(","):
            items.append(self._parse_select_item())
        return tuple(items)

    def _parse_select_item(self) -> SelectItem:
        token = self._peek()
        if token.kind == "OP" and token.value == "*":
            self._advance()
            return SelectItem(Star(None), None, "*")
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
            return SelectItem(Star(table), None, f"{table}.*")

        start = self._pos
        expr = self._parse_expression()
        source_text = self._source_text(start, self._pos)

        alias: str | None = None
        if self._accept_keyword("AS") is not None:
            alias = self._expect_identifier("alias after AS")
        elif self._peek().kind == "IDENT":
            alias = str(self._advance().value)
        return SelectItem(expr, alias, source_text)

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
        descending = False
        if self._accept_keyword("DESC") is not None:
            descending = True
        elif self._accept_keyword("ASC") is not None:
            descending = False
        return OrderItem(expr, descending)

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

    # --- expressions ------------------------------------------------------

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
                        "expected NULL after IS, found "
                        f"{self._describe(null_token)}"
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
        if self._at_op(*_SIGN_OPS):
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
                column = self._expect_identifier("column name after '.'")
                return ColumnRef(name, column)
            return ColumnRef(None, name)
        raise QueryError(f"unexpected {self._describe(token)} in expression")

    def _parse_function_call(self) -> Expr:
        name_token = self._advance()
        written = str(name_token.value)
        name = written.upper()
        self._expect_punct("(", f"after function name '{written}'")
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


def parse(sql: str) -> SelectStmt:
    """Parse `sql` into a `SelectStmt`, raising `QueryError` on any problem."""
    try:
        token_list = tokenize(sql)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"could not read the query: {exc}") from exc

    parser = _Parser(token_list)
    try:
        return parser.parse_statement()
    except QueryError:
        raise
    except RecursionError as exc:
        raise QueryError("query is nested too deeply to parse") from exc
    except Exception as exc:
        raise QueryError(f"malformed query: {exc}") from exc
