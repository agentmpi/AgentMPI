"""Recursive-descent parser for the minidb SQL surface."""

from __future__ import annotations

from typing import Any

from . import nodes
from .errors import QueryError
from .tokens import EOF, NAME, NUMBER, OP, STRING, Token, tokenize

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
        "BY",
        "HAVING",
        "ORDER",
        "ASC",
        "DESC",
        "LIMIT",
        "OFFSET",
        "AS",
        "AND",
        "OR",
        "NOT",
        "IS",
        "NULL",
        "IN",
        "LIKE",
    }
)

_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})


def parse(sql: str) -> nodes.Select:
    """Parse ``sql`` into a :class:`~minidb.nodes.Select`.

    Raises :class:`~minidb.errors.QueryError` for any syntax error.
    """
    parser = _Parser(sql)
    select = parser.parse_select()
    parser.eat_op(";")
    if parser.peek().kind != EOF:
        parser.fail("unexpected trailing input")
    return select


class _Parser:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.tokens = tokenize(sql)
        self.i = 0

    # -- token helpers -------------------------------------------------
    def peek(self, ahead: int = 0) -> Token:
        index = self.i + ahead
        if index >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[index]

    def advance(self) -> Token:
        token = self.peek()
        if token.kind != EOF:
            self.i += 1
        return token

    def at_op(self, *ops: str) -> bool:
        token = self.peek()
        return token.kind == OP and token.value in ops

    def at_keyword(self, *words: str) -> bool:
        token = self.peek()
        return token.kind == NAME and token.value.upper() in words

    def eat_op(self, op: str) -> bool:
        if self.at_op(op):
            self.i += 1
            return True
        return False

    def eat_keyword(self, word: str) -> bool:
        if self.at_keyword(word):
            self.i += 1
            return True
        return False

    def expect_op(self, op: str) -> None:
        if not self.eat_op(op):
            self.fail(f"expected {op!r}")

    def expect_keyword(self, word: str) -> None:
        if not self.eat_keyword(word):
            self.fail(f"expected {word}")

    def fail(self, message: str) -> None:
        token = self.peek()
        if token.kind == EOF:
            raise QueryError(f"{message} but the query ended")
        raise QueryError(f"{message} at position {token.pos}")

    def identifier(self) -> str:
        token = self.peek()
        if token.kind != NAME:
            self.fail("expected an identifier")
        if token.value.upper() in KEYWORDS:
            self.fail(f"unexpected keyword {token.value!r}")
        self.i += 1
        return token.value

    def at_identifier(self) -> bool:
        token = self.peek()
        return token.kind == NAME and token.value.upper() not in KEYWORDS

    def span(self, start_index: int, end_index: int) -> str:
        start = self.peek(start_index - self.i).pos
        end = self.peek(end_index - self.i).pos
        return "".join(self.sql[start:end].split())

    # -- statement -----------------------------------------------------
    def parse_select(self) -> nodes.Select:
        self.expect_keyword("SELECT")
        distinct = self.eat_keyword("DISTINCT")
        items = self.parse_select_list()
        self.expect_keyword("FROM")
        from_table = self.parse_table_ref()

        joins: list[nodes.Join] = []
        while True:
            if self.at_keyword("INNER"):
                self.i += 1
                self.expect_keyword("JOIN")
            elif self.at_keyword("JOIN"):
                self.i += 1
            else:
                break
            table = self.parse_table_ref()
            self.expect_keyword("ON")
            joins.append(nodes.Join(table, self.parse_expr()))

        where = self.parse_expr() if self.eat_keyword("WHERE") else None

        group_by: list[Any] = []
        if self.eat_keyword("GROUP"):
            self.expect_keyword("BY")
            group_by.append(self.parse_expr())
            while self.eat_op(","):
                group_by.append(self.parse_expr())

        having = self.parse_expr() if self.eat_keyword("HAVING") else None

        order_by: list[nodes.OrderKey] = []
        if self.eat_keyword("ORDER"):
            self.expect_keyword("BY")
            order_by.append(self.parse_order_key())
            while self.eat_op(","):
                order_by.append(self.parse_order_key())

        limit: int | None = None
        offset: int | None = None
        while True:
            if self.eat_keyword("LIMIT"):
                if limit is not None:
                    self.fail("duplicate LIMIT clause")
                limit = self.parse_count("LIMIT")
            elif self.eat_keyword("OFFSET"):
                if offset is not None:
                    self.fail("duplicate OFFSET clause")
                offset = self.parse_count("OFFSET")
            else:
                break

        return nodes.Select(
            items=items,
            from_table=from_table,
            joins=joins,
            distinct=distinct,
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def parse_count(self, clause: str) -> int:
        negative = False
        if self.at_op("-"):
            self.i += 1
            negative = True
        elif self.at_op("+"):
            self.i += 1
        token = self.peek()
        if token.kind != NUMBER:
            self.fail(f"{clause} requires an integer")
        self.i += 1
        value = token.value
        if not isinstance(value, int):
            raise QueryError(f"{clause} requires an integer, got {value!r}")
        if negative or value < 0:
            raise QueryError(f"{clause} must not be negative")
        return value

    def parse_table_ref(self) -> nodes.TableRef:
        name = self.identifier()
        alias = None
        if self.eat_keyword("AS"):
            alias = self.identifier()
        elif self.at_identifier():
            alias = self.identifier()
        return nodes.TableRef(name, alias)

    def parse_select_list(self) -> list[nodes.SelectItem]:
        items: list[nodes.SelectItem] = []
        while True:
            start = self.i
            if self.at_op("*"):
                self.i += 1
                items.append(nodes.SelectItem(nodes.Star(None), None, "*"))
            elif (
                self.at_identifier()
                and self.peek(1).kind == OP
                and self.peek(1).value == "."
                and self.peek(2).kind == OP
                and self.peek(2).value == "*"
            ):
                table = self.identifier()
                self.i += 2
                items.append(
                    nodes.SelectItem(nodes.Star(table), None, f"{table}.*")
                )
            else:
                expr = self.parse_expr()
                text = self.span(start, self.i)
                alias = None
                if self.eat_keyword("AS"):
                    alias = self.identifier()
                elif self.at_identifier():
                    alias = self.identifier()
                items.append(nodes.SelectItem(expr, alias, text))
            if not self.eat_op(","):
                break
        return items

    def parse_order_key(self) -> nodes.OrderKey:
        expr = self.parse_expr()
        desc = False
        if self.eat_keyword("DESC"):
            desc = True
        else:
            self.eat_keyword("ASC")
        return nodes.OrderKey(expr, desc)

    # -- expressions ---------------------------------------------------
    def parse_expr(self) -> Any:
        return self.parse_or()

    def parse_or(self) -> Any:
        left = self.parse_and()
        while self.eat_keyword("OR"):
            left = nodes.BinOp("OR", left, self.parse_and())
        return left

    def parse_and(self) -> Any:
        left = self.parse_not()
        while self.eat_keyword("AND"):
            left = nodes.BinOp("AND", left, self.parse_not())
        return left

    def parse_not(self) -> Any:
        if self.eat_keyword("NOT"):
            return nodes.UnaryOp("NOT", self.parse_not())
        return self.parse_predicate()

    def parse_predicate(self) -> Any:
        left = self.parse_additive()

        if self.eat_keyword("IS"):
            negated = self.eat_keyword("NOT")
            self.expect_keyword("NULL")
            return nodes.UnaryOp("IS NOT NULL" if negated else "IS NULL", left)

        negated = False
        if self.at_keyword("NOT") and self.peek(1).kind == NAME:
            follower = self.peek(1).value.upper()
            if follower in ("IN", "LIKE"):
                self.i += 1
                negated = True

        if self.eat_keyword("IN"):
            self.expect_op("(")
            options: list[Any] = []
            if self.at_op(")"):
                self.fail("IN requires at least one value")
            options.append(self.parse_expr())
            while self.eat_op(","):
                options.append(self.parse_expr())
            self.expect_op(")")
            node: Any = nodes.BinOp("IN", left, options)
            return nodes.UnaryOp("NOT", node) if negated else node

        if self.eat_keyword("LIKE"):
            pattern = self.parse_additive()
            node = nodes.BinOp("LIKE", left, pattern)
            return nodes.UnaryOp("NOT", node) if negated else node

        if negated:
            self.fail("expected IN or LIKE after NOT")

        token = self.peek()
        if token.kind == OP and token.value in _COMPARISONS:
            self.i += 1
            return nodes.BinOp(token.value, left, self.parse_additive())
        return left

    def parse_additive(self) -> Any:
        left = self.parse_multiplicative()
        while self.at_op("+", "-"):
            op = self.advance().value
            left = nodes.BinOp(op, left, self.parse_multiplicative())
        return left

    def parse_multiplicative(self) -> Any:
        left = self.parse_unary()
        while self.at_op("*", "/"):
            op = self.advance().value
            left = nodes.BinOp(op, left, self.parse_unary())
        return left

    def parse_unary(self) -> Any:
        if self.at_op("-"):
            self.i += 1
            return nodes.UnaryOp("-", self.parse_unary())
        if self.at_op("+"):
            self.i += 1
            return nodes.UnaryOp("+", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> Any:
        token = self.peek()
        if token.kind in (NUMBER, STRING):
            self.i += 1
            return nodes.Literal(token.value)
        if token.kind == OP and token.value == "(":
            self.i += 1
            inner = self.parse_expr()
            self.expect_op(")")
            return inner
        if token.kind == NAME:
            word = token.value.upper()
            if word == "NULL":
                self.i += 1
                return nodes.Literal(None)
            if word == "TRUE":
                self.i += 1
                return nodes.Literal(True)
            if word == "FALSE":
                self.i += 1
                return nodes.Literal(False)
            if word in KEYWORDS:
                self.fail(f"unexpected keyword {token.value!r}")
            nxt = self.peek(1)
            if nxt.kind == OP and nxt.value == "(":
                return self.parse_call(token.value)
            self.i += 1
            if self.at_op("."):
                self.i += 1
                return nodes.Column(token.value, self.identifier())
            return nodes.Column(None, token.value)
        self.fail("expected an expression")
        raise QueryError("expected an expression")

    def parse_call(self, name: str) -> nodes.Func:
        self.i += 2
        if self.at_op("*"):
            self.i += 1
            self.expect_op(")")
            return nodes.Func(name.upper(), [], True)
        args: list[Any] = []
        if not self.at_op(")"):
            args.append(self.parse_expr())
            while self.eat_op(","):
                args.append(self.parse_expr())
        self.expect_op(")")
        return nodes.Func(name.upper(), args, False)
