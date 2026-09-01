Implement exactly one file of the `minidb` system: `minidb/planner.py` (plan() -> Plan).

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
    "def plan(statement: Any, tables: dict[str, list[dict]]) -> Plan - resolves and validates the parsed SELECT statement (the AST root produced by parser) against `tables` and returns a fully resolved, engine-ready Plan; raises minidb.errors.QueryError for unknown table, unknown column, ambiguous unqualified column, duplicate table alias, unknown scalar/aggregate function or wrong arity, aggregate used in WHERE or in an ON condition, a non-aggregate select/HAVING/ORDER BY expression that is not one of the GROUP BY expressions, and negative LIMIT/OFFSET. It never raises KeyError, IndexError, AttributeError or TypeError.",
    "@dataclass(frozen=True) class TableRef: name: str; alias: str - one entry in FROM/JOIN order; `alias` equals `name` when no alias was written.",
    "@dataclass(frozen=True) class SelectItem: name: str; expr: Expr - one output column: `name` is the final output name (already computed per the Output naming rules, including `*`/`table.*` expansion) and `expr` is the resolved expression to evaluate.",
    "@dataclass(frozen=True) class OrderItem: expr: Expr; descending: bool - one ORDER BY key; `descending` is True for DESC.",
    "@dataclass(frozen=True) class AggregateCall: func: str; arg: Expr | None - one aggregate occurrence; `func` is one of 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX' (uppercased) and `arg` is None exactly for COUNT(*).",
    "@dataclass(frozen=True) class Plan: from_tables: tuple[TableRef, ...]; join_conditions: tuple[Expr, ...]; select: tuple[SelectItem, ...]; where: Expr | None; group_by: tuple[Expr, ...]; having: Expr | None; order_by: tuple[OrderItem, ...]; distinct: bool; limit: int | None; offset: int | None; is_aggregate: bool; aggregates: tuple[AggregateCall, ...] - the validated query; `join_conditions[i]` is the ON condition joining `from_tables[i + 1]` to everything before it, so len(join_conditions) == len(from_tables) - 1.",
    "@dataclass(frozen=True) class Literal: value: object - a constant; NULL is represented as Python None.",
    "@dataclass(frozen=True) class Column: alias: str; name: str - a resolved column reference, always fully qualified to a table alias in Plan.from_tables.",
    "@dataclass(frozen=True) class Unary: op: str; operand: Expr - op is '-' (arithmetic negation) or 'NOT'.",
    "@dataclass(frozen=True) class Binary: op: str; left: Expr; right: Expr - op is one of '+', '-', '*', '/', '=', '<>', '<', '<=', '>', '>=', 'AND', 'OR'; '!=' is normalised to '<>'.",
    "@dataclass(frozen=True) class Func: name: str; args: tuple[Expr, ...] - a scalar call, name uppercased, one of 'UPPER', 'LOWER', 'LENGTH', 'ABS', 'COALESCE'; arity is already validated (1 for the first four, >= 1 for COALESCE).",
    "@dataclass(frozen=True) class IsNull: operand: Expr; negated: bool - `x IS NULL` (negated False) or `x IS NOT NULL` (negated True).",
    "@dataclass(frozen=True) class InList: operand: Expr; items: tuple[Expr, ...] - `x IN (a, b, ...)`; items is never empty.",
    "@dataclass(frozen=True) class Like: operand: Expr; pattern: Expr - `x LIKE 'pat'`; the pattern is kept as an expression (normally a Literal str).",
    "@dataclass(frozen=True) class AggRef: index: int - a placeholder standing for Plan.aggregates[index]; the engine computes that aggregate for the current group and substitutes its value here.",
    "Expr = Literal | Column | Unary | Binary | Func | IsNull | InList | Like | AggRef - the resolved expression union; all variants are frozen dataclasses, so they support == and hash and can be used as dict keys."
  ],
  "module": "planner",
  "notes": "Row context: the engine evaluates a Column(alias, name) by looking the value up in the current joined row keyed by table alias, i.e. row[alias][name]; a key missing from an individual source dict must be treated as NULL (planner validates column names only against a table's first row, per the spec's column-order rule).\nEmpty tables: a table whose row list is empty has no known columns, so column references into it are accepted without validation; such a query yields zero rows anyway (or, for a bare aggregate query, the single zero-input aggregate row).\nAggregates: every aggregate occurrence anywhere in the query (select list, HAVING, ORDER BY) is collected once into Plan.aggregates and replaced in place by AggRef(index). Identical aggregate calls are deduplicated, so two AggRefs may share an index. Plan.is_aggregate is True when Plan.aggregates is non-empty or Plan.group_by is non-empty; when is_aggregate is True and group_by is empty the result is exactly one row even over zero input rows. Aggregate arguments contain no nested aggregates.\nGrouping: Plan.group_by holds the resolved grouping expressions in written order. Planner has already checked that every non-aggregate expression appearing in the select list, HAVING and ORDER BY is structurally equal (dataclass ==) to one of them; the engine may therefore evaluate a grouping expression once per group.\nORDER BY aliases: an ORDER BY item naming an output alias or output column name has already been rewritten to that SelectItem's expr, so the engine never needs the alias table.\nStar expansion: `*` and `table.*` are expanded by the planner into explicit SelectItems in FROM/JOIN order and, within a table, first-row key order; on a name collision the later table's column wins, so the earlier duplicate SelectItem is dropped rather than emitted twice.\nLIMIT/OFFSET: already validated as non-negative ints; None means absent.\nErrors: planner raises only minidb.errors.QueryError. Runtime-only errors (mixed-type comparison in ORDER BY, type errors inside scalar functions, division by zero yielding NULL) are not the planner's responsibility and are left to functions/engine.\nPlanner imports only minidb.nodes, minidb.errors and the standard library; the Plan and Expr types above are defined in minidb/planner.py, so the engine needs no import of minidb.nodes."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
### errors (minidb/errors.py)
{
  "exports": [
    "class QueryError(Exception) - the single error type of the whole minidb system; raised (never caught) by this module's definition itself, and raised by every other module for a malformed query, unknown table, unknown column, ambiguous column, misuse of an aggregate, negative LIMIT/OFFSET, or any value/type error met while planning or evaluating a query. It defines no methods of its own and adds no attributes.",
    "QueryError(*args: object) -> QueryError - inherited Exception constructor, unchanged: callers use QueryError(\"message\") with a single human-readable string, so str(exc) is that message and exc.args == (message,). Raises nothing itself.",
    "__all__: list[str] = [\"QueryError\"] - the module exports this name and nothing else."
  ],
  "module": "errors",
  "notes": "QueryError derives directly from Exception, NOT from ValueError/KeyError/LookupError/TypeError, so `except QueryError` cannot accidentally swallow a genuine bug and `except (KeyError, IndexError, AttributeError, TypeError)` in api.py will never catch it. Import it as `from .errors import QueryError` (this module imports nothing at all, not even from the standard library, so it can never take part in an import cycle). Construction convention every module should follow: exactly one positional argument, a short lowercase message describing the problem, optionally with the offending name quoted (e.g. QueryError(\"unknown column 'qty'\")); do not pass extra positional args, since nothing parses exc.args beyond args[0], and do not rely on any structured field such as .message, .position or .code, because none exist and none will be added. Message text is not part of the contract: dependent modules and tests must match on the type QueryError, never on the wording. There are no subclasses of QueryError and none may be added elsewhere; a module needing to distinguish failure kinds must do so by its own control flow, not by exception class. api.py is expected to let QueryError propagate unchanged and to wrap any other exception in a new QueryError, so QueryError is the only exception type that ever escapes the public API. Chaining with `raise QueryError(...) from exc` is permitted and expected where a stdlib error is being translated; the original stays reachable via __cause__ but is not part of the contract."
}

### nodes (minidb/nodes.py)
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


Return ONLY a JSON object:
{"path": "minidb/planner.py", "code": "<complete file contents>",
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