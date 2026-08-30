"""CSV loading and serialisation for tinyq."""

from __future__ import annotations

import csv
import io

from tinyq.schema import Column, Table, coerce, infer_kind


def load_csv(text: str) -> Table:
    """Parse CSV text with a header row into a Table.

    Cells are converted with schema.coerce and each column's kind comes from
    schema.infer_kind.  Empty input (no header) yields Table([]).
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        return Table([])

    names = [name.strip() for name in header]
    values: list[list] = [[] for _ in names]
    for row in reader:
        for i in range(len(names)):
            cell = row[i] if i < len(row) else ""
            values[i].append(coerce(cell))

    return Table([Column(name, infer_kind(vals), vals)
                  for name, vals in zip(names, values)])


def dump_csv(table: Table) -> str:
    """Serialise a Table back to CSV text with a header row.

    Lines are joined with '\\n' and there is no trailing blank line.  None
    becomes an empty field and booleans become 'true'/'false'.
    """
    columns = table.columns
    if not columns:
        return ""

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([column.name for column in columns])
    for i in range(table.nrows()):
        writer.writerow([_format(column.values[i]) for column in columns])

    out = buf.getvalue()
    if out.endswith("\n"):
        out = out[:-1]
    return out


def _format(value) -> str:
    if value is None:
        return ""
    # bool before int: bool is a subclass of int.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
