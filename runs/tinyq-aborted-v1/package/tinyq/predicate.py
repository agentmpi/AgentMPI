"""Evaluation of predicate trees produced by :mod:`tinyq.parser`.

Node shapes, exactly as the parser emits them::

    ("cmp", name, op, literal)
    ("and", left, right)
    ("or", left, right)
    ("not", child)

A ``None`` node is an absent WHERE clause and matches every row.
"""

_OPS = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _compare(name, op, literal, row):
    if name not in row:
        raise KeyError(name)
    if op not in _OPS:
        raise ValueError(f"unknown operator: {op}")
    value = row[name]
    if value is None or literal is None:
        return op == "!="
    return bool(_OPS[op](value, literal))


def evaluate(node, row: dict) -> bool:
    """Evaluate a predicate tree against one row dict."""
    if node is None:
        return True
    if not isinstance(node, tuple) or not node:
        raise ValueError(f"malformed predicate node: {node!r}")

    kind = node[0]
    if kind == "cmp":
        _, name, op, literal = node
        return _compare(name, op, literal, row)
    if kind == "and":
        return evaluate(node[1], row) and evaluate(node[2], row)
    if kind == "or":
        return evaluate(node[1], row) or evaluate(node[2], row)
    if kind == "not":
        return not evaluate(node[1], row)
    raise ValueError(f"unknown predicate node: {kind!r}")


def filter_rows(node, table) -> list[int]:
    """Return the indices of the rows of ``table`` satisfying ``node``."""
    n = table.nrows()
    if node is None:
        return list(range(n))
    return [i for i in range(n) if evaluate(node, table.row(i))]
