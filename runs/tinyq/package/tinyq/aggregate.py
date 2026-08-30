"""Aggregate functions and grouping helpers for tinyq."""

FUNCTIONS = ("count", "sum", "avg", "min", "max")


def _numeric(values):
    return [v for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def apply(func: str, values: list):
    """Apply an aggregate to a list of values.

    count: number of non-None values; 'count' over ['*'] style input counts
      every element including None.
    sum/avg: over non-None numeric values; sum of nothing is 0, avg of
      nothing is None. avg returns a float.
    min/max: over non-None values; None if there are none.
    Raise ValueError(f'unknown aggregate: {func}') otherwise.
    """
    name = func.lower() if isinstance(func, str) else func
    if name not in FUNCTIONS:
        raise ValueError(f"unknown aggregate: {func}")

    values = list(values)

    if name == "count":
        if any(v == "*" for v in values):
            return len(values)
        return sum(1 for v in values if v is not None)

    if name == "sum":
        total = 0
        for v in _numeric(values):
            total += v
        return total

    if name == "avg":
        numbers = _numeric(values)
        if not numbers:
            return None
        return sum(numbers) / len(numbers)

    present = [v for v in values if v is not None]
    if not present:
        return None
    return min(present) if name == "min" else max(present)


def group_indices(table, names: list) -> dict:
    """Return {group_key_tuple: [row indices]} preserving first-seen group
    order. `names` may be empty, in which case every row is in one group
    keyed by the empty tuple.
    """
    nrows = table.nrows()
    if not names:
        return {(): list(range(nrows))}

    columns = [table.column(name).values for name in names]
    groups: dict = {}
    for i in range(nrows):
        key = tuple(column[i] for column in columns)
        groups.setdefault(key, []).append(i)
    return groups
