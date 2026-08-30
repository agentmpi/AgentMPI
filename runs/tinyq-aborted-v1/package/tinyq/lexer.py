"""Tokeniser for the tinyq SQL subset."""

KEYWORDS = {"select", "from", "where", "group", "by", "order",
            "asc", "desc", "limit", "and", "or", "not", "as"}

_DIGITS = "0123456789"
_PUNCTUATION = (",", "(", ")", "*")
_TWO_CHAR_OPS = ("!=", "<=", ">=")
_ONE_CHAR_OPS = ("=", "<", ">")


class Token:
    def __init__(self, kind: str, value):
        self.kind = kind
        self.value = value

    def __repr__(self) -> str:
        return f"Token({self.kind!r}, {self.value!r})"


def tokenize(sql: str) -> list[Token]:
    """Tokenise a query.

    Case-insensitive keywords are emitted with kind 'keyword' and a
    lower-cased string value.  Identifiers keep their case and have kind
    'ident'.  Numbers become int or float with kind 'number'.  Single-quoted
    strings become kind 'string' with the quotes removed.  Operators are '=',
    '!=', '<', '<=', '>', '>=' with kind 'op'.  Punctuation is ',', '(', ')',
    '*' with kind 'punct'.  Raises ValueError('unexpected character: X') on
    anything else.
    """
    tokens = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        if ch.isspace():
            i += 1
            continue

        if ch == "'":
            value, i = _read_string(sql, i)
            tokens.append(Token("string", value))
            continue

        if ch in _DIGITS or (ch == "." and i + 1 < n and sql[i + 1] in _DIGITS):
            value, i = _read_number(sql, i)
            tokens.append(Token("number", value))
            continue

        if ch.isalpha() or ch == "_":
            word, i = _read_word(sql, i)
            lowered = word.lower()
            if lowered in KEYWORDS:
                tokens.append(Token("keyword", lowered))
            else:
                tokens.append(Token("ident", word))
            continue

        if ch in _PUNCTUATION:
            tokens.append(Token("punct", ch))
            i += 1
            continue

        if sql[i:i + 2] in _TWO_CHAR_OPS:
            tokens.append(Token("op", sql[i:i + 2]))
            i += 2
            continue

        if ch in _ONE_CHAR_OPS:
            tokens.append(Token("op", ch))
            i += 1
            continue

        raise ValueError(f"unexpected character: {ch}")

    return tokens


def _read_word(sql: str, start: int) -> tuple[str, int]:
    i = start
    n = len(sql)
    while i < n and (sql[i].isalnum() or sql[i] == "_"):
        i += 1
    return sql[start:i], i


def _read_number(sql: str, start: int) -> tuple[object, int]:
    i = start
    n = len(sql)
    is_float = False

    while i < n and sql[i] in _DIGITS:
        i += 1

    if i < n and sql[i] == ".":
        is_float = True
        i += 1
        while i < n and sql[i] in _DIGITS:
            i += 1

    # An exponent only counts when real digits follow it, so that the 'e' of
    # an identifier butted up against a number is left alone.
    if i < n and sql[i] in "eE":
        j = i + 1
        if j < n and sql[j] in "+-":
            j += 1
        if j < n and sql[j] in _DIGITS:
            is_float = True
            i = j
            while i < n and sql[i] in _DIGITS:
                i += 1

    text = sql[start:i]
    return (float(text) if is_float else int(text)), i


def _read_string(sql: str, start: int) -> tuple[str, int]:
    i = start + 1
    n = len(sql)
    chars = []
    while i < n:
        ch = sql[i]
        if ch == "'":
            if i + 1 < n and sql[i + 1] == "'":
                chars.append("'")
                i += 2
                continue
            return "".join(chars), i + 1
        chars.append(ch)
        i += 1
    raise ValueError("unterminated string literal")
