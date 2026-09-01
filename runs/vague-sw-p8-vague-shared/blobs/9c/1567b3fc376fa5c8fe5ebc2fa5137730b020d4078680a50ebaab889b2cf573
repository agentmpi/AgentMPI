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
    "def query(sql: str, tables: dict[str, list[dict]]) -> list[dict] - THE ONLY public entry point; tokenizes, parses, plans/validates and evaluates `sql` against `tables`, returning the result rows as a list of dicts (one per output row, in result order, keys are the output column names per the spec's Output naming rules). Raises minidb.errors.QueryError for any malformed query, unknown table, unknown column, ambiguous unqualified column, type error, mixed-type ORDER BY comparison, or negative LIMIT/OFFSET. It never lets KeyError, IndexError, AttributeError, TypeError, ZeroDivisionError, ValueError, RecursionError or any other non-QueryError exception escape: such an escape is caught and re-raised as QueryError."
  ],
  "module": "api",
  "notes": "SIGNATURE IS FIXED BY THE SPECIFICATION and is exactly `def query(sql: str, tables: dict[str, list[dict]]) -> list[dict]`. `minidb/__init__.py` is written by the harness and re-exports `query` from this module and `QueryError` from `minidb/errors.py`, so api must expose the module-level name `query` with exactly that spelling. `query` is the single public name of this module; everything else in api.py is private (leading underscore) and must not be depended upon.\n\nWHAT api DOES AND DOES NOT DO. api.py is a thin orchestrator only. It contains no lexing, parsing, planning, or evaluation logic. Its whole body is: (1) shallowly validate the two arguments, (2) call tokens -> parser -> planner -> engine in that order, (3) return the engine's rows unchanged, (4) guarantee the error contract. Any behaviour question about SQL surface, NULL three-valued logic, aggregates, ordering, joins, DISTINCT, LIMIT/OFFSET or output naming is owned by parser/planner/engine, not by api; api adds nothing to and removes nothing from what engine returns.\n\nARGUMENT VALIDATION api PERFORMS BEFORE DELEGATING. `sql` must be a str, else QueryError. `tables` must be a dict whose keys are str and whose values are lists of dicts with str keys, else QueryError. api does NOT copy, normalize, sort, or mutate `tables`; it is passed through to the engine as-is, and api never mutates the caller's rows. Column order for a table is the key order of its first row, i.e. the insertion order of the dicts the caller handed in, so api must not rebuild those dicts.\n\nRETURN-VALUE OWNERSHIP. The list and the row dicts returned by api are freshly built by the engine and are safe for the caller to mutate; they are never aliases of the input rows.\n\nERROR CONTRACT (the invariant every dependent module can rely on). The only exception type that can leave `query` is QueryError, the single error class defined in `minidb/errors.py` and re-exported as `minidb.QueryError`. Dependent modules are therefore free to raise QueryError with a human-readable message at the point of detection and let it propagate: api passes QueryError through unchanged, preserving its message, and does not wrap, rename, or reword it. Dependent modules should still prefer raising QueryError themselves rather than relying on api's catch-all, because the catch-all produces a generic message that loses the specific cause. The catch-all is only a last-resort safety net for unexpected internal bugs; it attaches the original exception as the QueryError's `__cause__` so debugging information is not lost. BaseException subclasses that are not Exception (KeyboardInterrupt, SystemExit) are not caught and propagate normally.\n\nSTATELESSNESS AND REENTRANCY. `query` is a pure function with no module-level mutable state, no caching, no global configuration, and no logging side effects. Repeated calls with equal inputs return equal results, and concurrent calls from multiple threads are safe provided the caller does not mutate `tables` during the call.\n\nIMPORTS AND EXTENSIBILITY. Per the module layout, api may import errors, tokens, nodes, parser, functions, planner and engine, and it imports them at module top level (no inline imports). There is no configuration object, no options parameter, and no second or alternate entry point. If a dependent module needs something more, it must expose that from its own module rather than expecting api to grow another export."
}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
### engine (minidb/engine.py)
{
  "exports": [
    "Row: TypeAlias = dict[str, Any] - one input or output row; keys are column/output names, values are Python values, SQL NULL is None.",
    "Tables: TypeAlias = dict[str, list[Row]] - the caller's table store, exactly the `tables` argument of the public `query()`; raises nothing (type alias only).",
    "def execute(plan: Any, tables: Tables) -> list[Row] - evaluates one validated query plan (the object returned by `planner`'s published plan-producing entry point) against `tables` and returns the result rows as a new list of new dicts, in result order, with keys set by the spec's Output naming rules. Performs the full evaluation pipeline: FROM/INNER JOIN row combination, WHERE filtering, GROUP BY grouping, aggregate computation, HAVING filtering, select-list projection, DISTINCT, ORDER BY, then OFFSET and LIMIT. Raises minidb.errors.QueryError for every failure, including unknown table name in `tables`, unknown or ambiguous column reference surviving into evaluation, mixed-type comparison in ORDER BY or in a comparison operator, non-numeric operand to arithmetic, bad argument type to a scalar or aggregate function, and negative LIMIT/OFFSET. Never raises KeyError, IndexError, AttributeError, TypeError, ValueError, ZeroDivisionError or StopIteration out of `execute`."
  ],
  "module": "engine",
  "notes": "Contract for dependents (`api` is the only one):\n1. `engine` does no lexing, parsing or validation. `api` must call `tokens` -> `parser` -> `planner` first and hand the resulting validated plan to `execute(plan, tables)` unchanged. `execute` treats `plan` as opaque and reads only what `planner` publishes on it; it deliberately does not re-specify planner's plan type, so a change in planner's plan shape needs no change to this signature.\n2. `execute` does not mutate `tables`, the row dicts inside it, or `plan`. Every returned row is a freshly built dict, so callers may mutate results freely. Values are returned by reference (no deep copy).\n3. Row shape: a table's column order is the key order of its first row (as the spec states); a later row missing that key is evaluated as NULL rather than an error, and extra keys in later rows are ignored for `*` expansion. An empty table has no columns.\n4. NULL is Python `None`. Three-valued logic is applied in WHERE/HAVING/ON: a row is kept only when the condition evaluates true, never when it is unknown. Arithmetic with NULL yields NULL; division (`/`) by zero yields NULL and does not raise.\n5. Aggregates: `COUNT(*)` counts rows, `COUNT(x)` counts non-NULL values and returns 0 over no input; SUM/AVG/MIN/MAX ignore NULLs and return NULL when there is no non-NULL input; AVG returns a float. An aggregate select list with no GROUP BY always yields exactly one row, even for zero input rows. With GROUP BY, one row per distinct group key in first-appearance order of the groups.\n6. ORDER BY is stable, NULL sorts before non-NULL for ASC and after for DESC, and an output alias may be used as a sort key. DISTINCT is applied to whole output rows after projection and before ORDER BY/OFFSET/LIMIT; unhashable values are compared structurally rather than raising.\n7. `engine` delegates value comparison, LIKE pattern matching, and scalar/aggregate function behaviour to `functions`, and name resolution/aggregate classification to `planner`; it re-raises their QueryErrors unchanged and adds no new error type. `engine` defines no exception class of its own.\n8. `execute` is pure and stateless: no module-level mutable state, no caching, so it is safe to call concurrently and repeatedly for the same plan."
}

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

### parser (minidb/parser.py)
{
  "exports": [
    "def parse(sql: str) -> Select - Tokenizes `sql` (via `minidb.tokens`) and parses the complete statement into the syntax tree defined by `minidb.nodes`, returning the root `Select` node. Accepts exactly the SQL surface of the specification (SELECT [DISTINCT] select_list FROM table [alias] ([INNER] JOIN table [alias] ON condition)* [WHERE] [GROUP BY] [HAVING] [ORDER BY] [LIMIT] [OFFSET]). Raises `minidb.errors.QueryError` for any lexical or grammatical problem: empty input, unknown/misplaced keyword, unterminated string literal, unbalanced parentheses, unknown function name, wrong arity for a known function, missing FROM, missing ON for a JOIN, a non-integer or negative LIMIT/OFFSET, or any trailing text after the end of the statement. It never propagates `KeyError`, `IndexError`, `AttributeError`, or `TypeError`."
  ],
  "module": "parser",
  "notes": "Scope: `parse` is purely syntactic. It performs no name resolution, no ambiguity detection, no aggregate/grouping classification, and no type checking; those are the planner's and engine's responsibility. It does not touch table data and takes no `tables` argument, so it cannot detect unknown tables or columns.\n\nReturn value: the root node of the tree published by `minidb.nodes` (the `Select` node type). The tree is freshly constructed on every call, contains no references to parser state, and `parse` has no global or cached state, so it is reentrant. Dependents should treat the tree as read-only unless `minidb.nodes` documents otherwise.\n\nErrors: `minidb.errors.QueryError` is the only exception type raised. Messages are human-readable and unspecified in content; do not match on them.\n\nLexical decisions already applied, so downstream modules receive normalized data:\n- Keywords and function names are recognized case-insensitively; identifiers (table, alias, column names) are preserved verbatim, case-sensitive.\n- String literals are unescaped: `''` inside a single-quoted literal becomes one `'`. A literal's value is a Python `str`.\n- Numeric literals become Python `int` when they have no fractional part or exponent, otherwise `float`.\n- `NULL` is parsed as a literal whose value is Python `None`.\n- Comparison operators `!=` and `<>` are both accepted and are represented identically (as `<>`-equivalent inequality); `=` is equality.\n- `IS NULL` / `IS NOT NULL`, `IN (...)`, `LIKE 'pat'`, `AND`, `OR`, `NOT` are parsed as condition constructs of the node tree, not as generic binary operators over strings.\n\nPrecedence implemented (loosest to tightest): OR, AND, NOT, comparison/IS/IN/LIKE, `+ -`, `* /`, unary `-`/`+`, then primary (literal, column reference, function call, parenthesized expression). Parentheses override this.\n\nOutput naming: the parser is the only module that sees source text, so it computes and attaches the output name of every select item per the specification -- an explicit `AS alias` or bare alias wins; a bare column reference `col` or `t.col` is named `col`; any other unaliased expression is named by its source text with all whitespace removed (e.g. `SUM(qty)`, `price*2`). Dependents must use the name carried on the select item rather than re-deriving it. `*` and `table.*` are represented as star select items (no name); their expansion is the engine's job.\n\nORDER BY items carry their expression plus an explicit ascending flag, defaulting to ascending when neither ASC nor DESC is written. An ORDER BY expression that is a bare name may refer to an output alias; the parser emits it as an ordinary column reference and leaves alias-versus-column resolution to the planner.\n\nAbsent optional clauses are represented as the empty/None form of the corresponding node field rather than being omitted, so a `Select` always exposes every clause slot. `LIMIT`/`OFFSET`, when present, are non-negative Python ints."
}

### planner (minidb/planner.py)
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