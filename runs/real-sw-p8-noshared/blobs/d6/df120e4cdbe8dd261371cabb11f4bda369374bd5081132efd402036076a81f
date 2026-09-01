Implement exactly one file of the `minidb` system: `minidb/engine.py` (execute() -> rows).

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
    "execute(plan: Plan, tables: dict[str, list[dict]]) -> list[dict] - Runs a planned query: materialises the FROM/JOIN row space, applies ON and WHERE, grouping and aggregation, HAVING, the select list, DISTINCT, ORDER BY, then OFFSET and LIMIT, and returns the result rows as a list of dicts keyed by the output column names the planner computed, in result order. Raises minidb.errors.QueryError for any runtime failure (a table named in the plan missing from `tables`, a mixed-type comparison in ORDER BY or in a condition, a value whose type a scalar or aggregate function cannot accept, a negative LIMIT/OFFSET); it never lets KeyError, IndexError, AttributeError, or TypeError escape."
  ],
  "module": "engine",
  "notes": "Argument shapes: `plan` is exactly the object returned by planner.plan(select, tables) and is consumed only through the attributes the planner publishes; engine does no parsing and re-does no name resolution, aggregate classification, `*` expansion, or output naming of its own. `tables` is the same mapping the public API received: table name -> list of rows, each row a dict from column name to value; column order for a table is the key order of its first row, and a table with zero rows contributes no columns.\n\nReturn shape: a new list of new dicts, one per result row. Keys are the planner's output names (a bare `col` or `t.col` is named `col`; an aliased expression is named by its alias; any other expression is named by its source text with all whitespace removed). Every returned row carries the same keys in the same order, namely select-list order, and for `*` that is FROM/JOIN order and each table's own column order with a later table's column winning a name collision. Rows come back ordered as ORDER BY dictates (stable, NULL before non-NULL for ASC and after for DESC, output aliases resolvable) and truncated by OFFSET then LIMIT after ordering.\n\nInvariants: execute() is pure with respect to its inputs. It never mutates `tables`, the row dicts inside it, or `plan`, and no result row aliases an input row dict. It holds no module-level state and is re-entrant, so the same plan may be executed repeatedly and concurrently.\n\nValue semantics: SQL NULL is Python None. Three-valued logic throughout, so WHERE/HAVING/ON keep a row only when the condition evaluates to true (NULL AND false is false, NULL OR true is true, everything else unknown is not true). Arithmetic involving NULL yields NULL and division by zero yields NULL rather than raising. Aggregates: COUNT(*) counts rows, COUNT(x) counts non-NULL values and yields 0 over an empty group, SUM/AVG/MIN/MAX ignore NULLs and yield NULL when there is no non-NULL input, AVG yields a float. An aggregate select list with no GROUP BY produces exactly one row even over zero input rows; with GROUP BY, one row per distinct group key in first-appearance order of the group key. DISTINCT is applied to the whole computed output row, after the select list is computed and before ORDER BY. Comparison, LIKE matching (% for any run of characters, _ for exactly one), and scalar/aggregate value computation are delegated to functions.compare, functions.like_match, functions.SCALARS and functions.AGGREGATES rather than reimplemented here; NULL placement in ORDER BY is the engine's own responsibility because compare() reports unknown for NULL operands.\n\nError behaviour: QueryError from minidb.errors is the only exception type that leaves execute(); every other exception raised inside is caught and re-raised as QueryError with the original attached as __cause__. api.py therefore needs no engine-specific translation, only its own catch-all wrapping, and callers should branch on the exception type and never on the message text."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


Return ONLY a JSON object:
{"path": "minidb/engine.py", "code": "<complete file contents>",
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