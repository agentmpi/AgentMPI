"""Command-line entry point for tinyq."""

from __future__ import annotations

import argparse
import sys

from tinyq.csvio import dump_csv, load_csv
from tinyq.executor import execute


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinyq",
        description="Run a SQL query over CSV tables.",
    )
    parser.add_argument("query", help="the SQL query to run")
    parser.add_argument(
        "--table",
        action="append",
        default=[],
        metavar="NAME=PATH.csv",
        help="bind a table name to a CSV file; may be repeated",
    )
    return parser


def _load_tables(specs: list[str]) -> dict:
    tables = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path:
            raise ValueError(f"malformed --table argument: {spec}")
        with open(path, encoding="utf-8") as handle:
            tables[name] = load_csv(handle.read())
    return tables


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        tables = _load_tables(args.table)
        print(dump_csv(execute(args.query, tables)))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
