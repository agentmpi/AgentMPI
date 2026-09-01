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
    "SCALARS",
    "AGGREGATES",
    "like_match",
    "compare"
  ],
  "module": "functions",
  "notes": "SQL NULL is Python None everywhere in this module. Names are the upper-cased function names; callers must uppercase before lookup and must test membership themselves (`name in SCALARS`) instead of catching KeyError, since a lookup miss is not our error to report. Every failure this module raises is minidb.errors.QueryError; it never lets KeyError, TypeError, ValueError, ZeroDivisionError, or AttributeError escape. Scalar callables receive positional argument values only (no rows, no expression nodes) and propagate None, except COALESCE which is the only NULL-consuming scalar. Aggregate callables receive one flat list of values per group, already extracted from the group's rows and in row order; they do no NULL filtering on the caller's behalf beyond the per-aggregate rules above, so the engine may pass the raw column values. COUNT(*) has no argument, so the engine should call AGGREGATES[\"COUNT\"] with a list holding one non-None placeholder per row in the group (for example the row count worth of any non-None value) or simply use the group's row count; COUNT never returns None. An aggregate over an empty group is well defined: COUNT gives 0, the others give None, which is what makes the aggregate-without-GROUP-BY-over-zero-rows single output row work. Numbers compare across int and float; bool compares only with bool. compare returning None means unknown, which callers must treat as not-true in WHERE/HAVING/ON, and equality/inequality operators must be derived from compare so that any comparison with NULL is unknown. compare imposes no NULL ordering: ORDER BY NULL placement (before non-NULLs ascending, after them descending) is the engine's responsibility, and compare is only called with non-None operands there. MIN/MAX order by the same rule as compare, so they raise on mixed-type input exactly where ORDER BY would. Division by zero yielding NULL is arithmetic in the engine, not part of this module: no operator functions are exported here."
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


PEER REVIEW OF YOUR MODULE:
- minidb/errors.py: no problems found. QueryError derives directly from Exception, adds no attributes and imports nothing, matching its published interface; the docstring-only class body is valid Python, so no `pass` is needed.
- minidb/api.py: signatures check out. query(sql, tables) -> list[dict] matches the spec, and the calls parse(sql), plan(select, tables), execute(plan, tables) match the published interfaces of parser, planner and engine; parse is correctly given the raw sql string rather than tokens, which is what tokens.tokenize is invoked through inside parser.
- minidb/api.py: `except RecursionError` is placed before `except Exception`, and RecursionError is a RuntimeError subclass, so deep-nesting failures are wrapped rather than escaping - correct as written; ZeroDivisionError, KeyError, IndexError, AttributeError and TypeError from any inner module are also converted, satisfying the 'never raise KeyError/IndexError/AttributeError/TypeError' rule.
- minidb/api.py: _validate_arguments checks table names and row types but not column keys, so a row dict with a non-string key (e.g. {1: 'x'}) flows into `*` expansion and output naming, where the result dict would carry a non-string output column name instead of failing cleanly -> extend the per-row loop to raise QueryError for any row key that is not a str.
- minidb/api.py: _validate_arguments walks every row of every table on each call, which is an O(rows) pre-pass duplicating validation the planner/engine must do anyway; harmless for correctness but it means a malformed row deep in a large table is reported before any syntax error in the sql -> if error precedence matters, run the type pre-pass after parse(sql) rather than before it.
- minidb/api.py: the wrapped message embeds the original exception type and text (`internal error while executing query: ...`); this is allowed since message text is not part of the contract, but it means internal bug details reach callers -> keep the `from exc` chaining and shorten the message if that is unwanted.
- minidb/tokens.py: the `kind` vocabulary ('KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'OP', 'PUNCT') is not part of the published Token/tokenize interface, so the parser has nothing contractual to switch on -> publish the exact kind set (and that KEYWORD values are upper-cased while IDENT values keep source case) as part of tokens.py's interface.
- minidb/tokens.py: `NULL` is emitted as a KEYWORD token, not as a literal token, so a naive parser will not build nodes.Literal(None) for it -> document that the parser must translate KEYWORD 'NULL' into nodes.Literal(None); Token.value is never None as the annotation suggests.
- minidb/tokens.py: tokenize() emits no end-of-input sentinel and the list is returned bare, so any parser lookahead past the last token raises IndexError, which the spec forbids escaping the public API -> append a final Token('EOF', '', len(sql)) (preferred) or state in the interface that the parser must bounds-check every peek and api.py must wrap IndexError in QueryError.
- minidb/tokens.py: every word matching a keyword in any case becomes a KEYWORD token, so identifiers such as a column named `order`, `Count` or `in` can never be referenced even though the spec makes identifiers case-sensitive -> either only treat words as keywords in positions where the grammar expects them, or publish the reserved-word list as a documented limitation so the parser reports it as QueryError rather than a confusing syntax error.

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