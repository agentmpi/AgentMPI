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
    "parse"
  ],
  "module": "parser",
  "notes": "Only `parse` is public; everything else in parser.py is private (leading underscore).\n\nparse() is purely syntactic: it does no name resolution, no table lookup, no aggregate classification and no type checking. Unknown tables/columns, ambiguous column names, aggregate/GROUP BY validation and mixed-type ordering are the planner's and engine's business, so parse() accepts them and the planner must reject them.\n\nAST returned (fields of the nodes.py dataclasses, in this order and always present):\n- Select(distinct: bool, items: list[SelectItem], from_: TableRef, joins: list[Join], where: expr|None, group_by: list[expr], having: expr|None, order_by: list[OrderKey], limit: int|None, offset: int|None). Absent clauses are None for where/having/limit/offset and an empty list for joins/group_by/order_by; `distinct` is False when DISTINCT is absent.\n- SelectItem(expr: expr, alias: str|None, name: str) - `name` is the output column name already computed per the specification's Output naming rules: the alias when one is given, the bare column name for `col`/`t.col`, otherwise the source text of the expression with all whitespace removed (e.g. `SUM(qty)`, `price*2`). `SELECT *` and `t.*` appear as a SelectItem whose expr is Star(table=None) or Star(table='t') with alias None and name '*' / 't.*'; the planner performs the expansion.\n- TableRef(name: str, alias: str|None); Join(table: TableRef, on: expr) - INNER JOIN and JOIN produce the same Join node, and `on` is never None because the grammar requires ON.\n- OrderKey(expr: expr, descending: bool) - descending is False when ASC or nothing is written; the expr may be a Column naming an output alias, which the planner resolves.\n\nExpression nodes produced: Column(table: str|None, name: str), Literal(value: int|float|str|None) (NULL becomes Literal(None); '' inside a single-quoted string is already unescaped to one quote), BinOp(op: str, left, right), UnaryOp(op: str, operand), Func(name: str, args: list[expr]), Star(table: str|None).\n\nOperator spellings in BinOp.op are normalized to lowercase for word operators and kept verbatim for symbols: 'and', 'or', '+', '-', '*', '/', '=', '<>', '!=', '<', '<=', '>', '>=', 'in', 'like'. UnaryOp.op is 'not', '-' or '+'. `x IS NULL` becomes UnaryOp('is null', x) and `x IS NOT NULL` becomes UnaryOp('is not null', x). For BinOp('in', x, rhs) the right operand is a Func('in_list', [items...]) holding the parenthesized item expressions in written order. `!=` is preserved as written rather than folded into `<>`; treat both as inequality.\n\nFunc.name is upper-cased, so scalar and aggregate lookups against functions.SCALARS / functions.AGGREGATES use 'UPPER', 'LOWER', 'LENGTH', 'ABS', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX'. `COUNT(*)` is Func('COUNT', [Star(None)]). The parser does not check argument counts or whether a name is a known function; the planner does.\n\nPrecedence implemented, lowest to highest: OR, AND, NOT, comparison/IS/IN/LIKE, + -, * /, unary +/-, then primary (literal, column, function call, parenthesized expression). Keywords are matched case-insensitively; identifiers keep their source case."
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

### tokens (minidb/tokens.py)
{
  "exports": [
    "Token",
    "tokenize"
  ],
  "module": "tokens",
  "notes": "Token kinds and value shapes, which the parser may rely on exactly:\n- \"KEYWORD\": value is the keyword uppercased. Keywords are matched case-insensitively and are exactly: SELECT, DISTINCT, FROM, AS, INNER, JOIN, ON, WHERE, GROUP, BY, HAVING, ORDER, ASC, DESC, LIMIT, OFFSET, AND, OR, NOT, IS, NULL, IN, LIKE. GROUP BY / ORDER BY arrive as two separate KEYWORD tokens (GROUP, BY) and (ORDER, BY). NULL arrives as KEYWORD \"NULL\"; the parser turns it into its NULL literal (there is no NULL token kind).\n- \"IDENT\": value is the identifier's source text with case preserved, because identifiers are case-sensitive. Function names are NOT keywords: UPPER, LOWER, LENGTH, ABS, COALESCE, COUNT, SUM, AVG, MIN, MAX all arrive as IDENT with their original spelling, and the parser should uppercase an IDENT that is immediately followed by PUNCT \"(\" when looking it up in functions.SCALARS / functions.AGGREGATES.\n- \"NUMBER\": value is an already-converted int (no '.', no exponent) or float. Only unsigned numeric text is lexed; a leading '-' is a separate OP token, so unary minus is the parser's job.\n- \"STRING\": value is the decoded str contents without the surrounding single quotes, with each doubled '' already collapsed to one '. An unterminated literal is a QueryError.\n- \"OP\": value is exactly one of \"=\", \"<>\", \"!=\", \"<\", \"<=\", \">\", \">=\", \"+\", \"-\", \"*\", \"/\". Longest match wins, so \"<=\" and \"<>\" are never split. \"=\" is only ever one character.\n- \"PUNCT\": value is exactly one of \"(\", \")\", \",\", \".\".\nThe asterisk is always OP \"*\": SELECT *, t.*, and COUNT(*) are distinguished by the parser from context, not by a distinct token kind.\nNo end-of-input sentinel token is appended; the parser must treat exhaustion of the list as end of input and report a truncated query as a QueryError itself.\nA table/column qualification arrives as three tokens: IDENT, PUNCT \".\", IDENT (or IDENT, PUNCT \".\", OP \"*\").\nOutput naming support: `pos` is a real index into the original `sql`, so the parser can recover the source text of an expression by slicing `sql` from the first token's `pos` to the `pos` of the token after the expression (or to len(sql) at end of input) and removing whitespace. This is necessary because NUMBER and STRING values are converted, so token values alone do not reproduce the source spelling.\nErrors: every failure is raised as minidb.errors.QueryError with a message that includes the offending character or literal and its position; tokenize performs no syntactic validation beyond lexing, so keyword order, balanced parentheses and clause structure are entirely the parser's responsibility."
}


PEER REVIEW OF YOUR MODULE:
- minidb/tokens.py: the `kind` vocabulary ('KEYWORD', 'IDENT', 'NUMBER', 'STRING', 'OP', 'PUNCT') is not part of the published Token/tokenize interface, so the parser has nothing contractual to switch on -> publish the exact kind set (and that KEYWORD values are upper-cased while IDENT values keep source case) as part of tokens.py's interface.
- minidb/tokens.py: `NULL` is emitted as a KEYWORD token, not as a literal token, so a naive parser will not build nodes.Literal(None) for it -> document that the parser must translate KEYWORD 'NULL' into nodes.Literal(None); Token.value is never None as the annotation suggests.
- minidb/tokens.py: tokenize() emits no end-of-input sentinel and the list is returned bare, so any parser lookahead past the last token raises IndexError, which the spec forbids escaping the public API -> append a final Token('EOF', '', len(sql)) (preferred) or state in the interface that the parser must bounds-check every peek and api.py must wrap IndexError in QueryError.
- minidb/tokens.py: every word matching a keyword in any case becomes a KEYWORD token, so identifiers such as a column named `order`, `Count` or `in` can never be referenced even though the spec makes identifiers case-sensitive -> either only treat words as keywords in positions where the grammar expects them, or publish the reserved-word list as a documented limitation so the parser reports it as QueryError rather than a confusing syntax error.
- minidb/tokens.py: _read_number accepts exponent forms (`1e5`, `2E-3`) that the spec's number grammar ('integers or floats') does not define, silently widening the accepted dialect -> either drop the exponent branch or note it as a deliberate superset so the parser/planner do not assume NUMBER text round-trips to the source spelling used for output naming.
- minidb/tokens.py: _read_number returns int/float in Token.value while the naming rule needs the original source spelling (an unaliased `1.50 * qty` must be named '1.50*qty'), and pos alone is not enough without the token's end offset -> keep the raw source slice available (e.g. a Token whose value is the parsed number plus the parser slicing sql itself), otherwise SelectItem.source_text cannot be reconstructed and nodes.SelectItem.output_name() will produce the wrong name.
- minidb/errors.py: QueryError defines no custom __init__ or attributes, which is exactly the published contract, but the module docstring promises it covers 'type errors' while nothing in errors.py converts builtin TypeError/KeyError -> keep errors.py as is and make api.py's wrapper the single place that converts unexpected builtin exceptions into QueryError.
- minidb/tokens.py: the token `kind` vocabulary actually emitted ("KEYWORD", "IDENT", "NUMBER", "STRING", "OP", "PUNCT") is not part of the published tokens interface, so the parser is coding against undocumented strings and one rename breaks it silently -> publish the exact kind set (and that KEYWORD values are upper-cased while IDENT values keep source case) as part of the tokens contract.
- minidb/tokens.py: tokenize appends no end-of-input sentinel token, so a parser that peeks past the last token gets IndexError, which the spec forbids from escaping the public API and which api.py can only blanket-wrap -> either append a final Token("EOF", None, len(sql)) or have the tokens interface state explicitly that end-of-input detection is the parser's job.
- minidb/tokens.py: `-` is only ever emitted as OP, and nodes.Select.limit/offset are typed `int | None`, so `LIMIT -1` cannot be represented as parsed; a UnaryOp there would be a type mismatch for the planner's negative-LIMIT check -> have the parser fold a leading minus into the integer it stores in Select.limit/Select.offset so the planner sees -1 and raises QueryError, and never a node.

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