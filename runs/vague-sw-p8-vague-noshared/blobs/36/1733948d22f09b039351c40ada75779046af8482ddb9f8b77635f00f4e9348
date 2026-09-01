Implement exactly one file of the `minidb` system: `minidb/api.py` (query() entry point).

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
    "def query(sql: str, tables: dict[str, list[dict]]) -> list[dict] - THE ONLY public entry point; tokenizes, parses, plans/validates and evaluates `sql` against `tables`, returning the result rows as a list of dicts (one per output row, in result order, keys are the output column names per the spec's Output naming rules). Raises minidb.errors.QueryError for any malformed query, unknown table, unknown column, ambiguous unqualified column, type error, mixed-type ORDER BY comparison, or negative LIMIT/OFFSET. It never lets KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, ValueError, RecursionError or any other non-QueryError exception escape: such an escape is caught and re-raised as QueryError."
  ],
  "module": "api",
  "notes": "SIGNATURE IS FIXED BY THE SPECIFICATION and is exactly `def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]`. `minidb/__init__.py` is written by the harness and re-exports `query` from this module and `QueryError` from `minidb/errors.py`, so api must expose the module-level name `query` with exactly that spelling. `query` is the single public name of this module; everything else in api.py is private (leading underscore) and must not be depended upon.\n\nWHAT api DOES AND DOES NOT DO. api.py is a thin orchestrator only. It contains no lexing, parsing, planning, or evaluation logic. Its whole body is: (1) shallowly validate the two arguments, (2) call tokens -> parser -> planner -> engine in that order, (3) return the engine's rows unchanged, (4) guarantee the error contract. Any behaviour question about SQL surface, NULL three-valued logic, aggregates, ordering, joins, DISTINCT, LIMIT/OFFSET or output naming is owned by parser/planner/engine, not by api; api adds nothing to and removes nothing from what engine returns.\n\nARGUMENT VALIDATION api PERFORMS BEFORE DELEGATING. `sql` must be a str, else QueryError. `tables` must be a dict whose keys are str and whose values are lists of dicts with str keys, else QueryError. api does NOT copy, normalize, sort, or mutate `tables`; it is passed through to the engine as-is, and api never mutates the caller's rows. Column order for a table is the key order of its first row, i.e. the insertion order of the dicts the caller handed in, so api must not rebuild those dicts.\n\nRETURN-VALUE OWNERSHIP. The list and the row dicts returned by api are freshly built by the engine and are safe for the caller to mutate; they are never aliases of the input rows.\n\nERROR CONTRACT (the invariant every dependent module can rely on). The only exception type that can leave `query` is QueryError, the single error class defined in `minidb/errors.py` and re-exported as `minidb.QueryError`. Dependent modules are therefore free to raise QueryError with a human-readable message at the point of detection and let it propagate: api passes QueryError through unchanged, preserving its message, and does not wrap, rename, or reword it. Dependent modules should still prefer raising QueryError themselves rather than relying on api's catch-all, because the catch-all produces a generic message that loses the specific cause. The catch-all is only a last-resort safety net for unexpected internal bugs; it attaches the original exception as the QueryError's `__cause__` so debugging information is not lost. BaseException subclasses that are not Exception (KeyboardInterrupt, SystemExit) are not caught and propagate normally.\n\nSTATELESSNESS AND REENTRANCY. `query` is a pure function with no module-level mutable state, no caching, no global configuration, and no logging side effects. Repeated calls with equal inputs return equal results, and concurrent calls from multiple threads are safe provided the caller does not mutate `tables` during the call.\n\nIMPORTS AND EXTENSIBILITY. Per the module layout, api may import errors, tokens, nodes, parser, functions, planner and engine, and it imports them at module top level (no inline imports). There is no configuration object, no options parameter, and no second or alternate entry point. If a dependent module needs something more, it must expose that from its own module rather than expecting api to grow another export."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


Return ONLY a JSON object:
{"path": "minidb/api.py", "code": "<complete file contents>",
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

Each module is owned by exactly one implementer. Do not create files you do not own,
and do not modify files you do not own.

| module | responsibility | may import |
| --- | --- | --- |
| `minidb/errors.py` | the error type this system raises | stdlib only |
| `minidb/tokens.py` | turning SQL text into a sequence of tokens | `errors` |
| `minidb/nodes.py` | the abstract syntax tree representation | stdlib only |
| `minidb/parser.py` | turning tokens into a syntax tree | `tokens`, `nodes`, `errors` |
| `minidb/functions.py` | scalar and aggregate function behaviour, pattern matching, value comparison | `errors` |
| `minidb/planner.py` | name resolution, aggregate classification, validation | `nodes`, `errors` |
| `minidb/engine.py` | evaluating a validated query against tables | `planner`, `functions`, `errors` |
| `minidb/api.py` | the public `query` entry point that ties the above together | all of the above |

`minidb/__init__.py` is provided by the harness and re-exports `query` and
`QueryError`; do not write it.

The exported names and signatures of each module are **not specified here**. They are
yours to choose and to publish, and the modules that depend on you must be written
against what you publish. Do not guess at another module's interface: use the
interface that module has published, and if it is insufficient, work within it and say
so rather than inventing a different signature for someone else's module.

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