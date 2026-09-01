"""Lexical analysis for minidb.

Turns SQL source text into a flat list of `Token` values. No syntactic
validation happens here: clause order, balanced parentheses and expression
structure are the parser's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QueryError

__all__ = ["Token", "tokenize"]

_KEYWORDS = frozenset(
    {
        "SELECT",
        "DISTINCT",
        "FROM",
        "AS",
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
    }
)

_TWO_CHAR_OPS = ("<>", "!=", "<=", ">=")
_ONE_CHAR_OPS = frozenset("=<>+-*/")
_PUNCT = frozenset("(),.")
_WHITESPACE = frozenset(" \t\r\n\f\v")
_DIGITS = frozenset("0123456789")


@dataclass(frozen=True)
class Token:
    """One lexical token; `pos` indexes its first character in the source."""

    kind: str
    value: str | int | float | None
    pos: int


def tokenize(sql: str) -> list[Token]:
    """Lex a complete SQL string into its tokens, in source order."""
    if not isinstance(sql, str):
        raise QueryError(f"query must be a string, not {type(sql).__name__}")

    tokens: list[Token] = []
    i = 0
    length = len(sql)
    while i < length:
        start = i
        ch = sql[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == "'":
            text, i = _read_string(sql, start)
            tokens.append(Token("STRING", text, start))
            continue
        if ch in _DIGITS:
            number, i = _read_number(sql, start)
            tokens.append(Token("NUMBER", number, start))
            continue
        if ch == "_" or ch.isalpha():
            word, i = _read_word(sql, start)
            upper = word.upper()
            if upper in _KEYWORDS:
                tokens.append(Token("KEYWORD", upper, start))
            else:
                tokens.append(Token("IDENT", word, start))
            continue
        if sql[i : i + 2] in _TWO_CHAR_OPS:
            tokens.append(Token("OP", sql[i : i + 2], start))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(Token("OP", ch, start))
            i += 1
            continue
        if ch in _PUNCT:
            tokens.append(Token("PUNCT", ch, start))
            i += 1
            continue
        raise QueryError(f"unexpected character {ch!r} at position {start}")
    return tokens


def _read_string(sql: str, start: int) -> tuple[str, int]:
    """Read a single-quoted literal, collapsing each doubled quote to one."""
    parts: list[str] = []
    i = start + 1
    length = len(sql)
    while True:
        if i >= length:
            raise QueryError(f"unterminated string literal at position {start}")
        ch = sql[i]
        if ch == "'":
            if i + 1 < length and sql[i + 1] == "'":
                parts.append("'")
                i += 2
                continue
            return "".join(parts), i + 1
        parts.append(ch)
        i += 1


def _read_number(sql: str, start: int) -> tuple[int | float, int]:
    """Read an unsigned integer or float literal."""
    i = start
    length = len(sql)
    is_float = False
    while i < length and sql[i] in _DIGITS:
        i += 1
    if i < length and sql[i] == ".":
        is_float = True
        i += 1
        if i >= length or sql[i] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:i]!r} at position {start}"
            )
        while i < length and sql[i] in _DIGITS:
            i += 1
    if i < length and sql[i] in "eE":
        j = i + 1
        if j < length and sql[j] in "+-":
            j += 1
        if j >= length or sql[j] not in _DIGITS:
            raise QueryError(
                f"malformed number literal {sql[start:j + 1]!r} at position {start}"
            )
        is_float = True
        i = j
        while i < length and sql[i] in _DIGITS:
            i += 1
    if i < length and (sql[i] == "." or sql[i] == "_" or sql[i].isalnum()):
        raise QueryError(
            f"malformed number literal {sql[start:i + 1]!r} at position {start}"
        )
    text = sql[start:i]
    try:
        return (float(text) if is_float else int(text)), i
    except ValueError as exc:
        raise QueryError(
            f"malformed number literal {text!r} at position {start}"
        ) from exc


def _read_word(sql: str, start: int) -> tuple[str, int]:
    """Read one identifier or keyword word."""
    i = start + 1
    length = len(sql)
    while i < length and (sql[i] == "_" or sql[i].isalnum()):
        i += 1
    return sql[start:i], i
