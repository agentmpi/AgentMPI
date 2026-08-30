"""Columnar table primitives and CSV cell typing for tinyq."""

import re

_INT_RE = re.compile(r"[+-]?[0-9]+")
_FLOAT_RE = re.compile(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?")


class Column:
    def __init__(self, name: str, kind: str, values: list):
        self.name = name
        self.kind = kind
        self.values = list(values)

    def __repr__(self):
        return f"Column({self.name!r}, {self.kind!r}, {self.values!r})"


class Table:
    def __init__(self, columns: list):
        self.columns = list(columns)

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)

    def names(self) -> list:
        return [col.name for col in self.columns]

    def nrows(self) -> int:
        if not self.columns:
            return 0
        return len(self.columns[0].values)

    def row(self, i: int) -> dict:
        return {col.name: col.values[i] for col in self.columns}

    def select(self, names: list) -> "Table":
        return Table([Column(c.name, c.kind, c.values) for c in map(self.column, names)])

    def take(self, indices: list) -> "Table":
        return Table(
            [
                Column(col.name, col.kind, [col.values[i] for i in indices])
                for col in self.columns
            ]
        )

    def __repr__(self):
        return f"Table({self.columns!r})"


def coerce(text: str) -> object:
    """Parse one CSV cell into an int, float, bool, str or None."""
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    if text == "":
        return None
    stripped = text.strip()
    if _INT_RE.fullmatch(stripped):
        return int(stripped)
    if _FLOAT_RE.fullmatch(stripped):
        return float(stripped)
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return text


def infer_kind(values: list) -> str:
    """Return the tinyq kind covering `values`, ignoring None."""
    has_bool = has_int = has_float = has_other = False
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            has_bool = True
        elif isinstance(value, int):
            has_int = True
        elif isinstance(value, float):
            has_float = True
        else:
            has_other = True
    if has_other:
        return "str"
    if has_bool:
        # bool only describes a column of pure booleans; anything mixed in is opaque.
        return "bool" if not (has_int or has_float) else "str"
    if has_float:
        return "float"
    if has_int:
        return "int"
    return "str"
