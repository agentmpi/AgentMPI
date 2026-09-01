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
    "execute(plan: Plan, tables: dict[str, list[dict]]) -> list[dict] - Runs a planned query: materialises the FROM/JOIN row space, applies ON and WHERE, grouping and aggregation, HAVING, the select list, DISTINCT, ORDER BY, then OFFSET and LIMIT, and returns the result rows as a list of dicts keyed by the output column names determined by the planner, in result order. Raises minidb.errors.QueryError for any runtime failure (mixed-type comparison in ORDER BY or a comparison, unknown table name in `tables`, a value type a scalar or aggregate function cannot accept, negative LIMIT/OFFSET); it never lets KeyError, IndexError, AttributeError, or TypeError escape."
  ],
  "module": "engine",
  "notes": "Argument shapes: `plan` is exactly the object returned by planner.plan(select, tables) and is consumed through the attributes planner publishes; engine performs no parsing and re-does no name resolution, aggregate classification, or select-list naming validation of its own. `tables` is the same mapping the public API received: table name -> list of rows, each row a dict of column name to value; column order for a table is the key order of its first row, and a table with zero rows contributes no columns to `*` expansion.\n\nReturn shape: a new list of new dicts, one per result row. Keys are the planner's output names (bare `col` or `t.col` -> `col`; alias if aliased; otherwise the expression's source text with all whitespace removed). Every returned row carries the same keys in the same order, which is select-list order (for `*`, FROM/JOIN order and each table's column order, with a later table's column winning a name collision). Rows are ordered as ORDER BY dictates (stable sort, NULL before non-NULL in ASC and after in DESC, output aliases resolvable) and truncated by OFFSET then LIMIT after ordering.\n\nInvariants: execute() is pure with respect to its inputs - it never mutates `tables`, the rows inside it, or `plan`, and result rows never alias input row dicts. It is re-entrant and holds no module-level state, so the same plan may be executed repeatedly.\n\nValue semantics implemented here: three-valued logic, so WHERE/HAVING/ON keep a row only when the condition is true (NULL AND false is false, NULL OR true is true); arithmetic with NULL yields NULL; division by zero yields NULL rather than raising. Aggregates: COUNT(*) counts rows, COUNT(x) counts non-NULL values and returns 0 with no input, SUM/AVG/MIN/MAX ignore NULLs and return NULL with no non-NULL input, AVG returns a float. An aggregate select list with no GROUP BY yields exactly one row even over zero input rows; with GROUP BY, one row per distinct group key. DISTINCT is applied to the whole computed output row, before ORDER BY. Comparison, LIKE matching (with % and _), and scalar/aggregate evaluation are delegated to functions.compare, functions.like_match, functions.SCALARS, and functions.AGGREGATES rather than reimplemented.\n\nError behaviour: the only exception type that leaves execute() is QueryError, raised from minidb.errors; api.py therefore needs no additional translation for engine failures, only its own catch-all wrapping."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
### errors (minidb/errors.py)
{
  "exports": [
    "class QueryError(Exception) - the single public exception type of minidb; raised for any malformed query, unknown table, unknown column, ambiguous column, mixed-type ordering comparison, negative LIMIT/OFFSET, or type error. It takes the standard Exception arguments (typically one human-readable message string: QueryError('unknown column: x')), adds no extra attributes, methods, or subclasses, and itself raises nothing."
  ],
  "module": "errors",
  "notes": "minidb/errors.py contains QueryError and nothing else; it imports only the standard library and has no dependencies on other minidb modules, so every module may import it freely without cycles. Import it as `from minidb.errors import QueryError`. QueryError derives directly from Exception (not from ValueError or RuntimeError), so `except QueryError` catches only minidb failures, and `except Exception` in api.query must convert any other escaping exception (KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, etc.) into QueryError before it leaves the public API. Construct it with a single descriptive message string; the message text is not part of the contract and must not be parsed by callers, who should branch only on the exception type. There is no error-code enum, no cause chaining requirement, and no factory helper: dependent modules simply `raise QueryError(<message>)` at the point of detection, optionally with `from exc` to preserve context. It is a plain Exception, so it can be raised, caught, re-raised, and pickled normally."
}

### functions (minidb/functions.py)
{
  "exports": [
    "SCALARS: dict[str, Callable[..., Any]] - maps the upper-case scalar function name to a callable taking already-evaluated argument values and returning the result value; keys are exactly \"UPPER\", \"LOWER\", \"LENGTH\", \"ABS\", \"COALESCE\"; each callable raises QueryError on wrong argument count or on an argument of a type it cannot handle (e.g. UPPER of a number, ABS of a string).",
    "SCALARS[\"UPPER\"](value: Any) -> str | None - upper-cases a string; returns None if value is None; raises QueryError if value is not a string or None.",
    "SCALARS[\"LOWER\"](value: Any) -> str | None - lower-cases a string; returns None if value is None; raises QueryError if value is not a string or None.",
    "SCALARS[\"LENGTH\"](value: Any) -> int | None - character length of a string; returns None if value is None; raises QueryError if value is not a string or None.",
    "SCALARS[\"ABS\"](value: Any) -> int | float | None - absolute value of an int or float; returns None if value is None; raises QueryError if value is not a number or None.",
    "SCALARS[\"COALESCE\"](*values: Any) -> Any - returns the first argument that is not None, or None if all are None; raises QueryError if called with no arguments.",
    "AGGREGATES: dict[str, Callable[[list[Any]], Any]] - maps the upper-case aggregate name to a callable taking the list of already-evaluated values for one group (in row order, NULLs included as None) and returning the aggregate value; keys are exactly \"COUNT\", \"SUM\", \"AVG\", \"MIN\", \"MAX\"; raises QueryError on values whose types cannot be combined or compared.",
    "AGGREGATES[\"COUNT\"](values: list[Any]) -> int - number of values that are not None; returns 0 for an empty list; never returns None and never raises.",
    "AGGREGATES[\"SUM\"](values: list[Any]) -> int | float | None - sum of the non-None values, None when there is no non-None value; raises QueryError if any non-None value is not a number.",
    "AGGREGATES[\"AVG\"](values: list[Any]) -> float | None - arithmetic mean of the non-None values as a float, None when there is no non-None value; raises QueryError if any non-None value is not a number.",
    "AGGREGATES[\"MIN\"](values: list[Any]) -> Any - smallest non-None value using compare ordering, None when there is no non-None value; raises QueryError if the non-None values are of mutually incomparable types.",
    "AGGREGATES[\"MAX\"](values: list[Any]) -> Any - largest non-None value using compare ordering, None when there is no non-None value; raises QueryError if the non-None values are of mutually incomparable types.",
    "like_match(value: Any, pattern: Any) -> bool | None - SQL LIKE test where % matches any run of characters (including none) and _ matches exactly one character, all other pattern characters matching literally; returns None (unknown) if value or pattern is None; raises QueryError if value or pattern is not a string or None.",
    "compare(a: Any, b: Any) -> int | None - three-way comparison returning -1 if a < b, 0 if equal, 1 if a > b; returns None (unknown) if either operand is None; raises QueryError for a mixed-type comparison such as a number against a string, or any pair of values that are not both numbers, both strings, or both booleans."
  ],
  "module": "functions",
  "notes": "SQL NULL is Python None everywhere in this module. Names are the upper-cased function names; callers must uppercase before lookup and must test membership themselves (`name in SCALARS`) instead of catching KeyError, since a lookup miss is not our error to report. Every failure this module raises is minidb.errors.QueryError; it never lets KeyError, TypeError, ValueError, ZeroDivisionError, or AttributeError escape. Scalar callables receive positional argument values only (no rows, no expression nodes) and propagate None, except COALESCE which is the only NULL-consuming scalar. Aggregate callables receive one flat list of values per group, already extracted from the group's rows and in row order; they do no NULL filtering on the caller's behalf beyond the per-aggregate rules above, so the engine may pass the raw column values. COUNT(*) has no argument, so the engine should call AGGREGATES[\"COUNT\"] with a list holding one non-None placeholder per row in the group (for example the row count worth of any non-None value) or simply use the group's row count; COUNT never returns None. An aggregate over an empty group is well defined: COUNT gives 0, the others give None, which is what makes the aggregate-without-GROUP-BY-over-zero-rows single output row work. Numbers compare across int and float; bool compares only with bool. compare returning None means unknown, which callers must treat as not-true in WHERE/HAVING/ON, and equality/inequality operators must be derived from compare so that any comparison with NULL is unknown. compare imposes no NULL ordering: ORDER BY NULL placement (before non-NULLs ascending, after them descending) is the engine's responsibility, and compare is only called with non-None operands there. MIN/MAX order by the same rule as compare, so they raise on mixed-type input exactly where ORDER BY would. Division by zero yielding NULL is arithmetic in the engine, not part of this module: no operator functions are exported here."
}

### planner (minidb/planner.py)
{
  "exports": [
    "@dataclass class PlannedItem: name: str; expr: object - one computed output column; `name` is the final output key already resolved by the Output naming rules (bare column -> column name, alias -> alias, other expression -> source text with all whitespace removed), `expr` is a nodes.py expression node (never Star).",
    "@dataclass class PlannedOrderKey: expr: object; descending: bool; output_index: int | None - one ORDER BY key; when `output_index` is not None the key is the already-computed output value of `plan.select_items[output_index]` (ORDER BY referencing an output alias), otherwise `expr` must be evaluated against the source row.",
    "@dataclass class Plan: sources: list[tuple[str, str]]; join_conditions: list[object]; where: object | None; group_by: list[object]; having: object | None; select_items: list[PlannedItem]; distinct: bool; order_by: list[PlannedOrderKey]; limit: int | None; offset: int | None; is_aggregate: bool; aggregate_calls: list[object]; column_bindings: dict[tuple[str | None, str], tuple[str, str]] - fully validated, name-resolved description of one SELECT statement; every field is populated by `plan()` and must not be mutated by consumers.",
    "def plan(select: Select, tables: dict[str, list[dict]]) -> Plan - resolves every table and column reference, expands `*` and `table.*`, computes output names, classifies aggregate vs non-aggregate execution, and validates the statement; raises minidb.errors.QueryError for an unknown table, unknown column, ambiguous unqualified column, duplicate table alias, aggregate nesting, an aggregate in WHERE or in an ON condition, a non-aggregate select/HAVING/ORDER BY expression that is not one of the GROUP BY expressions when grouping or aggregating, an unresolvable ORDER BY reference, an unknown function name, a wrong argument count for a known function, or a negative LIMIT/OFFSET. Raises nothing else: it never lets KeyError, IndexError, AttributeError or TypeError escape.",
    "def resolve_column(plan: Plan, qualifier: str | None, name: str) -> tuple[str, str] - maps a column reference as written in the query to the pair `(source_alias, column_name)` it was bound to at plan time, using `plan.column_bindings`; raises minidb.errors.QueryError if the reference is unknown or ambiguous (this cannot happen for references that came from the planned AST, since those were already validated)."
  ],
  "module": "planner",
  "notes": "Evaluation environment: `plan.sources` lists `(table_name, alias)` in FROM then JOIN order; `alias` equals the table name when no alias was written, and aliases are unique. A joined row handed to expression evaluation is `{alias: row_dict}`, so the engine resolves a Column by calling `resolve_column(plan, qualifier, name)` and reading `env[source_alias].get(column_name)`; a column missing from a particular row dict is NULL (None), not an error. `plan.join_conditions` has length `len(plan.sources) - 1` and `join_conditions[i]` is the ON condition for `sources[i + 1]`; INNER JOIN only, so a row survives only when its ON condition is true (unknown/NULL is not true). Star expansion is already done: `plan.select_items` never contains a Star node and is in `FROM`/`JOIN` order, within a table in that table's column order (key order of the table's first row; an empty table contributes no columns), with a later table's column winning a name collision, i.e. the colliding earlier item is dropped from the list. NULL is Python None throughout. `plan.is_aggregate` is True when the statement has aggregates and/or a GROUP BY; when it is True and `plan.group_by` is empty the engine must emit exactly one row even over zero input rows. `plan.aggregate_calls` lists every aggregate Func node occurring in the select list, HAVING and ORDER BY, in that order, deduplicated by node identity; the engine may pre-compute each of them per group and match by `id()`, or just evaluate them in place. `plan.group_by` holds the grouping expressions to evaluate per row to form the group key, in written order. `plan.having` is evaluated once per group and keeps the group only when true. Ordering is applied before DISTINCT filtering is irrelevant: the engine must compute the select list, then apply DISTINCT over whole output rows, then ORDER BY (stable), then OFFSET, then LIMIT. `plan.limit` and `plan.offset` are non-negative ints or None; negative values were already rejected. The planner does not evaluate expressions, does not read row values (it only reads the first row of each table for column order) and does not import functions.py; scalar/aggregate arity is validated against the fixed spec set (UPPER, LOWER, LENGTH, ABS with one argument, COALESCE with one or more, COUNT/SUM/AVG/MIN/MAX with one argument, plus COUNT(*)). All failures are minidb.errors.QueryError instances."
}


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