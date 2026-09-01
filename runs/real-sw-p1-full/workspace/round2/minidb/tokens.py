"""Lexer for the minidb SQL surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import QueryError

#: Token kinds produced by :func:`tokenize`.
NUMBER = "number"
STRING = "string"
NAME = "name"
OP = "op"
EOF = "eof"

_TWO_CHAR_OPS = ("<>", "!=", "<=", ">=")
_ONE_CHAR_OPS = frozenset("=<>+-*/(),.;")


@dataclass
class Token:
    """A single lexical token.

    ``kind`` is one of ``"number"``, ``"string"``, ``"name"``, ``"op"`` or
    ``"eof"``.  ``value`` is the decoded literal for numbers and strings, the
    raw text for names and operators, and ``None`` for end of input.  ``pos``
    is the offset of the token's first character in the source string; the
    trailing ``"eof"`` token has ``pos == len(sql)``.
    """

    kind: str
    value: Any
    pos: int


def tokenize(sql: str) -> list[Token]:
    """Split ``sql`` into tokens, always ending with an ``"eof"`` token.

    Raises :class:`~minidb.errors.QueryError` for unterminated string
    literals, malformed numbers, and characters that cannot start a token.
    """
    if not isinstance(sql, str):
        raise QueryError("the query must be a string")

    out: list[Token] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "'":
            length = _string_length(sql, i)
            out.append(_read_string(sql, i))
            i += length
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            token, i = _read_number(sql, i)
            out.append(token)
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append(Token(NAME, sql[i:j], i))
            i = j
            continue
        two = sql[i : i + 2]
        if two in _TWO_CHAR_OPS:
            out.append(Token(OP, two, i))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            out.append(Token(OP, ch, i))
            i += 1
            continue
        raise QueryError(f"unexpected character {ch!r} at position {i}")

    out.append(Token(EOF, None, n))
    return out


def _string_length(sql: str, start: int) -> int:
    """Return the number of source characters spanned by a string literal."""
    i = start + 1
    n = len(sql)
    while i < n:
        if sql[i] == "'":
            if i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            return i + 1 - start
        i += 1
    raise QueryError(f"unterminated string literal at position {start}")


def _read_string(sql: str, start: int) -> Token:
    i = start + 1
    n = len(sql)
    chunks: list[str] = []
    while i < n:
        ch = sql[i]
        if ch == "'":
            if i + 1 < n and sql[i + 1] == "'":
                chunks.append("'")
                i += 2
                continue
            return Token(STRING, "".join(chunks), start)
        chunks.append(ch)
        i += 1
    raise QueryError(f"unterminated string literal at position {start}")


def _read_number(sql: str, start: int) -> tuple[Token, int]:
    n = len(sql)
    i = start
    seen_dot = False
    seen_exp = False
    while i < n:
        ch = sql[i]
        if ch.isdigit():
            i += 1
        elif ch == "." and not seen_dot and not seen_exp:
            seen_dot = True
            i += 1
        elif ch in "eE" and not seen_exp and _exponent_follows(sql, i):
            seen_exp = True
            i += 2
        else:
            break
    text = sql[start:i]
    try:
        value: Any = float(text) if (seen_dot or seen_exp) else int(text)
    except ValueError as exc:
        raise QueryError(f"malformed number {text!r} at position {start}") from exc
    return Token(NUMBER, value, start), i


def _exponent_follows(sql: str, i: int) -> bool:
    n = len(sql)
    if i + 1 >= n:
        return False
    nxt = sql[i + 1]
    if nxt.isdigit():
        return True
    return nxt in "+-" and i + 2 < n and sql[i + 2].isdigit()
