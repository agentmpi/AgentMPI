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
    "Expr = Literal | ColumnRef | Star | UnaryOp | BinaryOp | FuncCall | IsNull | InList | Like - module-level type alias covering every expression node; annotation only, raises nothing.",
    "Node = Expr | SelectItem | TableRef | JoinClause | OrderItem | SelectStmt - type alias covering every AST node; annotation only, raises nothing.",
    "@dataclass(frozen=True, slots=True) class Literal: value: int | float | str | None -> Literal(value) - a literal; strings arrive with '' escapes already resolved, NULL is Literal(None). Constructor raises nothing.",
    "@dataclass(frozen=True, slots=True) class ColumnRef: table: str | None; name: str -> ColumnRef(table, name) - `col` is ColumnRef(None, 'col'), `t.col` is ColumnRef('t', 'col'); table is the written table name or alias, both case-sensitive. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class Star: table: str | None = None -> Star(table) - `*` is Star(None), `t.*` is Star('t'); only ever appears as SelectItem.expr, never nested in another expression. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class UnaryOp: op: str; operand: Expr -> UnaryOp(op, operand) - op is exactly one of 'NOT', '-', '+'. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class BinaryOp: op: str; left: Expr; right: Expr -> BinaryOp(op, left, right) - op is exactly one of '+', '-', '*', '/', '=', '<>', '<', '<=', '>', '>=', 'AND', 'OR'; the parser normalises '!=' to '<>'. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class FuncCall: name: str; args: tuple[Expr, ...] = (); star: bool = False -> FuncCall(name, args, star) - name is upper-cased by the parser; COUNT(*) is FuncCall('COUNT', (), True). Raises nothing; an unknown name is the planner's QueryError, not this module's.",
    "@dataclass(frozen=True, slots=True) class IsNull: operand: Expr; negated: bool = False -> IsNull(operand, negated) - `x IS NULL` is negated=False, `x IS NOT NULL` is negated=True. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class InList: operand: Expr; items: tuple[Expr, ...] -> InList(operand, items) - `x IN (a, b, ...)`; items is never empty in a well-formed parse. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class Like: operand: Expr; pattern: Expr -> Like(operand, pattern) - `x LIKE 'pat'`; pattern is normally Literal(str). Raises nothing.",
    "@dataclass(frozen=True, slots=True) class SelectItem: expr: Expr; alias: str | None = None; source_text: str = '' -> SelectItem(expr, alias, source_text) - one select_list entry; source_text is the entry's expression source with all whitespace removed, as required by Output naming. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class TableRef: name: str; alias: str | None = None -> TableRef(name, alias) - a FROM/JOIN table with optional alias. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class JoinClause: table: TableRef; condition: Expr -> JoinClause(table, condition) - one INNER JOIN with its ON condition; condition is never None. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class OrderItem: expr: Expr; descending: bool = False -> OrderItem(expr, descending) - one ORDER BY key; ASC/absent is descending=False. Raises nothing.",
    "@dataclass(frozen=True, slots=True) class SelectStmt: items: tuple[SelectItem, ...]; source: TableRef; distinct: bool = False; joins: tuple[JoinClause, ...] = (); where: Expr | None = None; group_by: tuple[Expr, ...] = (); having: Expr | None = None; order_by: tuple[OrderItem, ...] = (); limit: int | None = None; offset: int | None = None -> SelectStmt(items, source, distinct, joins, where, group_by, having, order_by, limit, offset) - the whole parsed query, the parser's single return value. Raises nothing.",
    "AGGREGATE_FUNCTIONS: frozenset[str] = frozenset({'COUNT', 'SUM', 'AVG', 'MIN', 'MAX'}) - upper-case aggregate names, for planner classification.",
    "SCALAR_FUNCTIONS: frozenset[str] = frozenset({'UPPER', 'LOWER', 'LENGTH', 'ABS', 'COALESCE'}) - upper-case scalar names.",
    "def is_aggregate_call(expr: Expr) -> bool - True iff expr is a FuncCall whose name is in AGGREGATE_FUNCTIONS. Raises nothing.",
    "def contains_aggregate(expr: Expr) -> bool - True iff expr is, or contains anywhere beneath it, an aggregate FuncCall. Raises ValueError only if handed an object that is not a Node.",
    "def children(node: Node) -> tuple[Node, ...] - the node's direct child nodes in source order (empty for leaves such as Literal, ColumnRef, Star). Raises ValueError if node is not a Node.",
    "def walk(node: Node) -> Iterator[Node] - pre-order iterator over node and every descendant node. Raises ValueError if node is not a Node.",
    "def expr_source(expr: Expr) -> str - canonical source rendering of expr with all whitespace removed, per the Output naming rule: SUM(qty) -> 'SUM(qty)', price * 2 -> 'price*2', t.col -> 't.col', a IS NOT NULL -> 'aISNOTNULL'. Raises ValueError if expr is not an Expr.",
    "def output_name(item: SelectItem) -> str - the Output naming result for one select item: item.alias if set, else item.expr.name if expr is a ColumnRef, else item.source_text if non-empty, else expr_source(item.expr). Raises ValueError if item.expr is Star (a Star item has no single output name and must be expanded by the engine first)."
  ],
  "module": "nodes",
  "notes": "nodes.py imports stdlib only, so it never raises minidb.QueryError and performs no validation: every construction succeeds and all user-facing errors (unknown function, ambiguous column, negative LIMIT, mixed-type ORDER BY, malformed SQL) belong to tokens/parser/planner/engine. The ValueError cases listed above signal an internal bug, not bad user SQL; api.py should not convert them into QueryError silently.\n\nAll node classes are @dataclass(frozen=True, slots=True): immutable, hashable and comparable by value, so they can be used as dict keys (useful for GROUP BY expression matching). Every sequence field is a tuple, not a list - build tuples when constructing nodes.\n\nConstruction is positional in the field order published above, and keyword construction works with those exact field names.\n\nCase handling: FuncCall.name and the op strings for NOT/AND/OR are upper-case; '!=' is normalised to '<>' by the parser so consumers only handle '<>'. Identifiers (ColumnRef.table, ColumnRef.name, TableRef.name, TableRef.alias, SelectItem.alias, Star.table) keep their exact source case.\n\nShapes a consumer can rely on: SelectStmt.source is always present (FROM is mandatory); joins/group_by/order_by/items are tuples, possibly empty except items which is non-empty; where/having are None when absent; limit/offset are None when absent and are whatever integer the parser read, including negatives, so the planner must reject negatives. `SELECT *` is items=(SelectItem(Star(None)),) and `t.*` is SelectItem(Star('t')); Star never appears anywhere else, so expression evaluators need no Star case. ORDER BY may reference an output alias, which arrives as an ordinary ColumnRef(None, alias) - resolving it against output names is the planner's job.\n\nOutput naming helpers: the parser fills SelectItem.source_text with the whitespace-stripped source text, and output_name() implements the spec rule on top of it; expr_source() is available for callers that need a name for a bare Expr with no SelectItem. Prefer output_name() so naming stays consistent across planner and engine."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
(this module has no dependencies)


PEER REVIEW OF YOUR MODULE:
- minidb/nodes.py: the helpers signal caller bugs with ValueError (children/walk/expr_source on a non-node, output_name on an aliasless Star item, i.e. every `SELECT *` item), so a planner/engine slip surfaces as ValueError rather than QueryError -> expand Star items before calling output_name, and wrap helper calls at the planner/engine boundary with `except ValueError as exc: raise QueryError(...) from exc` instead of relying on api.py's catch-all.
- minidb/nodes.py: expr_source renders the normalised operator and repr'd numbers, so an unaliased `a != b` is named "a<>b" and `1.50*2` is named "1.5*2", contradicting the Output naming rule (source text with whitespace removed) whenever SelectItem.source_text is empty -> the parser must always populate SelectItem.source_text from the raw SQL slice; treat expr_source only as a debug rendering, never as an output name.
- minidb/nodes.py: AGGREGATE_FUNCTIONS/SCALAR_FUNCTIONS duplicate the key sets of functions.AGGREGATES/functions.SCALARS, so the two can drift and a name present only in nodes' frozenset would hit the engine as a KeyError on my dicts -> classify and validate function names with `name.upper() in SCALARS` / `in AGGREGATES` from functions.py (nodes' sets advisory only), and never index either dict without that membership test.
- minidb/nodes.py: FuncCall.name preserves the source spelling (`sum`, `Count`), while my published dicts are keyed by upper-case name only -> always look up SCALARS[name.upper()] / AGGREGATES[name.upper()]; is_aggregate_call already upper-cases, so the engine must do the same rather than passing FuncCall.name through.
- minidb/nodes.py: COUNT(*) is FuncCall("COUNT", (), True) and carries no argument expression -> for star=True the engine must call AGGREGATES["COUNT"] with one non-None placeholder per group row (e.g. [1] * len(group_rows)); an empty sequence would report 0 for every group, and a sequence containing Nones would undercount.
- minidb/nodes.py: the root class is SelectStmt, but the parser interface every module was given promises `parse(sql: str) -> Select`; nothing named Select exists here, so `from .nodes import Select` is an ImportError -> add `Select = SelectStmt` (and export it) or rename the class to Select.
- minidb/nodes.py: OrderItem exposes `descending`, while the published parser interface says ORDER BY items carry an explicit *ascending* flag; a planner/engine reading `item.ascending` gets AttributeError -> settle on one field; if nodes keeps `descending`, dependents must read `not item.descending` and the parser must fill `descending`.
- minidb/nodes.py: SelectItem has no field for the computed output name, although the parser published that it attaches the output name to every select item; consumers are forced through `output_name(item)`, which raises ValueError (not QueryError) for a Star item -> add `output_name: str | None = None` to SelectItem, or require callers to expand Star items first and wrap the ValueError.
- minidb/nodes.py + minidb/parser.py: no interface for nodes.py or tokens.py was published to the parser owner in this build, so parser.py defines its own AST classes and lexer; the tree the planner and engine receive is therefore not made of nodes.py's classes, and children()/walk()/contains_aggregate()/expr_source() will take their `raise ValueError("not a minidb AST node")` path on every real query -> reconcile on one AST before integration: publish nodes.py's classes to the parser, or have planner/engine import the node classes parser.py exports.
- minidb/nodes.py: `_literal_source` renders numbers with repr, so `1e100` becomes `1e+100` and would not match the whitespace-stripped source text; harmless while the parser always fills SelectItem.source_text, but any caller that names an expression via `expr_source` alone can disagree with the parser -> derive names from output_name(item) only.

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