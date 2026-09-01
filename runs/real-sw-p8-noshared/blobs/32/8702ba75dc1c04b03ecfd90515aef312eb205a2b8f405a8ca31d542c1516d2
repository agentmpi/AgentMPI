Implement exactly one file of the `minidb` system: `minidb/parser.py` (parse() -> Select).

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
    "def parse(sql: str) -> Select - Tokenizes `sql` with tokens.tokenize and builds the AST of one complete SELECT statement using the dataclasses of nodes.py; raises minidb.errors.QueryError for any malformed query (empty input, unexpected or missing token, unbalanced parentheses, truncated statement, trailing input after the statement, a LIMIT/OFFSET that is not a non-negative integer literal) and never lets KeyError, IndexError, AttributeError or TypeError escape."
  ],
  "module": "parser",
  "notes": "`parse` is the only public name in parser.py; every helper is private (leading underscore) and no state is shared between calls, so parse() is reentrant and thread-safe.\n\nparse() is purely syntactic. It does not look at `tables`, resolve names, classify aggregates, check function arity or types, or validate GROUP BY/aggregate consistency; a syntactically valid query naming an unknown table or column parses successfully and must be rejected by the planner. The parser therefore never needs the tables argument.\n\nWhat the planner receives, one node per source construct, built from nodes.py exactly as nodes.py defines those dataclasses (parser passes every field by keyword, so field order in nodes.py is free):\n- One Select for the whole statement, carrying: distinct flag (False when DISTINCT is absent), the select-list items in written order, the FROM table, the JOIN list in written order, WHERE condition or None, GROUP BY expression list (empty when absent), HAVING condition or None, ORDER BY key list (empty when absent), LIMIT int or None, OFFSET int or None. All sequences are tuples so nodes stay hashable.\n- One select-list item per comma-separated entry, holding the expression, the AS alias (or None when none was written; `expr alias` without AS is also accepted), and the exact source-text slice of the expression taken from the original sql string. The slice is required because NUMBER and STRING token values are already converted, so only the source text can produce the spec's output name for an unaliased expression (`price * 2` -> 'price*2', `SUM(qty)` -> 'SUM(qty)'). Output naming itself (alias, else bare column name, else whitespace-stripped source text) is applied by nodes.py's select-item helper or by the planner; the parser only guarantees alias and source_text are correct.\n- `SELECT *` and `SELECT t.*` are select-list items whose expression is the Star node with table None or the written table/alias name. The parser does not expand them; expansion in FROM/JOIN order is the planner's job.\n- Table references carry the written name and the optional alias; JOIN nodes carry the joined table plus the ON condition, which is never None because the grammar requires ON. INNER JOIN and bare JOIN produce identical nodes (INNER is the only kind in this dialect).\n- ORDER BY keys carry the expression and a descending flag (True only for DESC; ASC and an unwritten direction both give False). The expression may be a bare column reference that actually names an output alias; resolving alias-versus-column is the planner's job.\n\nExpression nodes: column references (qualifier None for `col`, the written qualifier for `t.col`/`alias.col`), literals (SQL NULL becomes the literal None; a doubled '' inside a string literal is already collapsed to one quote by the tokenizer), binary operations, unary operations, function calls, and Star.\n\nSpellings the planner and engine can rely on: operator words and function names are stored UPPER-CASED ('AND', 'OR', 'NOT', 'IN', 'LIKE', 'IS NULL', 'IS NOT NULL', 'UPPER', 'LOWER', 'LENGTH', 'ABS', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX'), while identifiers (table, alias, column names) keep their source case and are case-sensitive. Symbol operators are kept verbatim: '+', '-', '*', '/', '=', '<>', '!=', '<', '<=', '>', '>='; '!=' is not folded into '<>', so both must be treated as inequality. `x IS NULL` and `x IS NOT NULL` are unary operations, not comparisons with a NULL literal. `x IN (a, b, ...)` is a binary 'IN' whose right side is the tuple of item expressions in written order. A LIKE pattern is whatever expression was written on the right, normally a string literal. `COUNT(*)` is the COUNT function with a single Star argument.\n\nPrecedence, lowest to highest: OR, AND, NOT, then comparison/IS NULL/IN/LIKE, then + and -, then * and /, then unary -/+, then primaries (literal, column reference, function call, parenthesized expression). Binary operators of one level are left-associative; parentheses group anything.\n\nLIMIT/OFFSET: only a non-negative integer literal is accepted. A float or non-numeric token is a QueryError, and a negative literal such as `LIMIT -1` is a QueryError raised by the parser, so the planner will never see a negative value (a defensive planner check is harmless but unreachable). LIMIT and OFFSET may be written in either order; writing either twice is a QueryError.\n\nErrors always carry the offending token value and its source position, but the message text is not part of the contract; callers must branch on the QueryError type only. Any QueryError raised by tokens.tokenize (unterminated string, malformed number, illegal character) propagates unchanged."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/tokens.py: the `kind` vocabulary ('KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'OP', 'PUNCT') is not part of the published Token/tokenize interface, so the parser has nothing contractual to switch on -> publish the exact kind set, plus the rule that KEYWORD values are upper-cased while IDENT values keep their source case.
- minidb/tokens.py: `NULL` is emitted as a KEYWORD token rather than a literal, so the parser will not naturally build nodes.Literal(None) for it -> state in the interface that KEYWORD 'NULL' must be translated to nodes.Literal(None); note Token.value is in practice never None despite the annotation.
- minidb/tokens.py: tokenize() emits no end-of-input sentinel, so any parser lookahead past the last token raises IndexError, which the spec forbids from escaping the public API -> append a final Token('EOF', '', len(sql)), or require the parser to bounds-check every peek and api.py to wrap IndexError in QueryError.
- minidb/tokens.py: any word matching a keyword in any case becomes a KEYWORD token, so identifiers such as a column named `order`, `Count` or `in` can never be referenced even though the spec makes identifiers case-sensitive -> only treat words as keywords where the grammar expects them, or publish the reserved-word list so the parser can report it as a clear QueryError.
- minidb/tokens.py: _read_number accepts exponent forms (`1e5`, `2E-3`) that the spec's number grammar ('integers or floats') does not define, silently widening the dialect -> drop the exponent branch or document it as a deliberate superset.
- minidb/tokens.py: Token.value for NUMBER is the parsed int/float and no end offset is recorded, but output naming needs the original source spelling (`1.50 * qty` must be named '1.50*qty') -> let the parser slice the source itself for SelectItem.source_text (it has pos and the raw sql), otherwise nodes.SelectItem.output_name() cannot produce the specified name.
- minidb/errors.py: no contract problems. QueryError subclasses Exception with a docstring-only body, adds no attributes and imports nothing, matching its published interface; the conversion of builtin KeyError/TypeError promised in its docstring must be implemented by api.py's wrapper, not here.
- minidb/tokens.py: the token `kind` vocabulary actually emitted ("KEYWORD", "IDENT", "NUMBER", "STRING", "OP", "PUNCT") is nowhere in the published Token/tokenize interface, so the parser switches on undocumented strings -> publish the exact kind set, plus the rule that KEYWORD values are upper-cased while IDENT values keep source case, as part of the tokens contract.
- minidb/tokens.py: tokenize emits no end-of-input sentinel, so any parser lookahead past the last token raises IndexError, which the spec forbids from leaving the public API -> append a final Token("EOF", "", len(sql)) or state in the interface that the parser must bounds-check every peek and api.py must wrap IndexError as QueryError.
- minidb/tokens.py: NULL arrives as Token("KEYWORD", "NULL", pos), not as a literal token, while nodes.Literal documents SQL NULL as Python None -> document that the parser must translate KEYWORD "NULL" into nodes.Literal(None); otherwise NULL never reaches the engine as None and every IS NULL / NULL comparison silently changes meaning.

Return ONLY a JSON object:
{"path": "minidb/parser.py", "code": "<complete file contents>",
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