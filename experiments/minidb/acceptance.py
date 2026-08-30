"""Acceptance suite for the `minidb` collaborative-development experiment.

This file is the experiment's *oracle*, and it is written by the harness author
before the run, never by the agents. That ordering is the whole point: it makes
"did the population succeed?" a question with a mechanical answer, so a comparison
between protocol variants measures the protocol rather than a judge's taste.

It is also the experiment's model of *verification*, which the paper argues is the
only real defence against a plausible-but-wrong result. Each case is an independent
check that some agent's confident output actually behaves as specified — the
agent-level analogue of the residual check in algorithm-based fault tolerance.

The suite is deliberately run out of process. Generated code can hang, exhaust
memory, or corrupt interpreter state, and a harness that imported it into itself
would take the whole run down with it. Isolation is not fastidiousness; it is the
reason a failing module costs one test case instead of the experiment.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

T_BASIC = {
    "t": [
        {"a": 1, "b": "x", "c": 10.5},
        {"a": 2, "b": "y", "c": 20.0},
        {"a": 3, "b": "x", "c": None},
        {"a": 4, "b": "z", "c": 5.25},
    ]
}

T_NULLS = {
    "n": [
        {"id": 1, "v": 10, "g": "a"},
        {"id": 2, "v": None, "g": "a"},
        {"id": 3, "v": 30, "g": "b"},
        {"id": 4, "v": None, "g": None},
    ]
}

T_JOIN = {
    "orders": [
        {"oid": 1, "cid": 10, "qty": 3},
        {"oid": 2, "cid": 20, "qty": 5},
        {"oid": 3, "cid": 10, "qty": 7},
        {"oid": 4, "cid": 30, "qty": 1},
    ],
    "customers": [
        {"cid": 10, "name": "ada"},
        {"cid": 20, "name": "bob"},
        {"cid": 40, "name": "cyd"},
    ],
}

T_EMPTY: dict[str, list[dict[str, Any]]] = {"e": []}

#: ``(name, module, sql, tables, expected)``.  ``module`` is the module the case
#: primarily exercises, used to attribute a failure to an owner; a failure may of
#: course be caused elsewhere, which is itself something the experiment measures.
CASES: list[tuple[str, str, str, dict[str, Any], Any]] = [
    # --- tokenizer / parser -------------------------------------------------
    ("select_all", "parser", "SELECT * FROM t", T_BASIC, [
        {"a": 1, "b": "x", "c": 10.5}, {"a": 2, "b": "y", "c": 20.0},
        {"a": 3, "b": "x", "c": None}, {"a": 4, "b": "z", "c": 5.25}]),
    ("select_cols", "parser", "SELECT b, a FROM t", T_BASIC,
     [{"b": "x", "a": 1}, {"b": "y", "a": 2}, {"b": "x", "a": 3}, {"b": "z", "a": 4}]),
    ("keywords_case_insensitive", "tokens", "select A from t where A = 2", T_BASIC, [{"a": 2}]),
    ("string_literal_escape", "tokens", "SELECT 'it''s' AS s FROM t LIMIT 1", T_BASIC, [{"s": "it's"}]),
    ("alias_with_as", "parser", "SELECT a AS num FROM t LIMIT 2", T_BASIC, [{"num": 1}, {"num": 2}]),
    ("alias_without_as", "parser", "SELECT a num FROM t LIMIT 1", T_BASIC, [{"num": 1}]),
    ("table_alias_qualified", "parser", "SELECT x.a FROM t x WHERE x.a = 3", T_BASIC, [{"a": 3}]),
    ("syntax_error", "parser", "SELECT FROM", T_BASIC, "QueryError"),
    ("unknown_table", "planner", "SELECT a FROM nosuch", T_BASIC, "QueryError"),
    ("unknown_column", "planner", "SELECT zzz FROM t", T_BASIC, "QueryError"),

    # --- expressions --------------------------------------------------------
    ("arithmetic", "engine", "SELECT a * 2 FROM t LIMIT 2", T_BASIC, [{"a*2": 2}, {"a*2": 4}]),
    ("arithmetic_precedence", "engine", "SELECT 1 + 2 * 3 AS r FROM t LIMIT 1", T_BASIC, [{"r": 7}]),
    ("parens", "engine", "SELECT (1 + 2) * 3 AS r FROM t LIMIT 1", T_BASIC, [{"r": 9}]),
    ("div_by_zero_is_null", "engine", "SELECT a / 0 AS r FROM t LIMIT 1", T_BASIC, [{"r": None}]),
    ("scalar_upper", "functions", "SELECT UPPER(b) AS u FROM t LIMIT 1", T_BASIC, [{"u": "X"}]),
    ("scalar_length", "functions", "SELECT LENGTH(b) AS n FROM t LIMIT 1", T_BASIC, [{"n": 1}]),
    ("scalar_abs", "functions", "SELECT ABS(0 - a) AS r FROM t LIMIT 1", T_BASIC, [{"r": 1}]),
    ("coalesce", "functions", "SELECT COALESCE(c, 0) AS r FROM t WHERE a = 3", T_BASIC, [{"r": 0}]),
    ("null_arithmetic", "engine", "SELECT c + 1 AS r FROM t WHERE a = 3", T_BASIC, [{"r": None}]),
    ("unnamed_expr_naming", "planner", "SELECT a  +  1 FROM t LIMIT 1", T_BASIC, [{"a+1": 2}]),

    # --- where / null logic -------------------------------------------------
    ("where_and", "engine", "SELECT a FROM t WHERE a > 1 AND b = 'x'", T_BASIC, [{"a": 3}]),
    ("where_or", "engine", "SELECT a FROM t WHERE a = 1 OR a = 4", T_BASIC, [{"a": 1}, {"a": 4}]),
    ("where_not", "engine", "SELECT a FROM t WHERE NOT b = 'x'", T_BASIC, [{"a": 2}, {"a": 4}]),
    ("where_ne", "engine", "SELECT a FROM t WHERE b <> 'x'", T_BASIC, [{"a": 2}, {"a": 4}]),
    ("is_null", "engine", "SELECT a FROM t WHERE c IS NULL", T_BASIC, [{"a": 3}]),
    ("is_not_null", "engine", "SELECT a FROM t WHERE c IS NOT NULL", T_BASIC, [{"a": 1}, {"a": 2}, {"a": 4}]),
    ("null_comparison_excluded", "engine", "SELECT a FROM t WHERE c > 1000", T_BASIC, []),
    ("in_list", "engine", "SELECT a FROM t WHERE a IN (2, 4)", T_BASIC, [{"a": 2}, {"a": 4}]),
    ("like_percent", "functions", "SELECT id FROM n WHERE g LIKE 'a%'", T_NULLS, [{"id": 1}, {"id": 2}]),
    ("like_underscore", "functions", "SELECT b FROM t WHERE b LIKE '_' LIMIT 1", T_BASIC, [{"b": "x"}]),
    ("null_and_false", "engine", "SELECT id FROM n WHERE v > 100 AND g = 'a'", T_NULLS, []),
    ("null_or_true", "engine", "SELECT id FROM n WHERE v > 100 OR g = 'b'", T_NULLS, [{"id": 3}]),

    # --- order / limit / distinct ------------------------------------------
    ("order_asc", "engine", "SELECT a FROM t ORDER BY b ASC, a ASC", T_BASIC,
     [{"a": 1}, {"a": 3}, {"a": 2}, {"a": 4}]),
    ("order_desc", "engine", "SELECT a FROM t ORDER BY a DESC", T_BASIC,
     [{"a": 4}, {"a": 3}, {"a": 2}, {"a": 1}]),
    ("order_nulls_first_asc", "engine", "SELECT a FROM t ORDER BY c ASC", T_BASIC,
     [{"a": 3}, {"a": 4}, {"a": 1}, {"a": 2}]),
    ("order_nulls_last_desc", "engine", "SELECT a FROM t ORDER BY c DESC", T_BASIC,
     [{"a": 2}, {"a": 1}, {"a": 4}, {"a": 3}]),
    ("order_by_alias", "engine", "SELECT a AS z FROM t ORDER BY z DESC LIMIT 2", T_BASIC,
     [{"z": 4}, {"z": 3}]),
    ("limit_offset", "engine", "SELECT a FROM t ORDER BY a LIMIT 2 OFFSET 1", T_BASIC, [{"a": 2}, {"a": 3}]),
    ("negative_limit", "planner", "SELECT a FROM t LIMIT -1", T_BASIC, "QueryError"),
    ("distinct", "engine", "SELECT DISTINCT b FROM t ORDER BY b", T_BASIC,
     [{"b": "x"}, {"b": "y"}, {"b": "z"}]),

    # --- aggregates ---------------------------------------------------------
    ("count_star", "engine", "SELECT COUNT(*) AS n FROM t", T_BASIC, [{"n": 4}]),
    ("count_col_skips_null", "engine", "SELECT COUNT(c) AS n FROM t", T_BASIC, [{"n": 3}]),
    ("sum_avg", "engine", "SELECT SUM(a) AS s, AVG(a) AS m FROM t", T_BASIC, [{"s": 10, "m": 2.5}]),
    ("min_max_skip_null", "engine", "SELECT MIN(c) AS lo, MAX(c) AS hi FROM t", T_BASIC,
     [{"lo": 5.25, "hi": 20.0}]),
    ("agg_all_null", "engine", "SELECT SUM(v) AS s, COUNT(v) AS n FROM n WHERE v IS NULL", T_NULLS,
     [{"s": None, "n": 0}]),
    ("agg_over_empty", "engine", "SELECT COUNT(*) AS n FROM e", T_EMPTY, [{"n": 0}]),
    ("group_by", "engine", "SELECT b, COUNT(*) AS n FROM t GROUP BY b ORDER BY b", T_BASIC,
     [{"b": "x", "n": 2}, {"b": "y", "n": 1}, {"b": "z", "n": 1}]),
    ("group_by_sum", "engine", "SELECT g, SUM(v) AS s FROM n GROUP BY g ORDER BY g", T_NULLS,
     [{"g": None, "s": None}, {"g": "a", "s": 10}, {"g": "b", "s": 30}]),
    ("having", "engine", "SELECT b, COUNT(*) AS n FROM t GROUP BY b HAVING COUNT(*) > 1", T_BASIC,
     [{"b": "x", "n": 2}]),
    ("nonaggregate_not_grouped", "planner", "SELECT a, COUNT(*) FROM t GROUP BY b", T_BASIC, "QueryError"),
    ("unnamed_agg_naming", "planner", "SELECT SUM(a) FROM t", T_BASIC, [{"SUM(a)": 10}]),

    # --- joins --------------------------------------------------------------
    ("inner_join", "engine",
     "SELECT o.oid, c.name FROM orders o JOIN customers c ON o.cid = c.cid ORDER BY o.oid", T_JOIN,
     [{"oid": 1, "name": "ada"}, {"oid": 2, "name": "bob"}, {"oid": 3, "name": "ada"}]),
    ("join_star", "engine",
     "SELECT * FROM orders o JOIN customers c ON o.cid = c.cid ORDER BY o.oid LIMIT 1", T_JOIN,
     [{"oid": 1, "cid": 10, "qty": 3, "name": "ada"}]),
    ("join_group", "engine",
     "SELECT c.name, SUM(o.qty) AS total FROM orders o JOIN customers c ON o.cid = c.cid "
     "GROUP BY c.name ORDER BY total DESC", T_JOIN,
     [{"name": "ada", "total": 10}, {"name": "bob", "total": 5}]),
    ("ambiguous_column", "planner",
     "SELECT cid FROM orders o JOIN customers c ON o.cid = c.cid", T_JOIN, "QueryError"),
    ("unambiguous_unqualified", "planner",
     "SELECT qty FROM orders o JOIN customers c ON o.cid = c.cid ORDER BY qty LIMIT 1", T_JOIN,
     [{"qty": 3}]),

    # --- error hygiene ------------------------------------------------------
    ("order_nulls_then_strings", "engine", "SELECT id FROM n ORDER BY g, id", T_NULLS,
     [{"id": 4}, {"id": 1}, {"id": 2}, {"id": 3}]),
    ("type_error_is_queryerror", "engine", "SELECT a + b AS r FROM t LIMIT 1", T_BASIC, "QueryError"),
    ("empty_table_select", "engine", "SELECT * FROM e", T_EMPTY, []),
]


def _normalise(rows: Any) -> Any:
    """Compare results structurally, ignoring key insertion order within a row."""
    if not isinstance(rows, list):
        return rows
    return [dict(sorted(r.items())) if isinstance(r, dict) else r for r in rows]


def run_all() -> dict[str, Any]:
    """Run every case in this process and return a JSON-serialisable report."""
    try:
        import minidb  # noqa: PLC0415 - the module under test is generated at runtime
    except BaseException:
        return {
            "importable": False,
            "import_error": traceback.format_exc(limit=6),
            "n_total": len(CASES),
            "n_passed": 0,
            "cases": [],
        }

    query: Callable[..., Any] | None = getattr(minidb, "query", None)
    QueryError = getattr(minidb, "QueryError", None)
    if query is None or QueryError is None:
        return {
            "importable": False,
            "import_error": "minidb must export `query` and `QueryError`",
            "n_total": len(CASES),
            "n_passed": 0,
            "cases": [],
        }

    cases: list[dict[str, Any]] = []
    for name, module, sql, tables, expected in CASES:
        row: dict[str, Any] = {"name": name, "module": module, "sql": sql}
        try:
            got = query(sql, {k: [dict(r) for r in v] for k, v in tables.items()})
            if expected == "QueryError":
                row |= {"passed": False, "reason": f"expected QueryError, got {got!r}"[:300]}
            elif _normalise(got) == _normalise(expected):
                row |= {"passed": True}
            else:
                row |= {"passed": False, "reason": f"expected {expected!r}, got {got!r}"[:300]}
        except BaseException as exc:
            if expected == "QueryError" and QueryError is not None and isinstance(exc, QueryError):
                row |= {"passed": True}
            elif isinstance(exc, RecursionError):
                row |= {"passed": False, "reason": "RecursionError"}
            else:
                row |= {
                    "passed": False,
                    "reason": f"{type(exc).__name__}: {exc}"[:300],
                    "traceback_tail": _blame(traceback.format_exc()),
                }
        cases.append(row)

    n_passed = sum(1 for c in cases if c["passed"])
    by_module: dict[str, dict[str, int]] = {}
    for c in cases:
        b = by_module.setdefault(c["module"], {"passed": 0, "failed": 0})
        b["passed" if c["passed"] else "failed"] += 1
    return {
        "importable": True,
        "n_total": len(cases),
        "n_passed": n_passed,
        "pass_rate": round(n_passed / len(cases), 4) if cases else 0.0,
        "by_module": by_module,
        "blame": _blame_counts(cases),
        "cases": cases,
    }


def _blame(tb: str) -> list[str]:
    """Extract the minidb frames from a traceback, innermost last."""
    out = [line.strip() for line in tb.splitlines() if "minidb/" in line and line.strip().startswith("File")]
    return out[-3:]


def _blame_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Attribute each failure to the innermost minidb module in its traceback.

    Declared ownership tells you which module a case was *written* to exercise;
    the traceback tells you which module actually broke. The two differ often, and
    the difference is the interesting measurement: it is how much of a failure is
    caused by a module's own bug versus by a violated interface it depended on.
    """
    counts: dict[str, int] = {}
    for c in cases:
        if c.get("passed"):
            continue
        frames = c.get("traceback_tail") or []
        mod = "unattributed"
        for line in reversed(frames):
            if "minidb/" in line:
                mod = line.split("minidb/", 1)[1].split('"', 1)[0].replace(".py", "")
                break
        counts[mod] = counts.get(mod, 0) + 1
    return counts


if __name__ == "__main__":
    sys.setrecursionlimit(3000)
    print(json.dumps(run_all(), default=str))
