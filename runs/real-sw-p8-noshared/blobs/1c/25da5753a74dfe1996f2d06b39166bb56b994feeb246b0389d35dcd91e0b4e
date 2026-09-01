Implement exactly one file of the `minidb` system: `minidb/nodes.py` (AST dataclasses).

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
    "Expr = Column | Literal | BinOp | UnaryOp | Func | Star - module-level type alias naming any expression node; raises nothing.",
    "@dataclass(frozen=True) class Column: name: str; table: str | None = None - a column reference, `col` (table=None) or `table.col`/`alias.col` (table set to the written qualifier); raises nothing.",
    "@dataclass(frozen=True) class Literal: value: int | float | str | None - a literal; value is already decoded (SQL NULL is Python None, '' inside a string literal already unescaped to a single quote); raises nothing.",
    "@dataclass(frozen=True) class Star: table: str | None = None - `*` (table=None) or `table.*` (table set to the written table name or alias); raises nothing.",
    "@dataclass(frozen=True) class BinOp: op: str; left: Expr; right: Expr | tuple[Expr, ...] - a binary operation; op is one of '+', '-', '*', '/', '=', '<>', '!=', '<', '<=', '>', '>=', 'AND', 'OR', 'IN', 'LIKE'; right is a tuple of Expr only when op == 'IN', otherwise a single Expr; raises nothing.",
    "@dataclass(frozen=True) class UnaryOp: op: str; operand: Expr - a unary operation; op is one of 'NOT', '-', '+', 'IS NULL', 'IS NOT NULL'; raises nothing.",
    "@dataclass(frozen=True) class Func: name: str; args: tuple[Expr | Star, ...] - a function call; name is upper-cased ('UPPER', 'LOWER', 'LENGTH', 'ABS', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX'); COUNT(*) is Func('COUNT', (Star(),)); arity is not checked here; raises nothing.",
    "@dataclass(frozen=True) class SelectItem: expr: Expr | Star; alias: str | None = None; source_text: str = '' - one entry of the select list; alias is the AS name when written; source_text is the exact source slice of expr, used for output naming; raises nothing.",
    "SelectItem.output_name(self) -> str - the output column name: alias when set, else the bare column name when expr is a Column, else source_text with all whitespace removed; returns '' when expr is a Star (callers expand stars themselves); raises nothing.",
    "@dataclass(frozen=True) class TableRef: name: str; alias: str | None = None - a table in FROM or JOIN; raises nothing.",
    "TableRef.ref_name(self) -> str - the name by which this table is referenced in expressions: alias if set, else name; raises nothing.",
    "@dataclass(frozen=True) class Join: table: TableRef; condition: Expr; kind: str = 'INNER' - one INNER JOIN clause with its ON condition, which is never None; kind is always 'INNER' in this dialect; raises nothing.",
    "@dataclass(frozen=True) class OrderKey: expr: Expr; descending: bool = False - one ORDER BY key; descending is True for DESC, False for ASC or unspecified; raises nothing.",
    "@dataclass(frozen=True) class Select: items: tuple[SelectItem, ...]; from_table: TableRef; distinct: bool = False; joins: tuple[Join, ...] = (); where: Expr | None = None; group_by: tuple[Expr, ...] = (); having: Expr | None = None; order_by: tuple[OrderKey, ...] = (); limit: int | None = None; offset: int | None = None - the whole parsed statement; unwritten clauses are None or empty tuples; raises nothing."
  ],
  "module": "nodes",
  "notes": "Pure data only: every class is a frozen dataclass with no validation, no name resolution and no evaluation, so constructing a node never raises and nodes.py imports nothing outside the standard library (dataclasses). The field names listed above are the entire contract - read them directly, do not probe for alternative spellings or switch on type(node).__name__. All child containers are tuples, so nodes are hashable; note that they also have value equality, so two structurally identical expressions compare equal, and aggregate results must be keyed by a planner-assigned index rather than by id(node). Case handling is fixed by the parser before nodes are built: operator words ('AND', 'OR', 'NOT', 'IN', 'LIKE', 'IS NULL', 'IS NOT NULL') and function names are stored upper-cased, while identifiers (table, alias and column names) are stored exactly as written and are case-sensitive. `x IN (a, b, ...)` is BinOp('IN', x, (a, b, ...)) and is the only case where BinOp.right is a tuple; `x IS NULL` / `x IS NOT NULL` are UnaryOp, not BinOp; a LIKE pattern is a Literal on the right. `SELECT *` is a SelectItem whose expr is Star(None) and `t.*` is Star('t'); a Star may appear only as a select item expr or as the single argument of COUNT, and expanding it to real columns is the planner's job. Output naming lives in SelectItem.output_name(): the parser MUST set source_text to the exact source slice of the expression (the method only strips whitespace), otherwise `price * 2` cannot be named 'price*2'. A Select always has a from_table; there is no FROM-less query. limit and offset are stored as parsed ints without sign checking, so the planner must reject negatives with QueryError. Nodes carry no source positions; error messages needing a position must use the parser's token positions."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/engine.py: the whole plan is re-derived by attribute guessing (_field(plan, ('sources','from_sources','source_list','from_tables')), ('select_items','items','select_list','projections'), ('group_by','groupby','group_keys'), ...), which reaches around planner's published Plan interface and re-does its work; a Plan that names a field differently silently degrades instead of failing -> read the exact field names planner published on Plan and let a mismatch be an outright error.
- minidb/engine.py: a JOIN whose ON condition is absent is padded with None in _join_conditions and then executed as a cross product; the spec only supports INNER JOIN with ON -> raise QueryError('JOIN requires an ON condition') when a join source has no condition instead of padding the list.
- minidb/engine.py: _output_name falls back to _render(expr) when the plan carries no source_text, so an unaliased non-column expression can be named by reconstructed text rather than the source text with whitespace removed (spec: `price * 2` must be named `price*2`, `SUM(qty)` must be `SUM(qty)`) -> require the planner/parser-supplied source text for unaliased expressions and only use ''.join(text.split()) on it.
- minidb/engine.py: _select_items de-duplicates output names by deleting the earlier pair (`ordered = [pair for pair in ordered if pair[0] != name]`), which also moves the surviving column to the end; for `SELECT a, b, a` the output keys become b, a instead of a, b, and the rule 'later wins' is specified only for `*` expansion collisions -> apply the drop-earlier rule to star expansion only and keep explicit select-list positions.
- minidb/engine.py: _source_rows accepts a tuple of rows and None, while api._validate_arguments has already rejected anything that is not a list of dicts, so the two modules disagree about the accepted table shape -> settle on list-of-dict and delete the tuple/None tolerance.
- minidb/engine.py: the excerpt ends mid-expression in _descending (`direction = _field(entry, ('direction', `), so ORDER BY direction handling, NULL ordering, mixed-type comparison and LIMIT/OFFSET validation cannot be verified -> ensure the file is complete and that NULL sorts first in ASC and last in DESC, that an incomparable pair from compare() becomes QueryError rather than a silent tie, and that negative LIMIT/OFFSET raises QueryError.
- minidb/engine.py: _compile calls _source_rows for every source just to build the schema and the row scan re-reads the same tables, so unknown-table detection happens twice and each table is materialised twice -> build the schema from the rows already fetched for the FROM/JOIN scan.
- minidb/api.py: no contract problem found -- query(sql, tables) matches the published signature, calls parse, plan and execute exactly as published, and converts RecursionError and every other non-QueryError exception into QueryError, so KeyError/TypeError/IndexError/AttributeError cannot escape the public API.
- minidb/errors.py: no problems found. QueryError derives directly from Exception, adds no attributes or subclasses and imports nothing, matching its published interface; the docstring-only class body is valid Python, so no `pass` is needed.
- minidb/api.py: signatures check out. query(sql, tables) -> list[dict] matches the specification, and the calls parse(sql), plan(select, tables), execute(plan, tables) match the published interfaces of parser, planner and engine; parse is correctly handed the raw sql string, which is what tokens.tokenize is driven from inside parser.

Return ONLY a JSON object:
{"path": "minidb/nodes.py", "code": "<complete file contents>",
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