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
    "Row: TypeAlias = dict[str, Any] - one input or output row; keys are column/output names, values are Python values, SQL NULL is None.",
    "Tables: TypeAlias = dict[str, list[Row]] - the caller's table store, exactly the `tables` argument of the public `query()`; raises nothing (type alias only).",
    "def execute(plan: Any, tables: Tables) -> list[Row] - evaluates one validated query plan (the object returned by `planner`'s published plan-producing entry point) against `tables` and returns the result rows as a new list of new dicts, in result order, with keys set by the spec's Output naming rules. Performs the full evaluation pipeline: FROM/INNER JOIN row combination, WHERE filtering, GROUP BY grouping, aggregate computation, HAVING filtering, select-list projection, DISTINCT, ORDER BY, then OFFSET and LIMIT. Raises minidb.errors.QueryError for every failure, including unknown table name in `tables`, unknown or ambiguous column reference surviving into evaluation, mixed-type comparison in ORDER BY or in a comparison operator, non-numeric operand to arithmetic, bad argument type to a scalar or aggregate function, and negative LIMIT/OFFSET. Never raises KeyError, IndexError, AttributeError, TypeError, ValueError, ZeroDivisionError or StopIteration out of `execute`."
  ],
  "module": "engine",
  "notes": "Contract for dependents (`api` is the only one):\n1. `engine` does no lexing, parsing or validation. `api` must call `tokens` -> `parser` -> `planner` first and hand the resulting validated plan to `execute(plan, tables)` unchanged. `execute` treats `plan` as opaque and reads only what `planner` publishes on it; it deliberately does not re-specify planner's plan type, so a change in planner's plan shape needs no change to this signature.\n2. `execute` does not mutate `tables`, the row dicts inside it, or `plan`. Every returned row is a freshly built dict, so callers may mutate results freely. Values are returned by reference (no deep copy).\n3. Row shape: a table's column order is the key order of its first row (as the spec states); a later row missing that key is evaluated as NULL rather than an error, and extra keys in later rows are ignored for `*` expansion. An empty table has no columns.\n4. NULL is Python `None`. Three-valued logic is applied in WHERE/HAVING/ON: a row is kept only when the condition evaluates true, never when it is unknown. Arithmetic with NULL yields NULL; division (`/`) by zero yields NULL and does not raise.\n5. Aggregates: `COUNT(*)` counts rows, `COUNT(x)` counts non-NULL values and returns 0 over no input; SUM/AVG/MIN/MAX ignore NULLs and return NULL when there is no non-NULL input; AVG returns a float. An aggregate select list with no GROUP BY always yields exactly one row, even for zero input rows. With GROUP BY, one row per distinct group key in first-appearance order of the groups.\n6. ORDER BY is stable, NULL sorts before non-NULL for ASC and after for DESC, and an output alias may be used as a sort key. DISTINCT is applied to whole output rows after projection and before ORDER BY/OFFSET/LIMIT; unhashable values are compared structurally rather than raising.\n7. `engine` delegates value comparison, LIKE pattern matching, and scalar/aggregate function behaviour to `functions`, and name resolution/aggregate classification to `planner`; it re-raises their QueryErrors unchanged and adds no new error type. `engine` defines no exception class of its own.\n8. `execute` is pure and stateless: no module-level mutable state, no caching, so it is safe to call concurrently and repeatedly for the same plan."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


FAILING ACCEPTANCE CASES ATTRIBUTED TO YOUR MODULE (round 2). Fix these; they are the definition of done:
- order_desc: SELECT a FROM t ORDER BY a DESC
    expected [{'a': 4}, {'a': 3}, {'a': 2}, {'a': 1}], got [{'a': 1}, {'a': 2}, {'a': 3}, {'a': 4}]
- order_nulls_last_desc: SELECT a FROM t ORDER BY c DESC
    expected [{'a': 2}, {'a': 1}, {'a': 4}, {'a': 3}], got [{'a': 3}, {'a': 4}, {'a': 1}, {'a': 2}]
- order_by_alias: SELECT a AS z FROM t ORDER BY z DESC LIMIT 2
    expected [{'z': 4}, {'z': 3}], got [{'z': 1}, {'z': 2}]
- join_group: SELECT c.name, SUM(o.qty) AS total FROM orders o JOIN customers c ON o.cid = c.cid GROUP BY c.name ORDER BY total DESC
    expected [{'name': 'ada', 'total': 10}, {'name': 'bob', 'total': 5}], got [{'name': 'bob', 'total': 5}, {'name': 'ada', 'total': 10}]
- empty_table_select: SELECT * FROM e
    QueryError: query has no output columns
    frames: File "/workspace/runs/vague-sw-p8-vague-noshared/workspace/round1/minidb/api.py", line 94, in _invoke | File "/workspace/runs/vague-sw-p8-vague-noshared/workspace/round1/minidb/engine.py", line 99, in execute | File "/workspace/runs/vague-sw-p8-vague-noshared/workspace/round1/minidb/engine.py", line 112, in _execute

PEER REVIEW OF YOUR MODULE:
- engine.py: it re-implements comparison, ordering, LIKE and the scalar/aggregate functions itself, but the spec assigns those to functions.py and lets engine import it, so two independent copies of NULL/mixed-type/LIKE semantics will disagree -> import functions and use its published SCALARS/AGGREGATES/like_match/compare instead of local copies.
- engine.py: the plan is read by guessing attribute names (_pick over ('select','select_items','projections',...), ('from_tables','tables','sources',...)) instead of the planner's published names; any planner field named differently is silently read as absent, and a Plan field literally called 'tables' (alias->columns map) would be consumed as the FROM list -> read the plan through planner's published interface, and raise QueryError when a required field is missing rather than defaulting.
- engine.py: node dispatch goes through the fixed _KIND_BY_CLASS spelling table, so any planner node class whose name is not listed (e.g. AggregateExpr, ColumnRef2, ArithOp) makes a legal query fail at evaluation -> dispatch on the planner's actual node types, not on lower-cased class-name guesses.
- engine.py: `if not select: raise QueryError('query has no output columns')` breaks `SELECT * FROM e` when e has no rows: `*` legitimately expands to zero columns there and the correct result is [] -> only raise when the parsed select list itself was empty; an empty star expansion must yield [] (or [{}] per row).
- engine.py: in _scan, a join whose ON condition was not found (`condition is None`) is evaluated as an unconstrained cross join, so a misread or missing ON silently returns wrong rows instead of failing -> treat a missing ON for a joined source as QueryError('join has no ON condition').
- engine.py: _pick's non-dict branch returns the first name whose getattr() is not None, so it cannot distinguish 'field absent' from 'field explicitly None' across the candidate list; with a plan object exposing both `where` and `where_clause` it can pick the wrong one -> look up one canonical published name per clause.
- engine.py: `_execute` raises QueryError('query has no output columns') whenever `plan.select` is empty, but `SELECT * FROM t` against a table with no rows legitimately expands to zero SelectItems (planner's `_schema_of` returns None for an empty table, so star expansion emits nothing). A valid query then errors instead of returning []. -> On empty `select`, return [] (or the single all-aggregate row when the plan is aggregate) instead of raising.
- engine.py: HAVING is only evaluated on the grouped path. `grouped` is bool(group_by) or bool(aggregates) or _uses_aggregate(select, having, order_by), so a HAVING containing no aggregate and with no GROUP BY (e.g. `HAVING 1 = 2`, which the planner accepts because it references no column) is silently dropped and every row is returned. -> Add `or having is not None` to the `grouped` predicate, or apply the HAVING filter in the ungrouped branch as well.
- engine.py: the module re-implements value comparison, NULL ordering, LIKE matching and the scalar/aggregate functions itself rather than importing `minidb.functions`, which the module layout makes the owner of exactly those behaviours. Two independent implementations will diverge on mixed-type comparison (QueryError), NULL sort position, AVG returning float, and LENGTH/ABS argument type errors, and functions.py becomes dead code that nothing exercises. -> `from .functions import ...` and delegate comparison, LIKE and function evaluation; keep only row combination, grouping, projection and slicing in engine.
- engine.py: `_group` with no GROUP BY uses `rows[0] if rows else {}` as the scope row, so any non-aggregate expression in a bare-aggregate query silently reads the first input row, and over zero rows the empty representative makes every alias lookup miss. -> Evaluate only aggregate placeholders and literals in that scope and raise QueryError if a Column is reached, so a planner gap surfaces as QueryError rather than as an arbitrary first-row value.

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