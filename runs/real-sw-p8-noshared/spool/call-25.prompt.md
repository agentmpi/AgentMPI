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
    "SCALARS: dict[str, Callable[..., Any]] - maps the upper-case scalar function name to a callable taking already-evaluated argument values and returning the result value; keys are exactly \"UPPER\", \"LOWER\", \"LENGTH\", \"ABS\", \"COALESCE\"; each callable raises QueryError on wrong argument count or on an argument whose type it cannot handle (e.g. UPPER of a number, ABS of a string).",
    "SCALARS[\"UPPER\"](value: Any) -> str | None - upper-cases a string; returns None if value is None; raises QueryError if value is neither a string nor None.",
    "SCALARS[\"LOWER\"](value: Any) -> str | None - lower-cases a string; returns None if value is None; raises QueryError if value is neither a string nor None.",
    "SCALARS[\"LENGTH\"](value: Any) -> int | None - character length of a string; returns None if value is None; raises QueryError if value is neither a string nor None.",
    "SCALARS[\"ABS\"](value: Any) -> int | float | None - absolute value of an int or float; returns None if value is None; raises QueryError if value is neither a number nor None.",
    "SCALARS[\"COALESCE\"](*values: Any) -> Any - returns the first argument that is not None, or None if all are None; raises QueryError if called with no arguments.",
    "AGGREGATES: dict[str, Callable[[list[Any]], Any]] - maps the upper-case aggregate name to a callable taking the list of already-evaluated values for one group (row order, NULLs present as None) and returning the aggregate value; keys are exactly \"COUNT\", \"SUM\", \"AVG\", \"MIN\", \"MAX\"; raises QueryError on values whose types cannot be combined or compared.",
    "AGGREGATES[\"COUNT\"](values: list[Any]) -> int - number of values that are not None; returns 0 for an empty list; never returns None.",
    "AGGREGATES[\"SUM\"](values: list[Any]) -> int | float | None - sum of the non-None values, None when there is no non-None value; raises QueryError if any non-None value is not a number.",
    "AGGREGATES[\"AVG\"](values: list[Any]) -> float | None - arithmetic mean of the non-None values as a float, None when there is no non-None value; raises QueryError if any non-None value is not a number.",
    "AGGREGATES[\"MIN\"](values: list[Any]) -> Any - smallest non-None value under compare ordering, None when there is no non-None value; raises QueryError if the non-None values are of mutually incomparable types.",
    "AGGREGATES[\"MAX\"](values: list[Any]) -> Any - largest non-None value under compare ordering, None when there is no non-None value; raises QueryError if the non-None values are of mutually incomparable types.",
    "like_match(value: Any, pattern: Any) -> bool | None - SQL LIKE test where % matches any run of characters (including none), _ matches exactly one character, and every other pattern character matches literally; returns None (unknown) if value or pattern is None; raises QueryError if a present value or pattern is not a string.",
    "compare(a: Any, b: Any) -> int | None - three-way comparison returning -1 if a < b, 0 if equal, 1 if a > b; returns None (unknown) if either operand is None; raises QueryError for a mixed-type comparison such as a number against a string, or for any pair that is not both numbers, both strings, or both booleans."
  ],
  "module": "functions",
  "notes": "SQL NULL is Python None everywhere in this module. Dictionary keys are the upper-cased function names; callers must uppercase the parsed name before lookup and must test membership themselves (`name in SCALARS`) rather than catching KeyError, since an unknown function name is the planner's error to report, not ours. Every failure raised from this module is minidb.errors.QueryError; it never lets KeyError, TypeError, ValueError, ZeroDivisionError, or AttributeError escape. Scalar callables receive positional argument values only, never rows or expression nodes, and they propagate None; COALESCE is the only scalar that consumes NULLs. Aggregate callables receive one flat list of values per group, already extracted from that group's rows in row order; they apply exactly the per-aggregate NULL rules above, so the engine may pass the raw column values without pre-filtering. COUNT(*) takes no argument, so the engine should call AGGREGATES[\"COUNT\"] with a list holding one non-None placeholder per row in the group, or simply use the group's row count; COUNT never returns None. Aggregates over an empty group are well defined - COUNT gives 0, the others give None - which is what makes an aggregate select list with no GROUP BY over zero input rows produce its single output row. Numbers compare across int and float; bool compares only with bool. compare returning None means unknown, and callers must treat unknown as not-true in WHERE, HAVING and ON; the operators = <> != < <= > >= must all be derived from compare so that any comparison with NULL is unknown. compare imposes no NULL ordering: ORDER BY NULL placement (before non-NULLs in ASC, after them in DESC) is the engine's job, and compare should only be handed non-None operands there. MIN and MAX order by the same rule as compare, so they raise on mixed-type input exactly where ORDER BY would. This module exports no operator or arithmetic helpers: NULL-propagating arithmetic, division by zero yielding NULL, three-valued AND/OR/NOT, IS NULL, and IN belong to the engine, which may build IN and the comparison operators on top of compare and LIKE on top of like_match."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/errors.py: no problems found. QueryError derives directly from Exception, adds no attributes or subclasses and imports nothing, matching its published interface; the docstring-only class body is valid Python, so no `pass` is needed.
- minidb/api.py: signatures check out. query(sql, tables) -> list[dict] matches the specification, and the calls parse(sql), plan(select, tables), execute(plan, tables) match the published interfaces of parser, planner and engine; parse is correctly handed the raw sql string, which is what tokens.tokenize is driven from inside parser.
- minidb/api.py: error wrapping is correct. `except RecursionError` precedes `except Exception` and RecursionError is a RuntimeError subclass, so it is caught by the specific clause; every other non-QueryError exception (KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError) is converted, satisfying the rule that those must never escape the public API.
- minidb/api.py: _validate_arguments checks table names and row types but never checks column keys, so a row such as {1: 'x'} flows into `*` expansion and output naming and yields a result dict with a non-string output column name instead of a clean failure -> in the per-row loop, raise QueryError for any row key that is not a str.
- minidb/api.py: the pre-pass walks every row of every table before parsing, so a malformed row deep in a large table is reported in preference to a syntax error in the sql, and the cost is O(total rows) even for a query that is rejected at lex time -> run _validate_arguments after parse(sql), or validate rows lazily as the engine touches each table.
- minidb/api.py: the wrapper message embeds the original exception type and text (`internal error while executing query: ...`); message text is not part of the contract so this is legal, but it exposes internal bug detail to callers -> keep the `from exc` chaining for debuggers and shorten the user-visible message if that is unwanted.
- minidb/tokens.py: the `kind` vocabulary ('KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'OP', 'PUNCT') is not part of the published Token/tokenize interface, so the parser has nothing contractual to switch on -> publish the exact kind set, plus the rule that KEYWORD values are upper-cased while IDENT values keep their source case.
- minidb/tokens.py: `NULL` is emitted as a KEYWORD token rather than a literal, so the parser will not naturally build nodes.Literal(None) for it -> state in the interface that KEYWORD 'NULL' must be translated to nodes.Literal(None); note Token.value is in practice never None despite the annotation.
- minidb/tokens.py: tokenize() emits no end-of-input sentinel, so any parser lookahead past the last token raises IndexError, which the spec forbids from escaping the public API -> append a final Token('EOF', '', len(sql)), or require the parser to bounds-check every peek and api.py to wrap IndexError in QueryError.
- minidb/tokens.py: any word matching a keyword in any case becomes a KEYWORD token, so identifiers such as a column named `order`, `Count` or `in` can never be referenced even though the spec makes identifiers case-sensitive -> only treat words as keywords where the grammar expects them, or publish the reserved-word list so the parser can report it as a clear QueryError.

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