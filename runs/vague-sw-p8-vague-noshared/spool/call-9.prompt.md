Implement exactly one file of the `minidb` system: `minidb/functions.py` (SCALARS, AGGREGATES, like_match, compare).

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
    "SCALARS: dict[str, Callable[..., object]] - maps the upper-case scalar function name to a callable that takes ALREADY-EVALUATED argument values (Python objects, None for NULL) and returns the result value. Keys are exactly \"UPPER\", \"LOWER\", \"LENGTH\", \"ABS\", \"COALESCE\". Each callable raises QueryError on wrong argument count or wrong argument type (e.g. UPPER of a number, ABS of a string); no other exception escapes.",
    "SCALARS[\"UPPER\"](value: object) -> str | None - upper-cases a string; returns None if value is None; raises QueryError if value is not a str and not None.",
    "SCALARS[\"LOWER\"](value: object) -> str | None - lower-cases a string; returns None if value is None; raises QueryError if value is not a str and not None.",
    "SCALARS[\"LENGTH\"](value: object) -> int | None - character length of a string; returns None if value is None; raises QueryError if value is not a str and not None.",
    "SCALARS[\"ABS\"](value: object) -> int | float | None - absolute value of an int or float; returns None if value is None; raises QueryError if value is not an int/float (bool is rejected too) and not None.",
    "SCALARS[\"COALESCE\"](*values: object) -> object - returns the first argument that is not None, else None; raises QueryError if called with zero arguments.",
    "AGGREGATES: dict[str, Callable[[Sequence[object]], object]] - maps the upper-case aggregate name to a callable that takes the full sequence of per-row input values for one group (Nones included, in row order) and returns the aggregate value. Keys are exactly \"COUNT\", \"SUM\", \"AVG\", \"MIN\", \"MAX\". Each callable raises QueryError on an unusable input type; it never raises TypeError/KeyError.",
    "AGGREGATES[\"COUNT\"](values: Sequence[object]) -> int - number of values that are not None; returns 0 for an empty sequence; never returns None. For COUNT(*) the caller passes one non-None placeholder per row (e.g. [1] * len(rows)).",
    "AGGREGATES[\"SUM\"](values: Sequence[object]) -> int | float | None - sum of the non-None values, ignoring Nones; returns None when there is no non-None input; int when every summed value is an int, else float; raises QueryError if any non-None value is not an int/float (bool is rejected).",
    "AGGREGATES[\"AVG\"](values: Sequence[object]) -> float | None - arithmetic mean of the non-None values; always a float when defined; returns None when there is no non-None input; raises QueryError if any non-None value is not an int/float.",
    "AGGREGATES[\"MIN\"](values: Sequence[object]) -> object - smallest non-None value under order_cmp ordering; returns None when there is no non-None input; raises QueryError if the non-None values are of mixed comparable kinds (e.g. str with number).",
    "AGGREGATES[\"MAX\"](values: Sequence[object]) -> object - largest non-None value under order_cmp ordering; returns None when there is no non-None input; raises QueryError on mixed-type input.",
    "like_match(value: object, pattern: object) -> bool | None - SQL LIKE: '%' matches any run of characters (including none), '_' matches exactly one character, every other character matches itself literally, and the match is anchored to the whole string; returns None (unknown) if value or pattern is None; raises QueryError if a non-None value or pattern is not a str.",
    "compare(op: str, left: object, right: object) -> bool | None - three-valued comparison for op in {'=', '<>', '!=', '<', '<=', '>', '>='}; returns None (unknown) if left or right is None; ints and floats compare numerically with each other, str compares with str, bool compares only with bool; raises QueryError for an unknown op and for a mixed-type comparison (e.g. 1 < 'a').",
    "order_cmp(left: object, right: object) -> int - three-way ORDER BY comparator returning -1, 0 or 1, with None sorting before every non-None value (use it directly via functools.cmp_to_key for ASC and negate/reverse it for DESC, which keeps sorting stable and puts None last in DESC); raises QueryError on a mixed-type comparison of two non-None values."
  ],
  "module": "functions",
  "notes": "Imports errors only (QueryError from minidb.errors); stdlib only. Every failure mode is reported as QueryError with a human-readable message; this module never lets KeyError, IndexError, AttributeError or TypeError escape.\\n\\nValue model: NULL is Python None. Values are int, float, str, bool or None. bool is treated as its own kind: it is not numeric for ABS/SUM/AVG and compares only with bool.\\n\\nDispatch: SCALARS and AGGREGATES are keyed by the UPPER-CASED function name; the caller must upper-case the parsed name before lookup (keywords are case-insensitive). A name absent from the dict is not this module's error to raise: use `name.upper() in SCALARS` / `in AGGREGATES` to classify a call, and let the planner raise QueryError for an unknown function. Both dicts are plain dicts and must be treated as read-only.\\n\\nArgument evaluation is the caller's job: this module never sees rows, tables, column names or AST nodes, only plain values. Arguments are passed positionally, already evaluated; COALESCE is NOT short-circuited here, so the caller evaluates all of its arguments first (safe, since arithmetic errors such as division by zero yield NULL rather than raising).\\n\\nAggregates take one sequence per group covering all rows of that group in row order, Nones included; the callables themselves skip Nones. COUNT is the only aggregate that never returns None. COUNT(*) is expressed by passing a non-None placeholder per row. An aggregate with no GROUP BY over zero rows is a single call with an empty sequence, which yields 0 for COUNT and None for the others.\\n\\nComparison and ordering: use compare() for WHERE/HAVING/ON comparisons and for IN (compare('=', x, element) per element, combining unknowns with three-valued OR in the caller); use order_cmp() for ORDER BY, MIN and MAX. Three-valued logic (AND/OR/NOT), IS NULL / IS NOT NULL, arithmetic and division-by-zero-yields-NULL stay with the caller: this module does not provide them.\\n\\nLIKE has no ESCAPE clause per the specification; a pattern of '%' matches any string including the empty one."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


Return ONLY a JSON object:
{"path": "minidb/functions.py", "code": "<complete file contents>",
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