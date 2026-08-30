"""Query execution: turn a parsed query plus a set of tables into a Table."""

from tinyq.schema import Column, Table
from tinyq.parser import parse
from tinyq.predicate import filter_rows
from tinyq.aggregate import apply as agg_apply, group_indices

STAR = [("column", "*")]


def _kind_of(values: list) -> str:
    """Infer a Column kind for a freshly computed list of values."""
    present = [v for v in values if v is not None]
    if not present:
        return "str"
    if all(isinstance(v, bool) for v in present):
        return "bool"
    if any(isinstance(v, bool) for v in present):
        return "str"
    if all(isinstance(v, int) for v in present):
        return "int"
    if all(isinstance(v, (int, float)) for v in present):
        return "float"
    return "str"


def _column_kind(table: Table, name: str, values: list) -> str:
    try:
        return table.column(name).kind
    except KeyError:
        return _kind_of(values)


def _agg_values(table: Table, arg: str, indices: list) -> list:
    # COUNT(*) counts every row, so the marker list keeps one entry per row.
    if arg == "*":
        return ["*"] * len(indices)
    return [table.row(i)[arg] for i in indices]


def _grouped(table: Table, columns: list, group_by: list) -> Table:
    if group_by:
        groups = group_indices(table, group_by)
    else:
        # Bare aggregates must yield exactly one row, even over zero rows.
        groups = group_indices(table, []) or {(): []}
    keys = list(groups.keys())

    out = []
    for item in columns:
        if item[0] == "column":
            name = item[1]
            if name not in group_by:
                raise ValueError(
                    f"column {name!r} must appear in GROUP BY or be aggregated"
                )
            pos = group_by.index(name)
            values = [key[pos] for key in keys]
            out.append(Column(name, _column_kind(table, name, values), values))
        elif item[0] == "agg":
            func, arg, alias = item[1], item[2], item[3]
            values = [
                agg_apply(func, _agg_values(table, arg, groups[key])) for key in keys
            ]
            out.append(Column(alias, _kind_of(values), values))
        else:
            raise ValueError(f"unsupported select item: {item!r}")
    return Table(out)


def _projected(table: Table, columns: list) -> Table:
    if columns == STAR:
        return table
    names = []
    for item in columns:
        if item[0] != "column":
            raise ValueError(f"unsupported select item: {item!r}")
        names.append(item[1])
    return table.select(names)


def _order_indices(order: list, values: list, descending: bool) -> list:
    def key(i):
        value = values[i]
        return (value is None, value)

    try:
        return sorted(order, key=key, reverse=descending)
    except TypeError:
        def text_key(i):
            value = values[i]
            return (value is None, "" if value is None else str(value))

        return sorted(order, key=text_key, reverse=descending)


def _ordered(table: Table, order_by: list) -> Table:
    if not order_by:
        return table
    names = table.names()
    order = list(range(table.nrows()))
    # Least significant key first: each pass is stable, so earlier keys win.
    for name, descending in reversed(order_by):
        if name not in names:
            raise ValueError(f"cannot ORDER BY unselected column: {name}")
        order = _order_indices(order, table.column(name).values, descending)
    return table.take(order)


def _limited(table: Table, limit) -> Table:
    if limit is None:
        return table
    if limit >= table.nrows():
        return table
    return table.take(list(range(limit)))


def execute(sql: str, tables: dict) -> Table:
    """Run a query. `tables` maps table name -> tinyq.schema.Table.

    Order of operations: FROM, WHERE, GROUP BY + aggregates, projection,
    ORDER BY, LIMIT.
    """
    query = parse(sql)

    if query.table not in tables:
        raise KeyError(query.table)
    source = tables[query.table]

    limit = query.limit
    if limit is not None and limit < 0:
        raise ValueError(f"LIMIT must not be negative: {limit}")

    columns = list(query.columns)
    group_by = list(query.group_by or [])
    is_star = columns == STAR
    has_agg = any(item[0] == "agg" for item in columns)

    if group_by and is_star:
        raise ValueError("SELECT * is not allowed with GROUP BY")

    filtered = source.take(filter_rows(query.where, source))

    if group_by or has_agg:
        result = _grouped(filtered, columns, group_by)
    else:
        result = _projected(filtered, columns)

    return _limited(_ordered(result, list(query.order_by or [])), limit)
