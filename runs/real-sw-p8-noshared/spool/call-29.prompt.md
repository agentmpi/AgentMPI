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
    "class QueryError(Exception) - the single public exception type of minidb; raised for any malformed query, unknown table, unknown column, ambiguous column reference, mixed-type ordering comparison, negative LIMIT/OFFSET, or type error. It takes the standard Exception arguments (typically one human-readable message string: QueryError('unknown column: x')), adds no extra attributes, methods, or subclasses, and itself raises nothing."
  ],
  "module": "errors",
  "notes": "minidb/errors.py contains QueryError and nothing else; it imports only the standard library and has no dependencies on other minidb modules, so every module may import it freely without cycles. Inside the package use `from .errors import QueryError`. QueryError derives directly from Exception (not from ValueError or RuntimeError), so `except QueryError` catches only minidb failures, and api.query must convert any other escaping exception (KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, RecursionError, etc.) into QueryError before it leaves the public API. Construct it with a single descriptive message string; the message text is not part of the contract and must not be parsed by callers, who should branch only on the exception type. There is no error-code enum, no cause chaining requirement, and no factory helper: dependent modules simply `raise QueryError(<message>)` at the point of detection, optionally with `from exc` to preserve context. Being a plain Exception, it can be raised, caught, re-raised, pickled, and re-exported by minidb/__init__.py unchanged."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/planner.py: `alias_of` falls back to `ref.name` and `build_sources` reads `ref.name`, `node.table`, `node.name`, `expr.operand`, `expr.left`, `expr.right`, `expr.name`, `expr.args`, `arg.table` directly, while parser.py deliberately assumes none of those field names and maps values onto whatever nodes.py actually declares; any other naming (qualifier, lhs, operands, ...) makes the planner raise AttributeError, which is not QueryError -> agree one canonical field set with nodes.py, or read node fields through the same tolerant accessor parser.py uses and convert a miss into QueryError.
- minidb/planner.py: `Func` classification compares `expr.name` against AGGREGATE_ARITY/SCALAR_ARITY, whose keys are upper-case only, so `sum(a)` or `Count(*)` is rejected as an unknown function even though the spec makes keywords case-insensitive; engine.py uppercases before its SCALARS/AGGREGATES lookup, so the two modules classify the same node differently -> uppercase once (`name = expr.name.upper()`) and use that for arity checks, aggregate classification and Plan contents.
- minidb/parser.py: `_build` catches TypeError from the keyword call and then passes the values positionally in its own canonical order; if a node class declares its fields in another order (BinOp(left, op, right)) the node is constructed silently wrong instead of failing -> only fall back to positional construction when the field count matches and no keyword match was possible, otherwise raise QueryError naming the class.
- minidb/parser.py: `_output_name` returns '*' or 't.*' for a Star select item and stores it in SelectItem's name field; if that value ever reaches the planner's output naming it produces a result key of '*', which no naming rule allows -> expand Star items before naming and have the planner ignore any name attached to a Star item.
- minidb/planner.py: `build_sources` fills `known[alias]` from the union of keys of all rows but `columns[alias]`, which drives `*` expansion, from the first row only, so a key that appears only in a later row is resolvable yet never appears in `SELECT *` output -> take both from the first row, per the rule that a table's columns are its first row's key order.
- minidb/planner.py: `if name not in self.tables` and `self.tables[name]` assume a dict; a non-mapping `tables` argument escapes as TypeError from plan() -> check `isinstance(tables, dict)` first and raise QueryError.
- minidb/planner.py: for a table with zero rows the alias goes into `open_schema`, so any column reference against it binds silently and evaluates to NULL rather than raising for an unknown column, and with two or more empty tables an unknown unqualified name is reported as 'ambiguous column' instead of 'unknown column' -> keep the permissive binding (no rows means no schema) but report unknown versus ambiguous consistently, since the spec requires unknown columns to be QueryError.
- minidb/planner.py: AGGREGATE_ARITY['COUNT'] is (1, 1), so `COUNT(*)` only survives validation if the parser delivers exactly one Star argument; a parser that emits `Func('COUNT', ())` for `COUNT(*)` is rejected as a wrong argument count -> state in the interface that COUNT(*) arrives as a single Star argument, and accept an empty argument tuple for COUNT as well.
- minidb/planner.py: the engine calls `resolve_column(plan, qualifier, name) -> (source_alias, column_name)` and relies on `Plan.column_bindings` holding every reference that occurred in the query, including `(None, col)` keys for unqualified names; that function is not visible in the reviewed excerpt -> confirm it is exported with exactly that signature and that plan() copies the planner's bindings into Plan.column_bindings, otherwise every Column evaluation in engine.py fails.
- minidb/planner.py: `build_sources` reads `self.select.from_table` directly while every other node access is defensive (`getattr(join, 'kind', 'INNER')`, `alias_of`); nodes.py declares the FROM field of Select, and if it is named `from_`/`table`/`source` this raises AttributeError on every query -> agree the field name with nodes.py and read it through the same defensive helper used for joins.

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