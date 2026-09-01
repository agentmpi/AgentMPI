Implement exactly one file of the `minidb` system: `minidb/errors.py` (QueryError only).

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
    "QueryError"
  ],
  "module": "errors",
  "notes": "minidb/errors.py contains QueryError and nothing else; it imports only the standard library and has no dependencies on other minidb modules, so every module may import it freely without cycles. Import it as `from minidb.errors import QueryError`. QueryError derives directly from Exception (not from ValueError or RuntimeError), so `except QueryError` catches only minidb failures, and `except Exception` in api.query must convert any other escaping exception (KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, etc.) into QueryError before it leaves the public API. Construct it with a single descriptive message string; the message text is not part of the contract and must not be parsed by callers, who should branch only on the exception type. There is no error-code enum, no cause chaining requirement, and no factory helper: dependent modules simply `raise QueryError(<message>)` at the point of detection, optionally with `from exc` to preserve context. It is a plain Exception, so it can be raised, caught, re-raised, and pickled normally."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/parser.py: imports `Expr` from `.nodes`, but the published nodes.py surface is only Select, TableRef, Join, Column, Literal, BinOp, UnaryOp, Func, Star, OrderKey, SelectItem — if nodes.py does not define an `Expr` alias the whole package fails at import time with ImportError -> drop the `Expr` import and annotate with `object`, or have nodes.py publish `Expr` explicitly.
- minidb/planner.py: `alias = ref.ref_name()` calls a method on nodes.TableRef that nodes.py never published (it published a dataclass with fields only); this raises AttributeError, not QueryError, at plan time -> use the published fields (`ref.alias or ref.name`), or get `ref_name()` added to the published nodes interface.
- minidb/planner.py: `Func` classification uses `name = expr.name` and looks it up in AGGREGATE_ARITY/SCALAR_ARITY, which hold upper-case keys only, so `sum(a)` or `Count(*)` is rejected as an unknown function even though keywords are case-insensitive; engine.py uppercases the name before its SCALARS/AGGREGATES lookup, so the two modules disagree about the same node -> uppercase once in the planner (`name = expr.name.upper()`) and use the upper-case name for arity checks and aggregate classification.
- minidb/planner.py: `validate` accepts `BinOp.right` as a tuple of operands (the IN list), but neither the planner nor the engine interface says a BinOp right operand can be a tuple; an engine that assumes a single node would evaluate the list as one expression -> state in the planner interface that `BinOp.right` is a tuple of expressions for `IN`/`NOT IN` (engine.py already evaluates both shapes).
- minidb/planner.py: `build_sources` records `self.known[alias]` from the union of keys over all rows while `self.columns[alias]` (used for `*` expansion) comes from the first row only, so a key that appears only in a later row resolves as a column but never appears in `SELECT *` output -> derive both from the first row, per the spec rule that a table's columns are the key order of its first row.
- minidb/planner.py: `if name not in self.tables` and `self.tables[name]` assume `tables` is a dict; a non-mapping `tables` argument raises TypeError out of plan() -> check `isinstance(tables, dict)` first and raise QueryError.
- minidb/planner.py: for a table with zero rows the alias goes into `open_schema` and any column reference against it silently binds and evaluates to NULL, so a genuinely unknown column is not reported as QueryError; with two or more empty tables an unknown unqualified name is reported as 'ambiguous column' instead of 'unknown column' -> keep the permissive binding (there is no schema without rows) but report unknown/ambiguous consistently, since the spec requires unknown columns to be QueryError.
- minidb/parser.py: `source_text = self._sql[start:self._source_pos()]` is the raw slice up to the next token, so it keeps interior and trailing whitespace (`price * 2 `); output naming requires the source text with all whitespace removed -> strip all whitespace (`''.join(source_text.split())`) where the output name is computed, i.e. in the planner's naming step, and never only `.strip()`.
- minidb/planner.py: published exports now include SCALAR_ARITY and AGGREGATE_ARITY and `PlannedOrderKey.descending`/`output_index` gained defaults, none of which were in the interface the engine was written against; harmless for engine.py, which only reads the documented fields, but the interface should be re-published so the drift is visible.
- minidb/planner.py: `build_sources` reads `self.select.from_table`, but parser publishes the field as `Select.from_` (Select(distinct, items, from_, joins, where, group_by, having, order_by, limit, offset)). Every query dies with AttributeError before any QueryError can be raised -> use `self.select.from_`.

Return ONLY a JSON object:
{"path": "minidb/errors.py", "code": "<complete file contents>",
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