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
    "query"
  ],
  "module": "api",
  "notes": "Public surface: `query` is the ONLY public name in minidb/api.py; minidb/__init__.py (provided by the harness) re-exports `query` from here and `QueryError` from minidb/errors.py, so dependents must import QueryError from minidb.errors, not from api.\n\nInputs: `tables` is not copied or mutated; keys are table names (case-sensitive), values are lists of row dicts mapping column name to value. A table's column order is the key order of its first row; an empty table list therefore contributes no columns to `*` expansion. `sql` keywords are case-insensitive, identifiers case-sensitive. Passing anything other than a str for `sql` or a dict of lists of dicts for `tables` is reported as QueryError, not TypeError.\n\nOutputs: a new list of new dicts is returned; callers may mutate it freely. Values are the Python values taken from the input rows or computed by the engine (None represents SQL NULL; AVG yields float; division by zero yields None). An aggregate select list with no GROUP BY always yields exactly one row, even over zero input rows; otherwise zero result rows yield an empty list.\n\nError behaviour: api performs no validation of its own beyond the wrapping described above; all diagnostics originate in tokens/parser/planner/engine, and their QueryError messages are propagated unchanged (message text is not part of the contract). api catches BaseException subclasses only where they are ordinary Exception instances; KeyboardInterrupt and SystemExit are allowed to propagate.\n\nDependency direction: api depends on parser.parse, planner.plan, engine.execute and errors.QueryError exactly as those modules publish them, and no module imports api."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
### engine (minidb/engine.py)
{
  "exports": [
    "execute"
  ],
  "module": "engine",
  "notes": "Argument shapes: `plan` is exactly the object returned by planner.plan(select, tables) and is consumed through the attributes planner publishes; engine performs no parsing and re-does no name resolution, aggregate classification, or select-list naming validation of its own. `tables` is the same mapping the public API received: table name -> list of rows, each row a dict of column name to value; column order for a table is the key order of its first row, and a table with zero rows contributes no columns to `*` expansion.\n\nReturn shape: a new list of new dicts, one per result row. Keys are the planner's output names (bare `col` or `t.col` -> `col`; alias if aliased; otherwise the expression's source text with all whitespace removed). Every returned row carries the same keys in the same order, which is select-list order (for `*`, FROM/JOIN order and each table's column order, with a later table's column winning a name collision). Rows are ordered as ORDER BY dictates (stable sort, NULL before non-NULL in ASC and after in DESC, output aliases resolvable) and truncated by OFFSET then LIMIT after ordering.\n\nInvariants: execute() is pure with respect to its inputs - it never mutates `tables`, the rows inside it, or `plan`, and result rows never alias input row dicts. It is re-entrant and holds no module-level state, so the same plan may be executed repeatedly.\n\nValue semantics implemented here: three-valued logic, so WHERE/HAVING/ON keep a row only when the condition is true (NULL AND false is false, NULL OR true is true); arithmetic with NULL yields NULL; division by zero yields NULL rather than raising. Aggregates: COUNT(*) counts rows, COUNT(x) counts non-NULL values and returns 0 with no input, SUM/AVG/MIN/MAX ignore NULLs and return NULL with no non-NULL input, AVG returns a float. An aggregate select list with no GROUP BY yields exactly one row even over zero input rows; with GROUP BY, one row per distinct group key. DISTINCT is applied to the whole computed output row, before ORDER BY. Comparison, LIKE matching (with % and _), and scalar/aggregate evaluation are delegated to functions.compare, functions.like_match, functions.SCALARS, and functions.AGGREGATES rather than reimplemented.\n\nError behaviour: the only exception type that leaves execute() is QueryError, raised from minidb.errors; api.py therefore needs no additional translation for engine failures, only its own catch-all wrapping."
}

### errors (minidb/errors.py)
{
  "exports": [
    "QueryError"
  ],
  "module": "errors",
  "notes": "minidb/errors.py contains QueryError and nothing else; it imports only the standard library and has no dependencies on other minidb modules, so every module may import it freely without cycles. Import it as `from minidb.errors import QueryError`. QueryError derives directly from Exception (not from ValueError or RuntimeError), so `except QueryError` catches only minidb failures, and `except Exception` in api.query must convert any other escaping exception (KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, etc.) into QueryError before it leaves the public API. Construct it with a single descriptive message string; the message text is not part of the contract and must not be parsed by callers, who should branch only on the exception type. There is no error-code enum, no cause chaining requirement, and no factory helper: dependent modules simply `raise QueryError(<message>)` at the point of detection, optionally with `from exc` to preserve context. It is a plain Exception, so it can be raised, caught, re-raised, and pickled normally."
}

### parser (minidb/parser.py)
{
  "exports": [
    "parse"
  ],
  "module": "parser",
  "notes": "Only `parse` is public; everything else in parser.py is private (leading underscore).\n\nparse() is purely syntactic: it does no name resolution, no table lookup, no aggregate classification and no type checking. Unknown tables/columns, ambiguous column names, aggregate/GROUP BY validation and mixed-type ordering are the planner's and engine's business, so parse() accepts them and the planner must reject them.\n\nAST returned (fields of the nodes.py dataclasses, in this order and always present):\n- Select(distinct: bool, items: list[SelectItem], from_: TableRef, joins: list[Join], where: expr|None, group_by: list[expr], having: expr|None, order_by: list[OrderKey], limit: int|None, offset: int|None). Absent clauses are None for where/having/limit/offset and an empty list for joins/group_by/order_by; `distinct` is False when DISTINCT is absent.\n- SelectItem(expr: expr, alias: str|None, name: str) - `name` is the output column name already computed per the specification's Output naming rules: the alias when one is given, the bare column name for `col`/`t.col`, otherwise the source text of the expression with all whitespace removed (e.g. `SUM(qty)`, `price*2`). `SELECT *` and `t.*` appear as a SelectItem whose expr is Star(table=None) or Star(table='t') with alias None and name '*' / 't.*'; the planner performs the expansion.\n- TableRef(name: str, alias: str|None); Join(table: TableRef, on: expr) - INNER JOIN and JOIN produce the same Join node, and `on` is never None because the grammar requires ON.\n- OrderKey(expr: expr, descending: bool) - descending is False when ASC or nothing is written; the expr may be a Column naming an output alias, which the planner resolves.\n\nExpression nodes produced: Column(table: str|None, name: str), Literal(value: int|float|str|None) (NULL becomes Literal(None); '' inside a single-quoted string is already unescaped to one quote), BinOp(op: str, left, right), UnaryOp(op: str, operand), Func(name: str, args: list[expr]), Star(table: str|None).\n\nOperator spellings in BinOp.op are normalized to lowercase for word operators and kept verbatim for symbols: 'and', 'or', '+', '-', '*', '/', '=', '<>', '!=', '<', '<=', '>', '>=', 'in', 'like'. UnaryOp.op is 'not', '-' or '+'. `x IS NULL` becomes UnaryOp('is null', x) and `x IS NOT NULL` becomes UnaryOp('is not null', x). For BinOp('in', x, rhs) the right operand is a Func('in_list', [items...]) holding the parenthesized item expressions in written order. `!=` is preserved as written rather than folded into `<>`; treat both as inequality.\n\nFunc.name is upper-cased, so scalar and aggregate lookups against functions.SCALARS / functions.AGGREGATES use 'UPPER', 'LOWER', 'LENGTH', 'ABS', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX'. `COUNT(*)` is Func('COUNT', [Star(None)]). The parser does not check argument counts or whether a name is a known function; the planner does.\n\nPrecedence implemented, lowest to highest: OR, AND, NOT, comparison/IS/IN/LIKE, + -, * /, unary +/-, then primary (literal, column, function call, parenthesized expression). Keywords are matched case-insensitively; identifiers keep their source case."
}

### planner (minidb/planner.py)
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


PEER REVIEW OF YOUR MODULE:
- minidb/functions.py: SCALARS and AGGREGATES have two different calling conventions (scalars are `f(*args)`, aggregates are `f(list_of_values)`) but the published interface only says "SCALARS, AGGREGATES dicts", so engine can easily call one like the other and get a TypeError -> state the convention in the published notes: `SCALARS[name](*arg_values)` and `AGGREGATES[name](values_list)`.
- minidb/functions.py: AGGREGATES['COUNT'] is `_count`, which counts non-NULL entries, so there is no way to express COUNT(*), which must count rows including all-NULL ones -> either document that the engine must pass one non-NULL sentinel per row for COUNT(*) (e.g. `[True] * len(rows)`), or add an explicit `COUNT_STAR` entry; otherwise `SELECT COUNT(*) FROM t` under-counts rows whose value is NULL.
- minidb/functions.py: `_value_class` puts bool in its own class, so `compare(True, 1)` and `ABS(True)` raise QueryError, and MIN/MAX over a column holding booleans and numbers fails -> if booleans are expected in table data, fold _BOOLEAN into _NUMBER, or document that bool is a distinct comparison class so the engine's ORDER BY error message matches.
- minidb/functions.py: compare() returns None for NULL, so a caller doing `if compare(a, b) < 0` gets a TypeError instead of unknown -> the engine must branch on `result is None` first; worth stating in the published notes, since spec requires unknown (not an error, not false-by-accident) for any comparison with NULL.
- minidb/functions.py: `_LIKE_CACHE` is an unbounded module-level dict keyed by raw pattern text, so a long-running process leaks one compiled regex per distinct pattern -> cap it (e.g. functools.lru_cache on the compile helper).
- minidb/parser.py: the parser hard-codes token kinds and spellings that tokens.py never published ('KEYWORD', 'IDENT', 'PUNCT', 'OP', 'NUMBER', '*' and '.' as OP/PUNCT respectively) -> get tokens.py to publish the exact kind set and which kind carries '*', '.', ',' and '(' ')', otherwise `SELECT *` and `t.*` silently fail to parse.
- minidb/parser.py: parse() does not wrap non-QueryError exceptions from tokenize() or from token attribute access, so anything unexpected escapes as AttributeError/TypeError from a module whose published contract is QueryError-only -> wrap the body of parse() in `try/except QueryError: raise/except Exception as exc: raise QueryError(...) from exc`.
- minidb/parser.py: `_optional_alias()` accepts any IDENT as an alias for both select items and table refs, so a mistyped keyword ('SELECT a FROM t WHER x') is swallowed as an alias and the query silently means something else -> only accept an alias after AS, or reject an unconsumed IDENT that is followed by another IDENT/expression.
- minidb/parser.py: `_select_item` computes source_text as `sql[start:next_token_pos]`, which includes the whitespace and any comment between the expression and the following token; SelectItem.output_name() only strips whitespace, so `SELECT price * 2 /*c*/ FROM t` is named 'price*2/*c*/' -> record the end position of the last token consumed by the expression instead of the start of the next token.
- minidb/parser.py: the listing is truncated inside `_and_expression`, so the parsing of NOT, comparisons, IS NULL / IS NOT NULL, IN (...), LIKE, unary minus and function calls could not be reviewed; those are exactly the productions the planner depends on for BinOp('IN', x, tuple) and UnaryOp('IS NULL', x) shapes -> re-send the remainder of the file for review.

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