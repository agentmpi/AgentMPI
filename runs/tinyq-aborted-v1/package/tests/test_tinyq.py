"""Integration suite for tinyq.

Written before the run and never shown to the agents as something they may
edit.  It is the experiment's objective function: a module that is elegant
in isolation and incompatible with its neighbours scores zero here, which is
exactly the property we want to measure.

Each test is tagged with the module whose contract it primarily exercises,
so a failure can be attributed to a rank.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CSV = """name,dept,salary,fulltime
ada,eng,120,true
grace,eng,140,true
alan,research,110,false
edsger,research,130,true
barbara,eng,150,true
"""


# ------------------------------------------------------------------ schema
def test_schema_coerce():
    from tinyq.schema import coerce
    assert coerce("12") == 12
    assert coerce("12.5") == 12.5
    assert coerce("TRUE") is True
    assert coerce("false") is False
    assert coerce("ada") == "ada"
    assert coerce("") is None


def test_schema_infer_kind():
    from tinyq.schema import infer_kind
    assert infer_kind([1, 2, None]) == "int"
    assert infer_kind([1, 2.5]) == "float"
    assert infer_kind([True, False]) == "bool"
    assert infer_kind(["a", 1]) == "str"


def test_schema_table_basics():
    from tinyq.schema import Column, Table
    t = Table([Column("a", "int", [1, 2, 3]), Column("b", "str", ["x", "y", "z"])])
    assert t.names() == ["a", "b"]
    assert t.nrows() == 3
    assert t.row(1) == {"a": 2, "b": "y"}
    assert t.select(["b"]).names() == ["b"]
    assert t.take([2, 0]).column("a").values == [3, 1]
    with pytest.raises(KeyError):
        t.column("nope")


# ------------------------------------------------------------------- csvio
def test_csv_roundtrip():
    from tinyq.csvio import dump_csv, load_csv
    t = load_csv(CSV)
    assert t.names() == ["name", "dept", "salary", "fulltime"]
    assert t.nrows() == 5
    assert t.column("salary").values == [120, 140, 110, 130, 150]
    assert t.column("salary").kind == "int"
    assert t.column("fulltime").values[0] is True
    out = dump_csv(t)
    assert out.splitlines()[0] == "name,dept,salary,fulltime"
    assert len(out.splitlines()) == 6
    assert load_csv(out).column("salary").values == [120, 140, 110, 130, 150]


def test_csv_empty():
    from tinyq.csvio import load_csv
    assert load_csv("").nrows() == 0


# ------------------------------------------------------------------- lexer
def test_lexer_kinds():
    from tinyq.lexer import tokenize
    toks = tokenize("SELECT a, COUNT(b) FROM t WHERE a >= 3 AND c = 'x'")
    kinds = [t.kind for t in toks]
    values = [t.value for t in toks]
    assert values[0] == "select" and kinds[0] == "keyword"
    assert "a" in values and "b" in values
    assert 3 in values
    assert "x" in values
    assert any(k == "op" and v == ">=" for k, v in zip(kinds, values))
    assert all(v == v.lower() for k, v in zip(kinds, values) if k == "keyword")


def test_lexer_rejects_garbage():
    from tinyq.lexer import tokenize
    with pytest.raises(ValueError):
        tokenize("select # from t")


# ------------------------------------------------------------------ parser
def test_parser_simple():
    from tinyq.parser import parse
    q = parse("SELECT name, salary FROM people WHERE salary > 115 LIMIT 2")
    assert q.table == "people"
    assert q.columns == [("column", "name"), ("column", "salary")]
    assert q.where == ("cmp", "salary", ">", 115)
    assert q.limit == 2
    assert q.group_by == []


def test_parser_star_and_order():
    from tinyq.parser import parse
    q = parse("SELECT * FROM people ORDER BY salary DESC, name ASC")
    assert q.columns == [("column", "*")]
    assert q.order_by == [("salary", True), ("name", False)]


def test_parser_aggregates_and_grouping():
    from tinyq.parser import parse
    q = parse("SELECT dept, AVG(salary) AS pay FROM people GROUP BY dept")
    assert q.columns[0] == ("column", "dept")
    assert q.columns[1] == ("agg", "avg", "salary", "pay")
    assert q.group_by == ["dept"]

    q2 = parse("SELECT COUNT(name) FROM people")
    assert q2.columns[0] == ("agg", "count", "name", "count(name)")


def test_parser_boolean_precedence():
    from tinyq.parser import parse
    q = parse("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3")
    assert q.where[0] == "or"
    assert q.where[2][0] == "and"

    q2 = parse("SELECT * FROM t WHERE NOT a = 1")
    assert q2.where == ("not", ("cmp", "a", "=", 1))

    q3 = parse("SELECT * FROM t WHERE (a = 1 OR b = 2) AND c = 3")
    assert q3.where[0] == "and"


def test_parser_rejects_malformed():
    from tinyq.parser import parse
    with pytest.raises(ValueError):
        parse("SELECT FROM")


# --------------------------------------------------------------- predicate
def test_predicate_evaluate():
    from tinyq.predicate import evaluate
    row = {"a": 5, "b": "x", "c": None}
    assert evaluate(("cmp", "a", ">", 3), row) is True
    assert evaluate(("cmp", "a", "<=", 4), row) is False
    assert evaluate(("cmp", "b", "=", "x"), row) is True
    assert evaluate(("and", ("cmp", "a", ">", 3), ("cmp", "b", "=", "x")), row) is True
    assert evaluate(("or", ("cmp", "a", "<", 3), ("cmp", "b", "=", "x")), row) is True
    assert evaluate(("not", ("cmp", "a", ">", 3)), row) is False
    assert evaluate(None, row) is True
    assert evaluate(("cmp", "c", "=", 1), row) is False
    assert evaluate(("cmp", "c", "!=", 1), row) is True
    with pytest.raises(KeyError):
        evaluate(("cmp", "zz", "=", 1), row)


def test_predicate_filter_rows():
    from tinyq.csvio import load_csv
    from tinyq.predicate import filter_rows
    t = load_csv(CSV)
    assert filter_rows(("cmp", "dept", "=", "eng"), t) == [0, 1, 4]
    assert filter_rows(None, t) == [0, 1, 2, 3, 4]


# --------------------------------------------------------------- aggregate
def test_aggregate_apply():
    from tinyq.aggregate import apply
    assert apply("count", [1, None, 3]) == 2
    assert apply("sum", [1, 2, None]) == 3
    assert apply("sum", []) == 0
    assert apply("avg", [1, 2]) == 1.5
    assert apply("avg", []) is None
    assert apply("min", [3, 1, None]) == 1
    assert apply("max", [3, 1, None]) == 3
    with pytest.raises(ValueError):
        apply("median", [1])


def test_aggregate_group_indices():
    from tinyq.aggregate import group_indices
    from tinyq.csvio import load_csv
    t = load_csv(CSV)
    groups = group_indices(t, ["dept"])
    assert list(groups.keys()) == [("eng",), ("research",)]
    assert groups[("eng",)] == [0, 1, 4]
    assert group_indices(t, [])[()] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------- executor
def _tables():
    from tinyq.csvio import load_csv
    return {"people": load_csv(CSV)}


def test_execute_projection_and_filter():
    from tinyq.executor import execute
    out = execute("SELECT name, salary FROM people WHERE dept = 'eng'", _tables())
    assert out.names() == ["name", "salary"]
    assert out.column("name").values == ["ada", "grace", "barbara"]


def test_execute_star_order_limit():
    from tinyq.executor import execute
    out = execute("SELECT * FROM people ORDER BY salary DESC LIMIT 2", _tables())
    assert out.column("name").values == ["barbara", "grace"]
    assert out.names() == ["name", "dept", "salary", "fulltime"]


def test_execute_group_by_aggregate():
    from tinyq.executor import execute
    out = execute("SELECT dept, AVG(salary) AS pay, COUNT(name) AS n "
                  "FROM people GROUP BY dept ORDER BY dept ASC", _tables())
    assert out.names() == ["dept", "pay", "n"]
    assert out.column("dept").values == ["eng", "research"]
    assert out.column("pay").values[0] == pytest.approx(136.6667, abs=1e-3)
    assert out.column("n").values == [3, 2]


def test_execute_bare_aggregate():
    from tinyq.executor import execute
    out = execute("SELECT MAX(salary) FROM people", _tables())
    assert out.nrows() == 1
    assert out.column("max(salary)").values == [150]


def test_execute_boolean_where():
    from tinyq.executor import execute
    out = execute("SELECT name FROM people WHERE salary > 115 AND dept = 'eng'",
                  _tables())
    assert out.column("name").values == ["ada", "grace", "barbara"]


def test_execute_errors():
    from tinyq.executor import execute
    with pytest.raises(KeyError):
        execute("SELECT * FROM missing", _tables())
    with pytest.raises(ValueError):
        execute("SELECT * FROM people GROUP BY dept", _tables())
    with pytest.raises(ValueError):
        execute("SELECT name FROM people GROUP BY dept", _tables())
    with pytest.raises(ValueError):
        execute("SELECT * FROM people LIMIT -1", _tables())


# --------------------------------------------------------------------- cli
def test_cli_end_to_end(tmp_path):
    csv_path = tmp_path / "people.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "tinyq",
         "SELECT dept, COUNT(name) AS n FROM people GROUP BY dept ORDER BY dept ASC",
         "--table", f"people={csv_path}"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "dept,n"
    assert lines[1] == "eng,3"
    assert lines[2] == "research,2"


def test_cli_reports_errors(tmp_path):
    csv_path = tmp_path / "people.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "tinyq", "SELECT * FROM nope",
         "--table", f"people={csv_path}"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr
