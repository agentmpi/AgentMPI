"""Lexical analysis for minidb: turning SQL text into a sequence of tokens."""

from __future__ import annotations

import dataclasses

from .errors import QueryError

__all__ = ["KEYWORDS", "Token", "tokenize"]


KEYWORDS: frozenset[str] = frozenset(
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

_WHITESPACE: frozenset[str] = frozenset(" \t\n\r\f")

_DIGITS: frozenset[str] = frozenset("0123456789")

# Longest match wins, so these are tried before the single-character operators.
# '!=' is normalised to the canonical value '<>'.
_TWO_CHAR_OPS: dict[str, str] = {"<>": "<>", "!=": "<>", "<=": "<=", ">=": ">="}

_ONE_CHAR_OPS: dict[str, str] = {
    "=": "=",
    "<": "<",
    ">": ">",
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
}

_PUNCT: frozenset[str] = frozenset("(),.")


@dataclasses.dataclass(frozen=True, slots=True)
class Token:
    """One lexical token of a SQL query.

    `kind` is one of 'KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'NULL', 'OP',
    'PUNCT', 'EOF'; `value` is the semantic value of the token; `text` is the
    exact source text ('' for 'EOF'); `pos` is the 0-based index in the query
    text where the token starts (len(sql) for 'EOF').
    """

    kind: str
    value: object
    text: str
    pos: int

    def is_keyword(self, *names: str) -> bool:
        """True for a KEYWORD token matching any of `names`, case-insensitively.

        With no names given, true for any KEYWORD token. Never raises.
        """
        if self.kind != "KEYWORD":
            return False
        if not names:
            return True
        value = self.value
        if not isinstance(value, str):
            return False
        upper = value.upper()
        for name in names:
            if isinstance(name, str) and upper == name.upper():
                return True
        return False


def _is_word_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_word_char(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


def _scan_word(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start
    while i < n and _is_word_char(sql[i]):
        i += 1
    text = sql[start:i]
    upper = text.upper()
    if upper == "NULL":
        return Token("NULL", None, text, start), i
    if upper in KEYWORDS:
        return Token("KEYWORD", upper, text, start), i
    return Token("IDENT", text, text, start), i


def _scan_string(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start + 1
    chunks: list[str] = []
    while i < n:
        ch = sql[i]
        if ch == "'":
            if i + 1 < n and sql[i + 1] == "'":
                chunks.append("'")
                i += 2
                continue
            i += 1
            return Token("STRING", "".join(chunks), sql[start:i], start), i
        chunks.append(ch)
        i += 1
    raise QueryError(
        f"unterminated string literal starting with {sql[start]!r} "
        f"at position {start}"
    )


def _scan_number(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start
    while i < n and sql[i] in _DIGITS:
        i += 1
    is_float = False
    if i < n and sql[i] == ".":
        if i + 1 >= n or sql[i + 1] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:i + 1]!r}: "
                f"expected a digit after {'.'!r} at position {i + 1}"
            )
        is_float = True
        i += 1
        while i < n and sql[i] in _DIGITS:
            i += 1
    if i < n and sql[i] in "eE":
        j = i + 1
        if j < n and sql[j] in "+-":
            j += 1
        if j >= n or sql[j] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:j + 1]!r}: "
                f"expected a digit in the exponent at position {j}"
            )
        is_float = True
        i = j
        while i < n and sql[i] in _DIGITS:
            i += 1
    if i < n and (sql[i] == "." or sql[i] in _DIGITS or _is_word_start(sql[i])):
        raise QueryError(
            f"malformed number literal {sql[start:i + 1]!r}: unexpected "
            f"{sql[i]!r} at position {i}"
        )
    text = sql[start:i]
    value: object = float(text) if is_float else int(text)
    return Token("NUMBER", value, text, start), i


def tokenize(sql: str) -> list[Token]:
    """Lex `sql` into tokens, terminated by exactly one 'EOF' token.

    Whitespace separates tokens and is discarded. Raises QueryError for an
    unterminated string literal, a malformed number, a character that cannot
    begin a token, or a non-string argument.
    """
    if not isinstance(sql, str):
        raise QueryError(
            f"query text must be a string, not {type(sql).__name__}"
        )
    tokens: list[Token] = []
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == "'":
            token, i = _scan_string(sql, i)
            tokens.append(token)
            continue
        if ch in _DIGITS:
            token, i = _scan_number(sql, i)
            tokens.append(token)
            continue
        if _is_word_start(ch):
            token, i = _scan_word(sql, i)
            tokens.append(token)
            continue
        pair = sql[i : i + 2]
        if len(pair) == 2 and pair in _TWO_CHAR_OPS:
            tokens.append(Token("OP", _TWO_CHAR_OPS[pair], pair, i))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(Token("OP", _ONE_CHAR_OPS[ch], ch, i))
            i += 1
            continue
        if ch in _PUNCT:
            tokens.append(Token("PUNCT", ch, ch, i))
            i += 1
            continue
        raise QueryError(f"unexpected character {ch!r} at position {i}")
    tokens.append(Token("EOF", None, "", n))
    return tokens
