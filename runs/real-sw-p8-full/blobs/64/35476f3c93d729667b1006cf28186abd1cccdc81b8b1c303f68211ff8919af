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
    "Plan",
    "PlannedItem",
    "PlannedOrderKey",
    "plan",
    "resolve_column",
    "SCALAR_ARITY",
    "AGGREGATE_ARITY"
  ],
  "module": "planner",
  "notes": "Evaluation environment: `plan.sources` lists `(table_name, alias)` in FROM then JOIN order; `alias` equals the table name when no alias was written, and aliases are unique. A joined row handed to expression evaluation is `{alias: row_dict}`, so the engine resolves a Column by calling `resolve_column(plan, qualifier, name)` and reading `env[source_alias].get(column_name)`; a column missing from a particular row dict is NULL (None), not an error. `plan.join_conditions` has length `len(plan.sources) - 1` and `join_conditions[i]` is the ON condition for `sources[i + 1]`; INNER JOIN only, so a row survives only when its ON condition is true (unknown/NULL is not true). Star expansion is already done: `plan.select_items` never contains a Star node and is in `FROM`/`JOIN` order, within a table in that table's column order (key order of the table's first row; an empty table contributes no columns), with a later table's column winning a name collision, i.e. the colliding earlier item is dropped from the list. NULL is Python None throughout. `plan.is_aggregate` is True when the statement has aggregates and/or a GROUP BY; when it is True and `plan.group_by` is empty the engine must emit exactly one row even over zero input rows. `plan.aggregate_calls` lists every aggregate Func node occurring in the select list, HAVING and ORDER BY, in that order, deduplicated by node identity; the engine may pre-compute each of them per group and match by `id()`, or just evaluate them in place. `plan.group_by` holds the grouping expressions to evaluate per row to form the group key, in written order. `plan.having` is evaluated once per group and keeps the group only when true. Ordering is applied before DISTINCT filtering is irrelevant: the engine must compute the select list, then apply DISTINCT over whole output rows, then ORDER BY (stable), then OFFSET, then LIMIT. `plan.limit` and `plan.offset` are non-negative ints or None; negative values were already rejected. The planner does not evaluate expressions, does not read row values (it only reads the first row of each table for column order) and does not import functions.py; scalar/aggregate arity is validated against the fixed spec set (UPPER, LOWER, LENGTH, ABS with one argument, COALESCE with one or more, COUNT/SUM/AVG/MIN/MAX with one argument, plus COUNT(*)). All failures are minidb.errors.QueryError instances."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
### errors (minidb/errors.py)
{
  "exports": [
    "QueryError"
  ],
  "module": "errors",
  "notes": "minidb/errors.py contains QueryError and nothing else; it imports only the standard library and has no dependencies on other minidb modules, so every module may import it freely without cycles. Import it as `from minidb.errors import QueryError`. QueryError derives directly from Exception (not from ValueError or RuntimeError), so `except QueryError` catches only minidb failures, and `except Exception` in api.query must convert any other escaping exception (KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, etc.) into QueryError before it leaves the public API. Construct it with a single descriptive message string; the message text is not part of the contract and must not be parsed by callers, who should branch only on the exception type. There is no error-code enum, no cause chaining requirement, and no factory helper: dependent modules simply `raise QueryError(<message>)` at the point of detection, optionally with `from exc` to preserve context. It is a plain Exception, so it can be raised, caught, re-raised, and pickled normally."
}

### nodes (minidb/nodes.py)
{
  "exports": [
    "Expr",
    "Column",
    "Literal",
    "Star",
    "BinOp",
    "UnaryOp",
    "Func",
    "SelectItem",
    "SelectItem.output_name",
    "TableRef",
    "TableRef.ref_name",
    "Join",
    "OrderKey",
    "Select"
  ],
  "module": "nodes",
  "notes": "Pure data only: every class is a frozen dataclass with no validation, no name resolution, and no evaluation, so constructing a node never raises and nodes.py imports nothing but the standard library (dataclasses/typing). All containers are tuples, not lists, so nodes are hashable and safe to use as dict keys (the planner may key resolved metadata by node identity or value). Case handling is fixed by the parser before nodes are built: keywords, operator words ('AND', 'OR', 'NOT', 'IN', 'LIKE', 'IS NULL', 'IS NOT NULL') and function names are stored upper-cased, while identifiers (table, alias, column names) are stored exactly as written and are case-sensitive. `x IN (a, b, ...)` is BinOp('IN', x, (a, b, ...)) - the only case where BinOp.right is a tuple; `x IS NULL` / `x IS NOT NULL` are UnaryOp, not BinOp; a LIKE pattern is a Literal on the right. `SELECT *` is a SelectItem whose expr is Star(None) and `t.*` is Star('t'); Star may appear only as a select item expr or as the single argument of COUNT, and expansion to real columns is the planner's job. Output naming lives in SelectItem.output_name(): the parser MUST set source_text to the exact source slice of the expression (the method only strips whitespace from it), otherwise unaliased expressions such as `price * 2` cannot be named 'price*2'. A Select always has a from_table; there is no FROM-less query. limit/offset are stored as parsed ints without sign checking, so the planner must reject negatives with QueryError. Nodes carry no positions; error messages that need a source position should use tokens.Token.pos from the parser."
}


PEER REVIEW OF YOUR MODULE:
- minidb/tokens.py: the token `kind` vocabulary actually emitted ("KEYWORD", "IDENT", "NUMBER", "STRING", "OP", "PUNCT") is not part of the published tokens interface, so the parser is coding against undocumented strings and one rename breaks it silently -> publish the exact kind set (and that KEYWORD values are upper-cased while IDENT values keep source case) as part of the tokens contract.
- minidb/tokens.py: tokenize appends no end-of-input sentinel token, so a parser that peeks past the last token gets IndexError, which the spec forbids from escaping the public API and which api.py can only blanket-wrap -> either append a final Token("EOF", None, len(sql)) or have the tokens interface state explicitly that end-of-input detection is the parser's job.
- minidb/tokens.py: `-` is only ever emitted as OP, and nodes.Select.limit/offset are typed `int | None`, so `LIMIT -1` cannot be represented as parsed; a UnaryOp there would be a type mismatch for the planner's negative-LIMIT check -> have the parser fold a leading minus into the integer it stores in Select.limit/Select.offset so the planner sees -1 and raises QueryError, and never a node.
- minidb/nodes.py: SelectItem.source_text defaults to "", so an unaliased non-column expression constructed without it silently gets output name "" instead of the whitespace-stripped source text the naming rule requires -> drop the default so the parser must supply source_text, or make the planner raise QueryError when output_name() is empty for a non-Star item.
- minidb/nodes.py: an ORDER BY reference to an output alias is indistinguishable from a real column, since both arrive as Column(name=..., table=None) -> planner must resolve unqualified OrderKey Column names against select-list output names first and only then against table columns, otherwise a valid `ORDER BY n` becomes unknown-column (or ambiguous-column under a join) QueryError.
- minidb/nodes.py: Func.args is typed `tuple[Expr | Star, ...]` while Expr already includes Star, and BinOp.right overloads a single Expr with a tuple for IN only -> harmless but the engine must branch on `isinstance(right, tuple)` for IN and must special-case Func('COUNT', (Star(),)); state both invariants in the nodes contract so the engine does not hit TypeError iterating a non-tuple right.
- minidb/nodes.py: Func.name is documented as upper-cased but nothing enforces it, and functions.py keys SCALARS/AGGREGATES strictly on upper-case names with membership tested by the caller -> the parser must upper-case the IDENT before constructing Func, and the planner must reject an unknown name with QueryError rather than letting engine do `SCALARS[name]` and raise KeyError.
- minidb/tokens.py: `.` is PUNCT and _read_number only starts on a digit, so `.5` lexes as PUNCT + NUMBER and will surface as a confusing parse error, while `1.5e3` is accepted as float -> either accept a leading-dot float in _read_number or leave it, but document that numeric literals must begin with a digit so the parser's error message is honest.
- minidb/nodes.py: __all__ omits 'Expr' although 'Expr' is a published export, so `from .nodes import *` (and any tooling driven by __all__) loses the type alias -> add "Expr" to __all__.
- minidb/functions.py: AGGREGATES['COUNT'] (_count) skips None, so it implements COUNT(x) only; there is no entry that counts rows, yet the spec requires COUNT(*) to count rows including all-NULL ones -> document in the published interface that the engine must call AGGREGATES['COUNT'] with one non-None sentinel per row for COUNT(*) (e.g. [True] * len(rows)), or add a separate 'COUNT_STAR' entry; otherwise COUNT(*) will undercount.

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