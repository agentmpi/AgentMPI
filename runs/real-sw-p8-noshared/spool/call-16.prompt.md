Implement exactly one file of the `minidb` system: `minidb/planner.py` (plan() -> Plan).

Rules:
- Pure Python 3.11+, standard library only.
- Write ONLY this file. Do not write, mention, or assume the contents of files you do
  not own beyond the published interfaces given below.
- Import from sibling modules using relative imports, e.g. `from .errors import QueryError`.
- Use the published interfaces below EXACTLY as given. If a dependency's published
  interface is insufficient, work within it anyway and say so in "concerns"; do not
  invent a different signature for someone else's module.
- The code must be complete and syntactically valid. No placeholders, no `pass` bodies,
  no `TODO`, no `NotImplementedError`.


THE INTERFACE YOU PUBLISHED (you must honour it; other modules were written against it):
{
  "exports": [
    "@dataclass class PlannedItem: name: str; expr: Expr - one computed output column; `name` is the final output key already resolved by the Output naming rules (bare column -> the bare column name, aliased expression -> the alias, other expression -> its source text with all whitespace removed), `expr` is a nodes.py expression node and is never a Star; constructing it raises nothing.",
    "@dataclass class PlannedOrderKey: expr: Expr; descending: bool = False; output_index: int | None = None - one ORDER BY key; when `output_index` is not None the key is the already-computed value of `plan.select_items[output_index]` (ORDER BY referencing an output name/alias), otherwise `expr` is evaluated against the source row; constructing it raises nothing.",
    "@dataclass class Plan: sources: list[tuple[str, str]]; join_conditions: list[Expr]; where: Expr | None; group_by: list[Expr]; having: Expr | None; select_items: list[PlannedItem]; distinct: bool; order_by: list[PlannedOrderKey]; limit: int | None; offset: int | None; is_aggregate: bool; aggregate_calls: list[Func]; column_bindings: dict[tuple[str | None, str], tuple[str, str]] - the fully validated, name-resolved description of one SELECT statement; it is produced only by plan(), must be treated as read-only by consumers, and constructing it raises nothing.",
    "def plan(select: Select, tables: dict[str, list[dict]]) -> Plan - resolves every table and column reference, expands `*` and `table.*`, computes output names, classifies the statement as aggregate or row-wise and validates it; raises minidb.errors.QueryError for an unknown table, an unknown column, an ambiguous unqualified column across joined tables, a duplicate table alias, an unknown function name, a wrong argument count, `*` used anywhere but a select item or the single argument of COUNT, an aggregate in WHERE or in an ON condition, a nested aggregate, a non-aggregate select/HAVING/ORDER BY expression that is not one of the GROUP BY expressions when the query is aggregated, an ORDER BY reference that names neither an output column nor a table column, or a negative LIMIT/OFFSET; it never lets KeyError, IndexError, AttributeError or TypeError escape.",
    "def resolve_column(plan: Plan, qualifier: str | None, name: str) -> tuple[str, str] - maps a column reference as written in the query (`qualifier` is the written table/alias or None) to the `(source_alias, column_name)` it was bound to at plan time, reading `plan.column_bindings`; raises minidb.errors.QueryError when the reference is unknown or was ambiguous, which cannot happen for references taken from the planned AST since those were validated by plan().",
    "SCALAR_ARITY: dict[str, tuple[int, int | None]] - the accepted scalar functions and their (minimum, maximum) argument counts: UPPER/LOWER/LENGTH/ABS = (1, 1), COALESCE = (1, None); a plain data dict that raises nothing.",
    "AGGREGATE_ARITY: dict[str, tuple[int, int | None]] - the accepted aggregate functions and their (minimum, maximum) argument counts: COUNT/SUM/AVG/MIN/MAX = (1, 1), where COUNT alone may take Star as its argument; a plain data dict that raises nothing."
  ],
  "module": "planner",
  "notes": "Evaluation environment: `plan.sources` lists `(table_name, alias)` in FROM then JOIN order; `alias` is the written alias or the table name, and aliases are unique. The engine builds a joined row as `{alias: row_dict}` and evaluates a Column by calling `resolve_column(plan, column.table, column.name)` and reading `env[source_alias].get(column_name)`; a key missing from a particular row dict is NULL (None), never an error. `plan.join_conditions` has length `len(plan.sources) - 1` and `join_conditions[i]` is the ON condition for `sources[i + 1]`; INNER JOIN only, so a joined row survives only when the condition is true (unknown counts as not true). Star expansion is already done: `plan.select_items` contains no Star, is in FROM/JOIN order and within a table in that table's column order (the key order of that table's first row; an empty table contributes no columns), and on a name collision the later table's column wins, meaning the colliding earlier item has been removed from the list. COUNT(*) reaches the engine as Func('COUNT', (Star(),)) and must count rows, including rows that are entirely NULL; every other aggregate ignores NULLs. `plan.is_aggregate` is True when the statement has any aggregate call, a GROUP BY, or a HAVING; when it is True and `plan.group_by` is empty the engine emits exactly one row even over zero input rows. `plan.group_by` holds the grouping expressions in written order, to be evaluated per row to form the group key; `plan.having` is evaluated once per group and keeps the group only when true. `plan.aggregate_calls` lists every aggregate Func node found in the select list, then HAVING, then ORDER BY, deduplicated by node identity, so the engine may pre-compute them per group and match by `id()` or simply evaluate them in place. Execution order after grouping: compute the select list, apply DISTINCT to whole output rows, then the stable ORDER BY, then OFFSET, then LIMIT. `plan.limit`/`plan.offset` are non-negative ints or None (negatives already rejected), and `plan.distinct` is a bool. NULL is Python None throughout. The planner evaluates no expressions and imports only nodes and errors: it never imports functions.py, so function names and arity are checked against SCALAR_ARITY/AGGREGATE_ARITY above. It reads the first row of each table for column order and the remaining rows' keys only to validate references against ragged data; an empty table has no discoverable schema, so a reference qualified to it is accepted, and an unqualified name that matches no known column binds to the single empty table when exactly one exists and is otherwise reported ambiguous. An unqualified ORDER BY name is matched against select-list output names first (alias precedence) and only then resolved as a table column. Every failure is a minidb.errors.QueryError."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


Return ONLY a JSON object:
{"path": "minidb/planner.py", "code": "<complete file contents>",
  "exports": ["<name>", ...], "concerns": ["<optional>", ...]}

--- SPECIFICATION ---
# minidb specification

`minidb` is a small SQL query engine over in-memory tables. It is implemented in
pure Python 3.11+ with **no third-party dependencies** and no imports outside the
standard library.

This document is the complete and only specification. It is broadcast unchanged to
every implementer; there is no other source of truth, and nothing here may be
renegotiated.

## Public API

Exactly one public entry point, in `minidb/api.py`, re-exported from
`minidb/__init__.py`:

```python
def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]:
    """Execute `sql` against `tables` and return the result rows."""
```

* `tables` maps a table name to a list of rows; each row is a dict from column
  name to value. Column order for a table is the key order of its first row.
* The return value is a list of dicts, one per result row, in result order. Keys
  are the output column names (see *Output naming*).
* Any malformed query, unknown table, unknown column, or type error must raise
  `minidb.QueryError` (defined in `minidb/errors.py`). It must never raise
  `KeyError`, `IndexError`, `AttributeError`, or `TypeError` out of the public API.

## SQL surface

Keywords are case-insensitive. Identifiers are case-sensitive. String literals use
single quotes with `''` as the escape for a literal quote. Numbers are integers or
floats. `NULL` is a literal.

```
SELECT [DISTINCT] select_list
FROM table [alias]
  [ [INNER] JOIN table [alias] ON condition ]*
[WHERE condition]
[GROUP BY expr [, expr]*]
[HAVING condition]
[ORDER BY expr [ASC|DESC] [, expr [ASC|DESC]]*]
[LIMIT n] [OFFSET n]
```

* `select_list` is `*`, or a comma-separated list of `expr [[AS] alias]`.
  `table.*` is also permitted.
* `expr` supports: column references (`col` or `table.col` or `alias.col`),
  literals, `+ - * /`, parentheses, the scalar functions `UPPER(x)`, `LOWER(x)`,
  `LENGTH(x)`, `ABS(x)`, `COALESCE(a, b, ...)`, and the aggregate functions
  `COUNT(*)`, `COUNT(x)`, `SUM(x)`, `AVG(x)`, `MIN(x)`, `MAX(x)`.
* `condition` supports: comparisons `= <> != < <= > >=`, `AND`, `OR`, `NOT`,
  parentheses, `x IS NULL`, `x IS NOT NULL`, `x IN (a, b, ...)`,
  `x LIKE 'pat'` where `%` matches any run of characters and `_` matches one.
* Division by zero yields `NULL`, it does not raise.

## Semantics

**NULL.** Three-valued logic. Any comparison with `NULL` yields unknown, and a
`WHERE`/`HAVING`/`ON` clause keeps a row only when its condition is true.
`NULL AND false` is false; `NULL OR true` is true. Arithmetic involving `NULL`
yields `NULL`.

**Aggregates.** `COUNT(*)` counts rows; `COUNT(x)` counts non-NULL values. `SUM`,
`AVG`, `MIN`, `MAX` ignore NULLs and return `NULL` when there is no non-NULL input,
except `COUNT`, which returns `0`. `AVG` returns a float. A query with an aggregate
in the select list and no `GROUP BY` produces exactly one row, even over zero input
rows. With `GROUP BY`, one row per distinct group key, and a non-aggregate select
expression must be one of the grouping expressions.

**ORDER BY.** Stable. `NULL` sorts before all non-NULL values in `ASC` and after
them in `DESC`. Mixed-type comparison is a `QueryError`. `ORDER BY` may reference an
output alias.

**JOIN.** `INNER JOIN` only. During a join, a bare column name that is ambiguous
across the joined tables is a `QueryError`; qualify it. An unqualified name that is
unambiguous resolves without qualification.

**DISTINCT** applies to the whole output row, after the select list is computed.

**LIMIT / OFFSET.** Applied after ordering. Negative values are a `QueryError`.

## Output naming

* A bare column `col` or `t.col` is named `col`.
* An aliased expression is named by its alias.
* An unaliased non-column expression is named by the source text of the
  expression with all whitespace removed: `SUM(qty)` becomes `SUM(qty)`, and
  `price * 2` becomes `price*2`.
* `*` expands to every column of every table in `FROM`/`JOIN` order, and within a
  table in that table's column order. On a name collision, the later table's
  column wins.

## Module layout

Each module is owned by exactly one implementer. Do not create files you do not
own, and do not modify files you do not own.

| module | responsibility | may import |
| --- | --- | --- |
| `minidb/errors.py` | `QueryError(Exception)` and nothing else | stdlib only |
| `minidb/tokens.py` | `Token` (dataclass: `kind`, `value`, `pos`) and `tokenize(sql) -> list[Token]` | `errors` |
| `minidb/nodes.py` | AST dataclasses: `Select`, `TableRef`, `Join`, `Column`, `Literal`, `BinOp`, `UnaryOp`, `Func`, `Star`, `OrderKey`, `SelectItem` | stdlib only |
| `minidb/parser.py` | `parse(sql) -> Select` | `tokens`, `nodes`, `errors` |
| `minidb/functions.py` | `SCALARS`, `AGGREGATES` dicts and `like_match(value, pattern)`, `compare(a, b)` | `errors` |
| `minidb/planner.py` | `plan(select, tables) -> Plan`: resolve names, classify aggregates, validate | `nodes`, `errors` |
| `minidb/engine.py` | `execute(plan, tables) -> list[dict]` | `planner`, `functions`, `errors` |
| `minidb/api.py` | `query(sql, tables)`: parse, plan, execute; wrap unexpected exceptions in `QueryError` | all of the above |

`minidb/__init__.py` is provided by the harness and re-exports `query` and
`QueryError`; do not write it.

Module boundaries are contracts. If your module needs something from another
module, use the interface that module has published; do not reach around it and do
not duplicate its logic.

## Examples

```python
tables = {"t": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]}

query("SELECT a FROM t WHERE a > 1", tables)
# [{"a": 2}]

query("SELECT COUNT(*) AS n, SUM(a) FROM t", tables)
# [{"n": 2, "SUM(a)": 3}]

query("SELECT b, COUNT(*) AS n FROM t GROUP BY b ORDER BY b DESC", tables)
# [{"b": "y", "n": 1}, {"b": "x", "n": 1}]
```

--- END SPECIFICATION ---