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
    "def parse(sql: str) -> Select - Tokenizes `sql` (via `minidb.tokens`) and parses the complete statement into the syntax tree defined by `minidb.nodes`, returning the root `Select` node. Accepts exactly the SQL surface of the specification (SELECT [DISTINCT] select_list FROM table [alias] ([INNER] JOIN table [alias] ON condition)* [WHERE] [GROUP BY] [HAVING] [ORDER BY] [LIMIT] [OFFSET]). Raises `minidb.errors.QueryError` for any lexical or grammatical problem: empty input, unknown/misplaced keyword, unterminated string literal, unbalanced parentheses, unknown function name, wrong arity for a known function, missing FROM, missing ON for a JOIN, a non-integer or negative LIMIT/OFFSET, or any trailing text after the end of the statement. It never propagates `KeyError`, `IndexError`, `AttributeError`, or `TypeError`."
  ],
  "module": "parser",
  "notes": "Scope: `parse` is purely syntactic. It performs no name resolution, no ambiguity detection, no aggregate/grouping classification, and no type checking; those are the planner's and engine's responsibility. It does not touch table data and takes no `tables` argument, so it cannot detect unknown tables or columns.\n\nReturn value: the root node of the tree published by `minidb.nodes` (the `Select` node type). The tree is freshly constructed on every call, contains no references to parser state, and `parse` has no global or cached state, so it is reentrant. Dependents should treat the tree as read-only unless `minidb.nodes` documents otherwise.\n\nErrors: `minidb.errors.QueryError` is the only exception type raised. Messages are human-readable and unspecified in content; do not match on them.\n\nLexical decisions already applied, so downstream modules receive normalized data:\n- Keywords and function names are recognized case-insensitively; identifiers (table, alias, column names) are preserved verbatim, case-sensitive.\n- String literals are unescaped: `''` inside a single-quoted literal becomes one `'`. A literal's value is a Python `str`.\n- Numeric literals become Python `int` when they have no fractional part or exponent, otherwise `float`.\n- `NULL` is parsed as a literal whose value is Python `None`.\n- Comparison operators `!=` and `<>` are both accepted and are represented identically (as `<>`-equivalent inequality); `=` is equality.\n- `IS NULL` / `IS NOT NULL`, `IN (...)`, `LIKE 'pat'`, `AND`, `OR`, `NOT` are parsed as condition constructs of the node tree, not as generic binary operators over strings.\n\nPrecedence implemented (loosest to tightest): OR, AND, NOT, comparison/IS/IN/LIKE, `+ -`, `* /`, unary `-`/`+`, then primary (literal, column reference, function call, parenthesized expression). Parentheses override this.\n\nOutput naming: the parser is the only module that sees source text, so it computes and attaches the output name of every select item per the specification -- an explicit `AS alias` or bare alias wins; a bare column reference `col` or `t.col` is named `col`; any other unaliased expression is named by its source text with all whitespace removed (e.g. `SUM(qty)`, `price*2`). Dependents must use the name carried on the select item rather than re-deriving it. `*` and `table.*` are represented as star select items (no name); their expansion is the engine's job.\n\nORDER BY items carry their expression plus an explicit ascending flag, defaulting to ascending when neither ASC nor DESC is written. An ORDER BY expression that is a bare name may refer to an output alias; the parser emits it as an ordinary column reference and leaves alias-versus-column resolution to the planner.\n\nAbsent optional clauses are represented as the empty/None form of the corresponding node field rather than being omitted, so a `Select` always exposes every clause slot. `LIMIT`/`OFFSET`, when present, are non-negative Python ints."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/parser.py: OrderItem publishes `ascending: bool = True`, but the planner's published OrderItem and every downstream consumer read a `descending` flag; a planner probing `descending`/`desc` sees no field and silently plans every ORDER BY as ASC, so `ORDER BY b DESC` returns ascending rows. -> rename the field to `descending: bool = False` (or add a `descending` property).
- minidb/parser.py: the published export list names `Expr`, `KEYWORDS` and `__all__`, but the module's `__all__` contains neither `Expr` nor `KEYWORDS`. -> add both to `__all__`; `__all__` itself is not a public export and should be dropped from the published list.
- minidb/parser.py: the module defines the whole AST itself and names the root `Select`, while the module table assigns the AST to nodes.py and the planner was published as taking `statement: Any`, the parser's root. This only works if planner and engine consume parser's classes and nodes.py is unused. -> state in parser's interface that these node types supersede nodes.py, and keep the root name `Select` frozen.
- minidb/parser.py: SelectItem carries `output_name` and its docstring says a star item 'must be expanded by the engine', which contradicts the planner contract, where Plan.select is already star-expanded, collision-resolved (later table wins) and named. -> the engine must not expand Star nor recompute names; treat parser's `output_name` as advisory and let the planner own naming.
- minidb/parser.py: any identifier equal to a keyword is tokenised as KEYWORD before the grammar is consulted, so a column or table literally named `order`, `in`, `is`, `by` or `on` can never be referenced even though identifiers are user data. -> only treat a word as a keyword where the grammar expects one, or accept KEYWORD tokens in identifier positions.
- minidb/parser.py: `_advance()` deliberately does not move past EOF, so any parse loop that expects progress on unexpected input spins forever instead of raising. -> make the parser raise QueryError('unexpected end of query') when it is asked to consume at EOF.
- minidb/parser.py: `UnaryOp.op` is documented as 'NOT', '-' or '+', but planner's published `Unary.op` is only '-' or 'NOT', so a unary '+' has no Plan representation and reaches the engine as an unsupported operator -> fold unary '+' into its operand in the parser (it is a no-op on numbers), or have planner drop it; do not pass '+' through as a Unary.
- minidb/parser.py: SelectItem's docstring says `output_name` is None for a star item "which the engine must expand itself". That contradicts planner's contract (Plan.select carries final `name: str`, stars already expanded) and engine's published interface, which never sees Star nodes and raises QueryError on a select item with no output name -> keep star expansion in planner (it already has `_is_star`/`_star_qualifier`) and reword the docstring; parser should still expose the qualifier so planner can expand it.
- minidb/parser.py: published exports include `Expr` and `KEYWORDS`, but `__all__` omits both (it lists neither, while including `Star`) -> add `Expr` and `KEYWORDS` to `__all__` so a star-import matches the published interface; alternatively drop them from the published export list.

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