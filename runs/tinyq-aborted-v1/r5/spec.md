# tinyq: a minimal columnar query engine

You are building **tinyq**, a small SQL-subset query engine over in-memory
columnar tables, in Python 3.10+ with no third-party dependencies.

The package lives at `runs/tinyq/package/tinyq/`. Each module is owned by exactly
one rank. **You may only create or edit the file you own.** An integration
test suite you did not write, and cannot edit, decides whether the system
works.

## The interfaces are fixed. Do not change them.

These signatures are the contract between modules. Everything below is
normative: your module must expose exactly these names with exactly these
behaviours, and you must call other modules exactly as described here.

### `tinyq/schema.py` — owner: rank 1

```python
class Column:
    def __init__(self, name: str, kind: str, values: list): ...
    name: str          # column name
    kind: str          # one of "int", "float", "str", "bool"
    values: list       # the column's values, one per row

class Table:
    def __init__(self, columns: list[Column]): ...
    columns: list[Column]
    def column(self, name: str) -> Column: ...      # KeyError if absent
    def names(self) -> list[str]: ...
    def nrows(self) -> int: ...
    def row(self, i: int) -> dict: ...              # {name: value}
    def select(self, names: list[str]) -> "Table": ...   # projection
    def take(self, indices: list[int]) -> "Table": ...   # row subset, order preserved

def coerce(text: str) -> object:
    """Parse one CSV cell: int if it looks like an int, else float if it
    looks like a float, else the literal strings 'true'/'false' (any case)
    become bool, else the string unchanged. Empty string becomes None."""

def infer_kind(values: list) -> str:
    """Return 'int', 'float', 'bool' or 'str' for a list of coerced values,
    ignoring None. Mixed int and float is 'float'. Anything else is 'str'."""
```

### `tinyq/csvio.py` — owner: rank 2

```python
from tinyq.schema import Column, Table, coerce, infer_kind

def load_csv(text: str) -> Table:
    """Parse CSV text with a header row into a Table. Use the stdlib `csv`
    module. Cells are converted with schema.coerce; each column's kind comes
    from schema.infer_kind. Empty input (no header) yields Table([])."""

def dump_csv(table: Table) -> str:
    """Serialise a Table back to CSV text with a header row, using '\\n' as
    the line terminator and no trailing blank line. None becomes an empty
    field. Booleans become 'true'/'false'."""
```

### `tinyq/lexer.py` — owner: rank 3

```python
class Token:
    def __init__(self, kind: str, value): ...
    kind: str    # "ident" | "number" | "string" | "op" | "punct" | "keyword"
    value: object

KEYWORDS = {"select", "from", "where", "group", "by", "order",
            "asc", "desc", "limit", "and", "or", "not", "as"}

def tokenize(sql: str) -> list[Token]:
    """Tokenise a query. Case-insensitive keywords are emitted with kind
    'keyword' and a lower-cased string value. Identifiers keep their case and
    have kind 'ident'. Numbers become int or float with kind 'number'.
    Single-quoted strings become kind 'string' with the quotes removed.
    Operators are '=', '!=', '<', '<=', '>', '>=' with kind 'op'.
    Punctuation is ',', '(', ')', '*' with kind 'punct'.
    Raise ValueError('unexpected character: X') on anything else."""
```

### `tinyq/parser.py` — owner: rank 4

```python
from tinyq.lexer import tokenize, Token

class Query:
    def __init__(self, columns, table, where, group_by, order_by, limit): ...
    columns: list      # list of ("column", name) or ("agg", func, argname, alias)
    table: str
    where: object      # a predicate tree, or None
    group_by: list     # list of column names
    order_by: list     # list of (name, descending: bool)
    limit: int | None

def parse(sql: str) -> Query:
    """Parse: SELECT <cols> FROM <table> [WHERE <expr>] [GROUP BY <cols>]
    [ORDER BY <col> [ASC|DESC], ...] [LIMIT <n>]

    <cols> is '*' (which becomes [("column", "*")]) or a comma-separated list
    of either a bare column name -> ("column", name), or an aggregate call
    FUNC(arg) [AS alias] -> ("agg", func_lowercase, arg, alias_or_default)
    where the default alias is f"{func_lowercase}({arg})".

    <expr> is a boolean expression over comparisons, supporting AND, OR, NOT
    and parentheses. Produce a tree of tuples:
      ("cmp", column_name, op, literal_value)
      ("and", left, right) / ("or", left, right) / ("not", child)
    Precedence, tightest first: NOT, AND, OR.

    Raise ValueError with a helpful message on malformed input."""
```

### `tinyq/predicate.py` — owner: rank 5

```python
def evaluate(node, row: dict) -> bool:
    """Evaluate a predicate tree from parser.parse against one row dict.
    Node shapes are ("cmp", name, op, literal), ("and", l, r), ("or", l, r),
    ("not", child). A None node evaluates to True. Comparing against None
    yields False for every operator except '!=' which yields True.
    Raise KeyError(name) if the column is not in the row."""

def filter_rows(node, table) -> list[int]:
    """Return the indices of the rows of `table` satisfying `node`, in order.
    `table` is a tinyq.schema.Table; use table.row(i) and table.nrows()."""
```

### `tinyq/aggregate.py` — owner: rank 6

```python
FUNCTIONS = ("count", "sum", "avg", "min", "max")

def apply(func: str, values: list):
    """Apply an aggregate to a list of values.
    count: number of non-None values; 'count' over ['*'] style input counts
      every element including None.
    sum/avg: over non-None numeric values; sum of nothing is 0, avg of
      nothing is None. avg returns a float.
    min/max: over non-None values; None if there are none.
    Raise ValueError(f'unknown aggregate: {func}') otherwise."""

def group_indices(table, names: list[str]) -> dict:
    """Return {group_key_tuple: [row indices]} preserving first-seen group
    order. `names` may be empty, in which case every row is in one group
    keyed by the empty tuple."""
```

### `tinyq/executor.py` — owner: rank 7

```python
from tinyq.schema import Column, Table
from tinyq.parser import parse
from tinyq.predicate import filter_rows
from tinyq.aggregate import apply as agg_apply, group_indices

def execute(sql: str, tables: dict) -> Table:
    """Run a query. `tables` maps table name -> tinyq.schema.Table.

    Order of operations: FROM, WHERE, GROUP BY + aggregates, projection,
    ORDER BY, LIMIT.

    - Unknown table name: raise KeyError(name).
    - SELECT *: all columns, original order. Not allowed with GROUP BY;
      raise ValueError in that case.
    - With GROUP BY: the output has one row per group. Grouping columns keep
      their names; aggregates use their alias. A non-aggregate, non-grouping
      column in the select list is a ValueError.
    - Without GROUP BY but with aggregates: exactly one output row over all
      filtered rows.
    - ORDER BY sorts by the named output columns in the given order, with
      None sorting last in ascending order.
    - LIMIT truncates. A negative limit is a ValueError."""
```

### `tinyq/cli.py` — owner: rank 8

```python
def main(argv: list[str] | None = None) -> int:
    """Entry point: `python -m tinyq <query> --table NAME=PATH.csv [...]`

    Load each --table argument with tinyq.csvio.load_csv, run the query with
    tinyq.executor.execute, print tinyq.csvio.dump_csv of the result to
    stdout, and return 0. On any error, print
    f'error: {exception}' to stderr and return 1.
    Support --help via argparse."""
```

Also create `tinyq/__init__.py` (owner: rank 1) containing only
`__version__ = "0.1.0"`, and `tinyq/__main__.py` (owner: rank 8) containing
`from tinyq.cli import main; raise SystemExit(main())`.

## How you will be judged

An integration suite at `runs/tinyq/package/tests/` imports these modules and
exercises them together. You cannot edit it. Run it with:

```
cd runs/tinyq/package && python -m pytest tests -q
```

Tests will fail until every rank has finished, which is expected. What
matters is the state at the end of the run.

## Rules

1. Only create or edit the file you own.
2. Never change an interface. If you think one is wrong, implement it as
   written anyway; a module that is individually better and collectively
   incompatible is worthless.
3. No third-party dependencies. Standard library only.
4. Handle the error cases named above; the suite checks them.
