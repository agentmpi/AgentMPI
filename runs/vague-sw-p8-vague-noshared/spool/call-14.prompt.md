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
    "KEYWORDS: frozenset[str] - module-level constant holding the reserved words, all uppercase: SELECT, DISTINCT, FROM, INNER, JOIN, ON, WHERE, GROUP, HAVING, ORDER, BY, ASC, DESC, LIMIT, OFFSET, AS, AND, OR, NOT, IS, IN, LIKE. Read-only; raises nothing.",
    "@dataclasses.dataclass(frozen=True, slots=True)\nclass Token:\n    kind: str\n    value: object\n    text: str\n    pos: int - one lexical token. `kind` is one of 'KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'NULL', 'OP', 'PUNCT', 'EOF'. `value` is the semantic value (see notes). `text` is the exact source text of the token (empty string for 'EOF'). `pos` is the 0-based index in the input SQL where the token starts (len(sql) for 'EOF'). Hashable and immutable; construction raises nothing beyond ordinary dataclass TypeError on wrong arity.",
    "def Token.is_keyword(self, *names: str) -> bool - True when self.kind == 'KEYWORD' and self.value equals one of `names` compared case-insensitively (names may be given in any case); with no names given, True for any KEYWORD token. Never raises.",
    "def tokenize(sql: str) -> list[Token] - lexes the whole SQL string and returns the tokens in source order, always terminated by exactly one Token(kind='EOF', value=None, text='', pos=len(sql)). Whitespace (space, tab, newline, carriage return, form feed) separates tokens and is discarded. Raises minidb.errors.QueryError for an unterminated string literal, a malformed number (e.g. '1.2.3', '1e', trailing '.' as in '1.'), or any character that cannot begin a token; the message includes the offending character and its position. Raises nothing else: no KeyError, IndexError, AttributeError or TypeError escapes it. A non-str argument raises QueryError as well."
  ],
  "module": "tokens",
  "notes": "Values per kind:\n- 'KEYWORD': value is the word upper-cased (keywords are case-insensitive); `text` keeps the original casing. A word that matches KEYWORDS case-insensitively is ALWAYS emitted as KEYWORD, never as IDENT, so a table/column literally named e.g. 'order' will arrive as a KEYWORD token; use `text` if you choose to accept it as an identifier.\n- 'IDENT': value is the identifier verbatim (identifiers are case-sensitive). Function names (UPPER, LOWER, LENGTH, ABS, COALESCE, COUNT, SUM, AVG, MIN, MAX) are NOT keywords; they arrive as IDENT and must be recognised by the parser case-insensitively, followed by a PUNCT '(' token.\n- 'NUMBER': value is an int when the literal has no fraction and no exponent, otherwise a float. Only unsigned literals are produced: a leading '-' or '+' is a separate OP token, so unary sign is the parser's job. Accepted forms: digits, digits '.' digits, with optional exponent 'e'/'E' with optional sign. A literal may not start with '.'.\n- 'STRING': value is the decoded str with the doubled-quote escape resolved ('it''s' -> \"it's\"); `text` is the raw literal including its quotes.\n- 'NULL': the word NULL, matched case-insensitively, gets its own kind with value None. NULL is deliberately NOT in KEYWORDS, so `is_keyword` is false for it; test `tok.kind == 'NULL'`.\n- 'OP': value is the canonical operator string, one of '=', '<>', '<', '<=', '>', '>=', '+', '-', '*', '/'. '!=' is normalised to value '<>' while `text` stays '!='. Longest match wins, so '<=' and '>=' never split.\n- 'PUNCT': value is one of '(', ')', ',', '.' (identical to `text`). '.' is emitted for qualified names such as t.col; it is never part of a NUMBER.\n- 'EOF': exactly one, always last; value None.\n\nOther invariants a dependent module must know:\n- '*' has no dedicated kind: both the SELECT-list star (including the star in 't.*' and 'COUNT(*)') and the multiplication operator are OP with value '*'. Disambiguation is the parser's responsibility by position.\n- There is no comment syntax; '--' lexes as two OP '-' tokens.\n- Semicolons are not accepted and raise QueryError, so a trailing ';' is an error.\n- The token list never contains whitespace or None entries, and `tokenize('')` returns exactly [EOF].\n- `pos` values are strictly increasing, and `sql[tok.pos:tok.pos + len(tok.text)] == tok.text` for every non-EOF token. This lets the parser recover the source text of an expression for output naming: concatenate the `text` of the expression's tokens (they carry no whitespace), or slice the original SQL between the first token's `pos` and the last token's `pos + len(text)` and strip whitespace.\n- The only import is `minidb.errors` (for QueryError) plus the standard library; tokenize performs no validation of table or column names."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


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

Each module is owned by exactly one implementer. Do not create files you do not own,
and do not modify files you do not own.

| module | responsibility | may import |
| --- | --- | --- |
| `minidb/errors.py` | the error type this system raises | stdlib only |
| `minidb/tokens.py` | turning SQL text into a sequence of tokens | `errors` |
| `minidb/nodes.py` | the abstract syntax tree representation | stdlib only |
| `minidb/parser.py` | turning tokens into a syntax tree | `tokens`, `nodes`, `errors` |
| `minidb/functions.py` | scalar and aggregate function behaviour, pattern matching, value comparison | `errors` |
| `minidb/planner.py` | name resolution, aggregate classification, validation | `nodes`, `errors` |
| `minidb/engine.py` | evaluating a validated query against tables | `planner`, `functions`, `errors` |
| `minidb/api.py` | the public `query` entry point that ties the above together | all of the above |

`minidb/__init__.py` is provided by the harness and re-exports `query` and
`QueryError`; do not write it.

The exported names and signatures of each module are **not specified here**. They are
yours to choose and to publish, and the modules that depend on you must be written
against what you publish. Do not guess at another module's interface: use the
interface that module has published, and if it is insufficient, work within it and say
so rather than inventing a different signature for someone else's module.

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