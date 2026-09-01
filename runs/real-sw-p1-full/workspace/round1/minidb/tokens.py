"""Lexical analysis for the minidb SQL subset."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import QueryError

__all__ = ["Token", "tokenize"]

_SYMBOLS = ("<>", "!=", "<=", ">=", "=", "<", ">", "+", "-", "*", "/", "(", ")", ",", ".")


@dataclass(frozen=True)
class Token:
    """A single lexical token: its kind, its text value and its offset."""

    kind: str
    value: str
    pos: int


def tokenize(sql: str) -> list[Token]:
    """Split `sql` into tokens, ending with a single token of kind `eof`."""
    if not isinstance(sql, str):
        raise QueryError(f"sql must be a string, not {type(sql).__name__}")
    tokens: list[Token] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        start = index
        if char == "'":
            value, index = _read_string(sql, index)
            tokens.append(Token("string", value, start))
            continue
        if char.isdigit() or (char == "." and index + 1 < length and sql[index + 1].isdigit()):
            value, index = _read_number(sql, index)
            tokens.append(Token("number", value, start))
            continue
        if char.isalpha() or char == "_":
            end = index
            while end < length and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            tokens.append(Token("name", sql[index:end], start))
            index = end
            continue
        for symbol in _SYMBOLS:
            if sql.startswith(symbol, index):
                tokens.append(Token("op", symbol, start))
                index += len(symbol)
                break
        else:
            raise QueryError(f"unexpected character {char!r} at position {index}")
    tokens.append(Token("eof", "", length))
    return tokens


def _read_string(sql: str, index: int) -> tuple[str, int]:
    length = len(sql)
    cursor = index + 1
    parts: list[str] = []
    while cursor < length:
        char = sql[cursor]
        if char == "'":
            if cursor + 1 < length and sql[cursor + 1] == "'":
                parts.append("'")
                cursor += 2
                continue
            return "".join(parts), cursor + 1
        parts.append(char)
        cursor += 1
    raise QueryError(f"unterminated string literal at position {index}")


def _read_number(sql: str, index: int) -> tuple[str, int]:
    length = len(sql)
    cursor = index
    seen_dot = False
    while cursor < length and (sql[cursor].isdigit() or (sql[cursor] == "." and not seen_dot)):
        if sql[cursor] == ".":
            seen_dot = True
        cursor += 1
    if cursor < length and sql[cursor] in "eE":
        lookahead = cursor + 1
        if lookahead < length and sql[lookahead] in "+-":
            lookahead += 1
        if lookahead < length and sql[lookahead].isdigit():
            while lookahead < length and sql[lookahead].isdigit():
                lookahead += 1
            cursor = lookahead
    return sql[index:cursor], cursor
