Implement exactly one file of the `minidb` system: `minidb/tokens.py` (Token dataclass and tokenize()).

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
    "Token",
    "tokenize"
  ],
  "module": "tokens",
  "notes": "Token kinds and value shapes, which the parser may rely on exactly:\n- \"KEYWORD\": value is the keyword uppercased. Keywords are matched case-insensitively and are exactly: SELECT, DISTINCT, FROM, AS, INNER, JOIN, ON, WHERE, GROUP, BY, HAVING, ORDER, ASC, DESC, LIMIT, OFFSET, AND, OR, NOT, IS, NULL, IN, LIKE. GROUP BY / ORDER BY arrive as two separate KEYWORD tokens (GROUP, BY) and (ORDER, BY). NULL arrives as KEYWORD \"NULL\"; the parser turns it into its NULL literal (there is no NULL token kind).\n- \"IDENT\": value is the identifier's source text with case preserved, because identifiers are case-sensitive. Function names are NOT keywords: UPPER, LOWER, LENGTH, ABS, COALESCE, COUNT, SUM, AVG, MIN, MAX all arrive as IDENT with their original spelling, and the parser should uppercase an IDENT that is immediately followed by PUNCT \"(\" when looking it up in functions.SCALARS / functions.AGGREGATES.\n- \"NUMBER\": value is an already-converted int (no '.', no exponent) or float. Only unsigned numeric text is lexed; a leading '-' is a separate OP token, so unary minus is the parser's job.\n- \"STRING\": value is the decoded str contents without the surrounding single quotes, with each doubled '' already collapsed to one '. An unterminated literal is a QueryError.\n- \"OP\": value is exactly one of \"=\", \"<>\", \"!=\", \"<\", \"<=\", \">\", \">=\", \"+\", \"-\", \"*\", \"/\". Longest match wins, so \"<=\" and \"<>\" are never split. \"=\" is only ever one character.\n- \"PUNCT\": value is exactly one of \"(\", \")\", \",\", \".\".\nThe asterisk is always OP \"*\": SELECT *, t.*, and COUNT(*) are distinguished by the parser from context, not by a distinct token kind.\nNo end-of-input sentinel token is appended; the parser must treat exhaustion of the list as end of input and report a truncated query as a QueryError itself.\nA table/column qualification arrives as three tokens: IDENT, PUNCT \".\", IDENT (or IDENT, PUNCT \".\", OP \"*\").\nOutput naming support: `pos` is a real index into the original `sql`, so the parser can recover the source text of an expression by slicing `sql` from the first token's `pos` to the `pos` of the token after the expression (or to len(sql) at end of input) and removing whitespace. This is necessary because NUMBER and STRING values are converted, so token values alone do not reproduce the source spelling.\nErrors: every failure is raised as minidb.errors.QueryError with a message that includes the offending character or literal and its position; tokenize performs no syntactic validation beyond lexing, so keyword order, balanced parentheses and clause structure are entirely the parser's responsibility."
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
- minidb/engine.py: mixed-type ORDER BY is silently treated as a tie -- in _compare_keys, when compare(a, b) returns None the code does `continue` instead of failing, contradicting the spec rule that mixed-type comparison is a QueryError -> raise QueryError('cannot order values of different types') when compare() reports an incomparable pair (or require compare() itself to raise QueryError).
- minidb/engine.py: it imports `Plan, resolve_column` from .planner, but planner's published contract is `plan(select, tables) -> Plan`; `resolve_column` is not a promised export -> either have planner publish resolve_column(plan, qualifier, name) -> (alias, column) explicitly, or do name resolution through data already stored on Plan.
- minidb/engine.py: AST fields are probed by guessing attribute names (_attr(node, ('value','val','literal')), ('qualifier','table','table_name','prefix','alias'), ('args','arguments','params','operands'), and type(node).__name__ string tests) instead of using the field names nodes.py publishes -> import the dataclasses from .nodes and read their declared fields, so a rename in nodes.py fails loudly rather than mis-evaluating.
- minidb/engine.py: aggregate results are keyed by `id(call)` in _group_aggregates and looked up by `id(node)` in _eval_func; this only works if planner puts the very same node objects in plan.aggregate_calls and in the select/having expressions, and id() may be reused after a node is garbage collected -> key aggregates by a stable planner-assigned index or by the node object itself held in the Plan.
- minidb/engine.py: a JOIN with no recorded ON condition degrades to a cross product (`if condition is None ... joined.append(candidate)`), and plan.join_conditions being shorter than plan.sources is accepted silently -> treat a missing ON condition for a join source as QueryError('JOIN requires an ON condition').
- minidb/engine.py: _source_rows accepts tuples and None for a table's rows while api._validate_arguments has already rejected anything that is not a list, so the two modules disagree on the accepted table shape -> keep one rule (list of dicts) and drop the tuple/None tolerance in _source_rows.
- minidb/engine.py: the excerpt ends mid-statement (`if name ` at the end of _eval_func), so the scalar-function branch cannot be verified -> ensure the file is complete and that an unknown scalar name raises QueryError(f'unknown function: {name}') rather than falling through to a KeyError on SCALARS.
- minidb/engine.py: _slice raises QueryError for negative LIMIT/OFFSET only at execution time, so `LIMIT -1` on an empty plan path still costs a full scan and the check is duplicated with the planner's validation duty -> validate LIMIT/OFFSET in planner.plan and keep the engine check only as an assertion-style guard.
- minidb/api.py: no contract problem found -- query(sql, tables) matches the published signature, calls parse/plan/execute exactly as those modules publish them, and wraps RecursionError and every non-QueryError exception into QueryError, so KeyError/TypeError/IndexError/AttributeError cannot escape the public API.
- minidb/planner.py: `build_sources` reads `self.select.from_table`, but parser publishes the field as `Select.from_` (Select(distinct, items, from_, joins, where, group_by, having, order_by, limit, offset)). Every query dies with AttributeError before any QueryError can be raised -> use `self.select.from_`.

Return ONLY a JSON object:
{"path": "minidb/tokens.py", "code": "<complete file contents>",
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