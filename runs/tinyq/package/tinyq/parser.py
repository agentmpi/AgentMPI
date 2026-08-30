"""Recursive-descent parser for the tinyq SQL subset."""

from tinyq.lexer import tokenize, Token

_BOOLEAN_LITERALS = {"true": True, "false": False}


class Query:
    def __init__(self, columns, table, where, group_by, order_by, limit):
        self.columns = columns
        self.table = table
        self.where = where
        self.group_by = group_by
        self.order_by = order_by
        self.limit = limit

    def __repr__(self) -> str:
        return (
            f"Query(columns={self.columns!r}, table={self.table!r}, "
            f"where={self.where!r}, group_by={self.group_by!r}, "
            f"order_by={self.order_by!r}, limit={self.limit!r})"
        )


def parse(sql: str) -> Query:
    """Parse: SELECT <cols> FROM <table> [WHERE <expr>] [GROUP BY <cols>]
    [ORDER BY <col> [ASC|DESC], ...] [LIMIT <n>]

    <cols> is '*' (which becomes [("column", "*")]) or a comma-separated list
    of either a bare column name -> ("column", name), or an aggregate call
    FUNC(arg) [AS alias] -> ("agg", func_lowercase, arg, alias_or_default)
    where the default alias is f"{func_lowercase}({arg})".

    <expr> is a boolean expression over comparisons, supporting AND, OR, NOT
    and parentheses, producing a tree of tuples:
      ("cmp", column_name, op, literal_value)
      ("and", left, right) / ("or", left, right) / ("not", child)
    Precedence, tightest first: NOT, AND, OR.

    Raises ValueError with a helpful message on malformed input.
    """
    return _Parser(tokenize(sql)).query()


def _describe(token: Token | None) -> str:
    if token is None:
        return "end of query"
    return f"{token.value!r}"


class _Parser:
    def __init__(self, tokens: list):
        self._tokens = tokens
        self._pos = 0

    def _peek(self, offset: int = 0) -> Token | None:
        index = self._pos + offset
        if index < len(self._tokens):
            return self._tokens[index]
        return None

    def _at(self, kind: str, value=None) -> bool:
        token = self._peek()
        if token is None or token.kind != kind:
            return False
        return value is None or token.value == value

    def _accept(self, kind: str, value=None) -> bool:
        if self._at(kind, value):
            self._pos += 1
            return True
        return False

    def _expect_keyword(self, word: str) -> None:
        if not self._accept("keyword", word):
            raise ValueError(
                f"expected {word.upper()}, got {_describe(self._peek())}"
            )

    def _expect_punct(self, char: str) -> None:
        if not self._accept("punct", char):
            raise ValueError(f"expected {char!r}, got {_describe(self._peek())}")

    def _expect_name(self, what: str) -> str:
        token = self._peek()
        if token is None or token.kind != "ident":
            raise ValueError(f"expected {what}, got {_describe(token)}")
        self._pos += 1
        return token.value

    def query(self) -> Query:
        if self._peek() is None:
            raise ValueError("empty query")

        self._expect_keyword("select")
        columns = self._select_list()
        self._expect_keyword("from")
        table = self._expect_name("a table name")

        where = None
        if self._accept("keyword", "where"):
            where = self._or_expression()

        group_by = []
        if self._accept("keyword", "group"):
            self._expect_keyword("by")
            group_by = self._name_list("a column name after GROUP BY")

        order_by = []
        if self._accept("keyword", "order"):
            self._expect_keyword("by")
            order_by = self._order_list()

        limit = None
        if self._accept("keyword", "limit"):
            limit = self._limit_value()

        trailing = self._peek()
        if trailing is not None:
            raise ValueError(f"unexpected input after query: {_describe(trailing)}")

        return Query(columns, table, where, group_by, order_by, limit)

    def _select_list(self) -> list:
        if self._accept("punct", "*"):
            return [("column", "*")]
        columns = [self._select_item()]
        while self._accept("punct", ","):
            columns.append(self._select_item())
        return columns

    def _select_item(self):
        token = self._peek()
        if token is None or token.kind != "ident":
            raise ValueError(
                f"expected a column name or aggregate, got {_describe(token)}"
            )
        if self._at_aggregate_call():
            return self._aggregate()

        self._pos += 1
        if self._at("keyword", "as"):
            raise ValueError(
                f"AS alias is only allowed on an aggregate, not on column "
                f"{token.value!r}"
            )
        return ("column", token.value)

    def _at_aggregate_call(self) -> bool:
        following = self._peek(1)
        return (
            following is not None
            and following.kind == "punct"
            and following.value == "("
        )

    def _aggregate(self):
        func = self._peek().value.lower()
        self._pos += 1
        self._expect_punct("(")
        if self._accept("punct", "*"):
            arg = "*"
        else:
            arg = self._expect_name(f"an argument for {func.upper()}")
        self._expect_punct(")")

        alias = f"{func}({arg})"
        if self._accept("keyword", "as"):
            alias = self._expect_name("an alias after AS")
        return ("agg", func, arg, alias)

    def _name_list(self, what: str) -> list:
        names = [self._expect_name(what)]
        while self._accept("punct", ","):
            names.append(self._expect_name(what))
        return names

    def _order_list(self) -> list:
        items = [self._order_item()]
        while self._accept("punct", ","):
            items.append(self._order_item())
        return items

    def _order_item(self):
        name = self._expect_name("a column name after ORDER BY")
        if self._accept("keyword", "desc"):
            return (name, True)
        self._accept("keyword", "asc")
        return (name, False)

    def _limit_value(self) -> int:
        token = self._peek()
        if token is None or token.kind != "number" or not isinstance(token.value, int):
            raise ValueError(f"expected an integer after LIMIT, got {_describe(token)}")
        self._pos += 1
        return token.value

    def _or_expression(self):
        node = self._and_expression()
        while self._accept("keyword", "or"):
            node = ("or", node, self._and_expression())
        return node

    def _and_expression(self):
        node = self._not_expression()
        while self._accept("keyword", "and"):
            node = ("and", node, self._not_expression())
        return node

    def _not_expression(self):
        if self._accept("keyword", "not"):
            return ("not", self._not_expression())
        return self._primary()

    def _primary(self):
        if self._accept("punct", "("):
            node = self._or_expression()
            self._expect_punct(")")
            return node
        return self._comparison()

    def _comparison(self):
        name = self._expect_name("a column name in WHERE")
        token = self._peek()
        if token is None or token.kind != "op":
            raise ValueError(
                f"expected a comparison operator after {name!r}, "
                f"got {_describe(token)}"
            )
        self._pos += 1
        return ("cmp", name, token.value, self._literal())

    def _literal(self):
        token = self._peek()
        if token is not None:
            if token.kind in ("number", "string"):
                self._pos += 1
                return token.value
            if token.kind == "ident" and token.value.lower() in _BOOLEAN_LITERALS:
                self._pos += 1
                return _BOOLEAN_LITERALS[token.value.lower()]
        raise ValueError(f"expected a literal value, got {_describe(token)}")
