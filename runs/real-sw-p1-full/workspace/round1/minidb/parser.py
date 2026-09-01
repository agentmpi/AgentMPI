"""Recursive-descent parser for the minidb SQL subset."""

from __future__ import annotations

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
from .tokens import Token, tokenize

__all__ = ["parse"]

_KEYWORDS = frozenset(
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
        "AND",
        "OR",
        "NOT",
        "IS",
        "NULL",
        "IN",
        "LIKE",
        "AS",
    }
)

_COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})


def parse(sql: str) -> Select:
    """Parse `sql` into a Select node, raising QueryError on malformed input."""
    parser = _Parser(sql)
    select = parser.parse_select()
    parser.expect_eof()
    return select


def _number_value(text: str) -> object:
    try:
        if "." in text or "e" in text or "E" in text:
            return float(text)
        return int(text)
    except ValueError:
        raise QueryError(f"malformed number: {text}") from None


class _Parser:
    def __init__(self, sql: str) -> None:
        self._sql = sql
        self._tokens = tokenize(sql)
        self._index = 0

    def _peek(self, offset: int = 0) -> Token:
        index = min(self._index + offset, len(self._tokens) - 1)
        return self._tokens[index]

    def _advance(self) -> Token:
        token = self._peek()
        if token.kind != "eof":
            self._index += 1
        return token

    def _at_keyword(self, *words: str) -> bool:
        token = self._peek()
        return token.kind == "name" and token.value.upper() in words

    def _accept_keyword(self, *words: str) -> bool:
        if self._at_keyword(*words):
            self._index += 1
            return True
        return False

    def _expect_keyword(self, word: str) -> None:
        if not self._accept_keyword(word):
            token = self._peek()
            raise QueryError(f"expected {word} at position {token.pos}")

    def _at_op(self, *symbols: str) -> bool:
        token = self._peek()
        return token.kind == "op" and token.value in symbols

    def _accept_op(self, *symbols: str) -> bool:
        if self._at_op(*symbols):
            self._index += 1
            return True
        return False

    def _expect_op(self, symbol: str) -> None:
        if not self._accept_op(symbol):
            token = self._peek()
            raise QueryError(f"expected {symbol!r} at position {token.pos}")

    def _at_identifier(self) -> bool:
        token = self._peek()
        return token.kind == "name" and token.value.upper() not in _KEYWORDS

    def _identifier(self) -> str:
        token = self._peek()
        if not self._at_identifier():
            raise QueryError(f"expected an identifier at position {token.pos}")
        self._index += 1
        return token.value

    def expect_eof(self) -> None:
        token = self._peek()
        if token.kind != "eof":
            raise QueryError(f"unexpected {token.value!r} at position {token.pos}")

    def _source_from(self, start: int) -> str:
        return "".join(self._sql[start : self._peek().pos].split())

    def parse_select(self) -> Select:
        self._expect_keyword("SELECT")
        distinct = self._accept_keyword("DISTINCT")
        items = self._parse_select_list()
        self._expect_keyword("FROM")
        from_ = self._parse_table_ref()
        joins = []
        while True:
            if self._at_keyword("INNER"):
                self._index += 1
                self._expect_keyword("JOIN")
            elif not self._accept_keyword("JOIN"):
                break
            table = self._parse_table_ref()
            self._expect_keyword("ON")
            joins.append(Join(table, self._parse_expression()))
        where = self._parse_expression() if self._accept_keyword("WHERE") else None
        group_by = []
        if self._accept_keyword("GROUP"):
            self._expect_keyword("BY")
            group_by.append(self._parse_expression())
            while self._accept_op(","):
                group_by.append(self._parse_expression())
        having = self._parse_expression() if self._accept_keyword("HAVING") else None
        order_by = []
        if self._accept_keyword("ORDER"):
            self._expect_keyword("BY")
            order_by.append(self._parse_order_key())
            while self._accept_op(","):
                order_by.append(self._parse_order_key())
        limit = self._parse_count("LIMIT") if self._accept_keyword("LIMIT") else None
        offset = self._parse_count("OFFSET") if self._accept_keyword("OFFSET") else None
        return Select(
            distinct,
            tuple(items),
            from_,
            tuple(joins),
            where,
            tuple(group_by),
            having,
            tuple(order_by),
            limit,
            offset,
        )

    def _parse_count(self, word: str) -> int:
        negative = self._accept_op("-")
        if not negative:
            self._accept_op("+")
        token = self._peek()
        if token.kind != "number":
            raise QueryError(f"{word} requires an integer")
        self._index += 1
        try:
            value = int(token.value)
        except ValueError:
            raise QueryError(f"{word} requires an integer") from None
        if negative:
            value = -value
        if value < 0:
            raise QueryError(f"{word} must not be negative")
        return value

    def _parse_table_ref(self) -> TableRef:
        name = self._identifier()
        alias = None
        if self._accept_keyword("AS"):
            alias = self._identifier()
        elif self._at_identifier():
            alias = self._identifier()
        return TableRef(name, alias)

    def _parse_select_list(self) -> list[SelectItem]:
        items = [self._parse_select_item()]
        while self._accept_op(","):
            items.append(self._parse_select_item())
        return items

    def _parse_select_item(self) -> SelectItem:
        start = self._peek().pos
        if self._at_op("*"):
            self._index += 1
            return SelectItem(Star(None), None, "*")
        if (
            self._peek().kind == "name"
            and self._peek(1).kind == "op"
            and self._peek(1).value == "."
            and self._peek(2).kind == "op"
            and self._peek(2).value == "*"
        ):
            qualifier = self._peek().value
            self._index += 3
            return SelectItem(Star(qualifier), None, f"{qualifier}.*")
        expr = self._parse_expression()
        source_text = self._source_from(start)
        alias = None
        if self._accept_keyword("AS"):
            alias = self._identifier()
        elif self._at_identifier():
            alias = self._identifier()
        return SelectItem(expr, alias, source_text)

    def _parse_order_key(self) -> OrderKey:
        start = self._peek().pos
        expr = self._parse_expression()
        source_text = self._source_from(start)
        descending = False
        if self._accept_keyword("DESC"):
            descending = True
        else:
            self._accept_keyword("ASC")
        return OrderKey(expr, descending, source_text)

    def _parse_expression(self) -> object:
        return self._parse_or()

    def _parse_or(self) -> object:
        node = self._parse_and()
        while self._accept_keyword("OR"):
            node = BinOp("OR", node, self._parse_and())
        return node

    def _parse_and(self) -> object:
        node = self._parse_not()
        while self._accept_keyword("AND"):
            node = BinOp("AND", node, self._parse_not())
        return node

    def _parse_not(self) -> object:
        if self._accept_keyword("NOT"):
            return UnaryOp("NOT", self._parse_not())
        return self._parse_predicate()

    def _parse_predicate(self) -> object:
        node = self._parse_additive()
        while True:
            if self._accept_keyword("IS"):
                negated = self._accept_keyword("NOT")
                self._expect_keyword("NULL")
                node = UnaryOp("IS NOT NULL" if negated else "IS NULL", node)
                continue
            negated = False
            if self._at_keyword("NOT") and self._peek(1).kind == "name" and self._peek(1).value.upper() in ("IN", "LIKE"):
                self._index += 1
                negated = True
            if self._accept_keyword("IN"):
                self._expect_op("(")
                values = [self._parse_expression()]
                while self._accept_op(","):
                    values.append(self._parse_expression())
                self._expect_op(")")
                node = BinOp("IN", node, tuple(values))
                if negated:
                    node = UnaryOp("NOT", node)
                continue
            if self._accept_keyword("LIKE"):
                node = BinOp("LIKE", node, self._parse_additive())
                if negated:
                    node = UnaryOp("NOT", node)
                continue
            if negated:
                raise QueryError(f"expected IN or LIKE after NOT at position {self._peek().pos}")
            token = self._peek()
            if token.kind == "op" and token.value in _COMPARISONS:
                self._index += 1
                node = BinOp(token.value, node, self._parse_additive())
                continue
            return node

    def _parse_additive(self) -> object:
        node = self._parse_multiplicative()
        while self._at_op("+", "-"):
            op = self._advance().value
            node = BinOp(op, node, self._parse_multiplicative())
        return node

    def _parse_multiplicative(self) -> object:
        node = self._parse_unary()
        while self._at_op("*", "/"):
            op = self._advance().value
            node = BinOp(op, node, self._parse_unary())
        return node

    def _parse_unary(self) -> object:
        if self._at_op("-", "+"):
            op = self._advance().value
            operand = self._parse_unary()
            return UnaryOp("-", operand) if op == "-" else operand
        return self._parse_primary()

    def _parse_primary(self) -> object:
        token = self._peek()
        if token.kind == "number":
            self._index += 1
            return Literal(_number_value(token.value))
        if token.kind == "string":
            self._index += 1
            return Literal(token.value)
        if token.kind == "op" and token.value == "(":
            self._index += 1
            node = self._parse_expression()
            self._expect_op(")")
            return node
        if token.kind == "name":
            upper = token.value.upper()
            if upper == "NULL":
                self._index += 1
                return Literal(None)
            if self._peek(1).kind == "op" and self._peek(1).value == "(":
                self._index += 2
                args: list[object] = []
                if self._at_op("*"):
                    self._index += 1
                    args.append(Star(None))
                elif not self._at_op(")"):
                    args.append(self._parse_expression())
                    while self._accept_op(","):
                        args.append(self._parse_expression())
                self._expect_op(")")
                return Func(upper, tuple(args))
            if upper in _KEYWORDS:
                raise QueryError(f"unexpected keyword {token.value!r} at position {token.pos}")
            self._index += 1
            if self._at_op("."):
                self._index += 1
                return Column(token.value, self._identifier())
            return Column(None, token.value)
        if token.kind == "eof":
            raise QueryError("unexpected end of query")
        raise QueryError(f"unexpected {token.value!r} at position {token.pos}")
